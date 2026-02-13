# GarminBot v3 - Plano de Tracking Nutricional

## Objetivo

Adicionar registo de alimentação ao GarminBot para que o utilizador possa registar o que come via Telegram (texto livre em Português ou foto de código de barras), e obter resumos diários/semanais com calorias, macronutrientes e défice calórico face às calorias gastas registadas pelo Garmin.

## Decisões Arquiteturais

| Decisão | Escolha | Justificação |
|---|---|---|
| Parsing de texto | Claude API (Anthropic SDK) | Interpreta Português naturalmente, entende marcas, quantidades variadas e separadores. Custo negligível (~$0.001/msg com Haiku). |
| Dados nutricionais | OpenFoodFacts API | Gratuito, sem API key, boa cobertura de produtos PT/EU. Suporta barcode + pesquisa por nome. |
| Fallback nutricional | Claude API (estimativa) | Quando OpenFoodFacts não encontra o produto, o Claude estima valores nutricionais por 100g. |
| Leitura de barcode | pyzbar + Pillow | Descodifica barcode de fotos enviadas no Telegram. Leve, sem dependências externas pesadas. |
| Confirmação | Sempre, com inline keyboard | Bot mostra o que interpretou + valores nutricionais. Utilizador confirma com botão antes de guardar. |
| API key | Opcional | Bot funciona sem ANTHROPIC_API_KEY; comandos de nutrição ficam desativados com mensagem explicativa. |

---

## Estrutura de Ficheiros

```
src/
├── nutrition/                    # NOVO package
│   ├── __init__.py
│   ├── parser.py                 # Claude API: texto PT → items estruturados
│   ├── openfoodfacts.py          # OpenFoodFacts API client
│   ├── barcode.py                # Descodificação de barcode de fotos
│   └── service.py                # Orquestrador: parse → lookup → fallback → resultado
tests/
├── test_parser.py                # NOVO
├── test_openfoodfacts.py         # NOVO
├── test_barcode.py               # NOVO
└── test_nutrition_service.py     # NOVO
```

---

## Fase 1: Modelo de Dados

### 1.1 Novo modelo `FoodEntry`

**Ficheiro:** `src/database/models.py`

```python
class FoodEntry(Base):
    __tablename__ = "food_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)           # dia do registo
    name = Column(String(200), nullable=False)                 # "pudim continente proteína"
    quantity = Column(Float, nullable=False, default=1.0)      # 1, 2, 150
    unit = Column(String(20), nullable=False, default="un")    # "un", "g", "ml"
    calories = Column(Float, nullable=True)                    # kcal totais para a quantidade
    protein_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    carbs_g = Column(Float, nullable=True)
    fiber_g = Column(Float, nullable=True)
    source = Column(String(30), nullable=False, default="openfoodfacts")  # "openfoodfacts" | "claude_estimate" | "barcode"
    barcode = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
```

**Decisão:** Sem tabela de agregação diária. Totais calculados on-the-fly via `SUM()` — simples, sem risco de dessincronização, e o volume de dados é baixo (< 20 entradas/dia).

### 1.2 Migration

**Ficheiro:** `src/database/repository.py` — adicionar a `_run_migrations()`:

```python
if "food_entries" not in inspector.get_table_names():
    FoodEntry.__table__.create(self._engine)
```

Segue o padrão existente de migrations idempotentes no startup.

### 1.3 Novos métodos no Repository

**Ficheiro:** `src/database/repository.py`

```python
def save_food_entries(self, day: date, entries: list[dict]) -> list[int]:
    """Guardar múltiplas food entries de uma vez. Retorna lista de IDs."""

def get_food_entries(self, day: date) -> list[FoodEntry]:
    """Todas as entradas de um dia, ordenadas por created_at."""

def delete_last_food_entry(self, day: date) -> bool:
    """Apagar a entrada mais recente do dia. Retorna True se apagou."""

def get_daily_nutrition(self, day: date) -> dict:
    """Totais do dia via SUM().
    Retorna: {calories, protein_g, fat_g, carbs_g, fiber_g, entry_count}
    Retorna zeros se não houver dados."""

def get_weekly_nutrition(self, end_date: date) -> dict:
    """Médias diárias de nutrição nos últimos 7 dias.
    Retorna: {avg_calories, avg_protein, avg_fat, avg_carbs, avg_fiber, days_with_data}"""
```

**Testes:** Adicionar a `tests/test_database.py` — save, get, delete, totais diários, totais semanais, dia vazio retorna zeros.

---

## Fase 2: Módulo de Parsing (Claude API)

### 2.1 `src/nutrition/parser.py`

**Responsabilidade:** Receber texto livre em Português e retornar lista de items estruturados.

```python
@dataclass
class ParsedFoodItem:
    name: str         # "pudim continente proteína de chocolate"
    quantity: float   # 1.0
    unit: str         # "un" | "g" | "ml"

def parse_food_text(text: str, api_key: str) -> list[ParsedFoodItem]:
    """Usa Claude API para extrair items alimentares de texto livre PT."""
```

**Prompt do Claude:**

```
Tu és um parser de alimentos. Recebe texto em Português que descreve o que alguém comeu.
Extrai cada alimento individual com quantidade e unidade.

Regras:
- "e" separa alimentos diferentes
- "+" faz parte do nome do mesmo produto (ex: "+proteína" é parte do produto)
- Se não há quantidade explícita, assume 1 unidade
- Se há peso (ex: "150g"), usa unit="g"
- Se há volume (ex: "200ml"), usa unit="ml"
- Caso contrário, usa unit="un"
- Normaliza nomes: remove "de", "um/uma" desnecessários, mantém marca e variante

Responde APENAS com JSON válido, sem markdown:
[{"name": "...", "quantity": N, "unit": "..."}]
```

**Modelo:** `claude-haiku-4-5-20251001` — mais barato, suficiente para extração estruturada.

**Config:** `max_tokens=500`, timeout 15s.

**Testes (`tests/test_parser.py`):**
- Mock `anthropic.Anthropic().messages.create()`
- "1 pudim continente +proteína de chocolate e 2 mini babybel light" → 2 items corretos
- "150g de arroz cozido" → quantity=150, unit="g"
- "uma maçã" → quantity=1, unit="un", name="maçã"
- Input vazio → lista vazia
- Resposta inválida do Claude → exceção tratada

---

## Fase 3: Módulo OpenFoodFacts

### 3.1 `src/nutrition/openfoodfacts.py`

**Responsabilidade:** Lookup de dados nutricionais por barcode ou pesquisa textual.

```python
@dataclass
class NutritionData:
    product_name: str                # nome oficial do produto
    calories_per_100g: float | None
    protein_per_100g: float | None
    fat_per_100g: float | None
    carbs_per_100g: float | None
    fiber_per_100g: float | None
    serving_size_g: float | None     # tamanho da porção padrão (se disponível)

def lookup_barcode(barcode: str) -> NutritionData | None:
    """GET https://world.openfoodfacts.org/api/v2/product/{barcode}.json
    Retorna dados nutricionais ou None se não encontrado."""

def search_product(query: str) -> NutritionData | None:
    """GET https://world.openfoodfacts.org/cgi/search.pl?search_terms={query}&json=1&page_size=1
    Retorna primeiro resultado ou None."""
```

**Detalhes:**
- Timeout: 10 segundos
- User-Agent: `"GarminBot/1.0"` (requerido pela API OFF)
- Para pesquisa, adicionar `&countries_tags=pt` para priorizar produtos portugueses
- Campos relevantes no JSON: `product.nutriments.energy-kcal_100g`, `proteins_100g`, `fat_100g`, `carbohydrates_100g`, `fiber_100g`, `serving_quantity`

**Cálculo de nutrientes por quantidade:**
- Se `unit="g"`: `(value_per_100g * quantity) / 100`
- Se `unit="un"`: usa `serving_size_g` se disponível, senão assume 100g e marca como estimativa
- Se `unit="ml"`: trata como gramas (aproximação razoável para a maioria dos líquidos alimentares)

**Testes (`tests/test_openfoodfacts.py`):**
- Mock `requests.get`
- Barcode encontrado → NutritionData correto
- Barcode não encontrado → None
- Pesquisa com resultado → primeiro match
- Pesquisa sem resultados → None
- Timeout da rede → None (não crashar)

---

## Fase 4: Módulo de Barcode

### 4.1 `src/nutrition/barcode.py`

**Responsabilidade:** Descodificar código de barras de uma imagem.

```python
def decode_barcode(image_bytes: bytes) -> str | None:
    """Descodifica o primeiro barcode encontrado na imagem.
    Retorna string do barcode (EAN-13) ou None se não encontrado."""
```

**Implementação:**
- `Pillow` para abrir a imagem (`Image.open(BytesIO(image_bytes))`)
- `pyzbar.decode()` para encontrar barcodes
- Retorna `barcodes[0].data.decode("utf-8")` ou `None`

**Nota Windows:** O `pyzbar` no Windows requer a DLL do `zbar`. O package `pyzbar` PyPI geralmente inclui os binários necessários, mas pode ser preciso instalar `vcredist`. Documentar no README.

**Testes (`tests/test_barcode.py`):**
- Imagem com barcode → string correta
- Imagem sem barcode → None
- Imagem corrupta/inválida → None (não crashar)

---

## Fase 5: Serviço Orquestrador

### 5.1 `src/nutrition/service.py`

**Responsabilidade:** Ponto de entrada único para o bot. Coordena parsing, lookup e fallback.

```python
@dataclass
class FoodItemResult:
    name: str
    quantity: float
    unit: str
    calories: float | None
    protein_g: float | None
    fat_g: float | None
    carbs_g: float | None
    fiber_g: float | None
    source: str             # "openfoodfacts" | "claude_estimate" | "barcode"
    barcode: str | None

class NutritionService:
    def __init__(self, anthropic_api_key: str): ...

    def process_text(self, text: str) -> list[FoodItemResult]:
        """Texto → parse (Claude) → lookup cada item (OFF) → fallback (Claude) → resultados."""

    def process_barcode(self, image_bytes: bytes) -> FoodItemResult | None:
        """Foto → decode barcode → lookup OFF → resultado ou None."""

    def _estimate_nutrition(self, food_name: str) -> dict:
        """Fallback: pede ao Claude para estimar valores nutricionais por 100g."""

    def _calculate_nutrients(self, nutrition_per_100g: NutritionData, quantity: float, unit: str) -> dict:
        """Calcula nutrientes totais com base na quantidade e unidade."""
```

**Fluxo de texto (`process_text`):**

```
"1 pudim continente +proteína e 2 babybel light"
        │
        ▼
parser.parse_food_text()
        │
        ▼
[{name: "pudim continente proteína", qty: 1, unit: "un"},
 {name: "babybel light", qty: 2, unit: "un"}]
        │
        ▼  (para cada item)
openfoodfacts.search_product(name)
        │
   ┌────┴────┐
   │ Found   │ Not found
   ▼         ▼
Usar dados  _estimate_nutrition(name)
OFF         via Claude API
   │         │
   ▼         ▼
_calculate_nutrients(data, qty, unit)
        │
        ▼
[FoodItemResult, FoodItemResult]
```

**Fluxo de barcode (`process_barcode`):**

```
foto (bytes)
    │
    ▼
barcode.decode_barcode()
    │
    ├── None → return None
    │
    ▼
openfoodfacts.lookup_barcode(code)
    │
    ├── None → return None
    │
    ▼
FoodItemResult (qty=1, unit="un", source="barcode")
```

**Testes (`tests/test_nutrition_service.py`):**
- Mock parser + openfoodfacts
- Texto com 2 items, ambos encontrados no OFF → resultados corretos
- Texto com item não encontrado → fallback Claude → resultado com source="claude_estimate"
- Barcode válido → resultado correto
- Barcode não descodificado → None
- Barcode descodificado mas produto não encontrado → None
- Cálculo de nutrientes: 150g de algo com 200 kcal/100g → 300 kcal

---

## Fase 6: Comandos Telegram

### 6.1 Comando `/comi <texto>`

**Ficheiro:** `src/telegram/bot.py`

**Handler:** `_cmd_comi(update, context)`

```
Utilizador: /comi 1 pudim continente +proteína de chocolate e 2 mini babybel light
        │
        ▼
Auth check + rate limit
        │
        ▼
NutritionService.process_text(texto)
        │
        ▼
Formatar mensagem de confirmação:

    📝 *Registar refeição:*

    1. Pudim Continente Proteína Chocolate (1 un)
       → 150 kcal | P: 12g | G: 4g | H: 18g | F: 0.5g

    2. Mini Babybel Light (2 un)
       → 84 kcal | P: 10g | G: 4g | H: 0g | F: 0g

    *Total: 234 kcal | P: 22g | G: 8g | H: 18g | F: 0.5g*

    [✅ Confirmar]  [❌ Cancelar]
        │
        ▼
Guardar items em context.user_data["pending_food"]
        │
        ▼
Esperar callback do inline keyboard
```

### 6.2 Foto de Barcode

**Handler:** `MessageHandler(filters.PHOTO, _handle_photo)`

```
Utilizador envia foto de barcode
        │
        ▼
Auth check
        │
        ▼
Download foto (maior resolução disponível)
        │
        ▼
NutritionService.process_barcode(image_bytes)
        │
        ├── None → "❌ Não consegui ler o código de barras. Tenta com melhor iluminação ou usa /comi."
        │
        ▼
Perguntar quantidade:
    "Encontrei: *Mini Babybel Light*
     Quantas unidades comeste?"
        │
        ▼
Utilizador responde com número
        │
        ▼
Mostrar confirmação (mesmo formato do /comi)
        │
        ▼
[✅ Confirmar]  [❌ Cancelar]
```

### 6.3 ConversationHandler

**Padrão:** `python-telegram-bot` `ConversationHandler` para gerir o fluxo multi-step.

```python
AWAITING_CONFIRMATION = 0
AWAITING_BARCODE_QUANTITY = 1

conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler("comi", _cmd_comi),
        MessageHandler(filters.PHOTO, _handle_photo),
    ],
    states={
        AWAITING_CONFIRMATION: [
            CallbackQueryHandler(_confirm_food, pattern="^food_confirm$"),
            CallbackQueryHandler(_cancel_food, pattern="^food_cancel$"),
        ],
        AWAITING_BARCODE_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_barcode_quantity),
        ],
    },
    fallbacks=[CommandHandler("cancelar", _cancel_food)],
    conversation_timeout=300,  # 5 minutos para confirmar
)
```

**Dados pendentes:** `context.user_data["pending_food"] = list[FoodItemResult]`

**Callback confirmar:**
- Salva todas as entries via `repository.save_food_entries(today, items)`
- Responde: "✅ Registado! Total: 234 kcal"
- Limpa `context.user_data["pending_food"]`

**Callback cancelar:**
- Responde: "❌ Registo cancelado."
- Limpa `context.user_data["pending_food"]`

### 6.4 Comando `/nutricao`

**Handler:** `_cmd_nutricao(update, context)`

Mostra resumo do dia atual:

```
🍽 *Nutrição — 13/02/2026*

📋 *Refeições registadas:*
• 08:30 — Pudim Continente Proteína (1 un) — 150 kcal
• 08:30 — Mini Babybel Light (2 un) — 84 kcal
• 12:45 — Arroz cozido (150g) — 195 kcal
• 12:45 — Peito de frango grelhado (200g) — 330 kcal

📊 *Totais do dia:*
• Calorias: 759 kcal
• Proteína: 82g | Gordura: 18g | HC: 65g | Fibra: 3g

⚖️ *Balanço calórico:*
• Gastas (Garmin): 2.150 kcal
• Ingeridas: 759 kcal
• Défice: -1.391 kcal (64.7%)
```

Também aceitar `/dieta` como alias.

### 6.5 Comando `/apagar`

**Handler:** `_cmd_apagar(update, context)`

- Chama `repository.delete_last_food_entry(today)`
- Se apagou: "🗑 Apagada última entrada: *Mini Babybel Light (2 un) — 84 kcal*"
- Se não há entradas: "Não há entradas para apagar hoje."

### 6.6 Registo de comandos

Adicionar a `register_commands()`:
- `("comi", "Registar alimento (ex: /comi 2 ovos e 1 torrada)")`
- `("nutricao", "Resumo nutricional do dia")`
- `("apagar", "Apagar último alimento registado")`

---

## Fase 7: Integração nos Relatórios Existentes

### 7.1 Relatório Diário

**Ficheiro:** `src/telegram/formatters.py` — modificar `format_daily_summary()`

Adicionar parâmetro opcional `nutrition: dict | None`. Quando presente, anexar secção:

```
🍽 *Nutrição*
• Calorias ingeridas: 1.850 kcal
• Proteína: 120g | Gordura: 65g | HC: 210g | Fibra: 25g
• Défice calórico: -450 kcal (19.6%)
```

**Ficheiro:** `src/scheduler/jobs.py` — modificar `make_daily_report_job()`

Após buscar métricas de ontem, buscar também `repository.get_daily_nutrition(yesterday)`. Passar ao formatter.

### 7.2 Relatório Semanal

**Ficheiro:** `src/telegram/formatters.py` — modificar `format_weekly_report()`

Adicionar parâmetro opcional `weekly_nutrition: dict | None`. Quando presente, anexar:

```
🍽 *Nutrição (média diária)*
• Calorias: 1.920 kcal/dia
• Proteína: 115g | Gordura: 70g | HC: 225g | Fibra: 22g
• Défice médio: -380 kcal/dia (16.5%)
```

**Ficheiro:** `src/scheduler/jobs.py` — modificar `make_weekly_report_job()`

Buscar `repository.get_weekly_nutrition(yesterday)`. Passar ao formatter.

### 7.3 Cálculo de Défice Calórico

```python
def calculate_deficit(active_cal: int | None, resting_cal: int | None,
                      eaten_cal: float | None) -> tuple[int | None, float | None]:
    """
    deficit = (active_calories + resting_calories) - calories_eaten

    Retorna: (deficit_kcal, deficit_pct) ou (None, None) se dados insuficientes.
    - Positivo = défice (comeu menos do que gastou)
    - Negativo = excedente (comeu mais do que gastou)
    """
```

**Apresentação:**
- Défice: `"Défice: -450 kcal (19.6%)"` — comeu menos do que gastou
- Excedente: `"Excedente: +200 kcal (8.3%)"` — comeu mais do que gastou
- Sem dados Garmin: `"Balanço: sem dados de atividade"`
- Sem dados nutrição: não mostrar secção

### 7.4 Exportação CSV

**Ficheiro:** `src/telegram/bot.py` — modificar `_cmd_exportar`

Adicionar opção `/exportar nutricao` que exporta a tabela `food_entries` em CSV separado:
- Colunas: `data, nome, quantidade, unidade, calorias, proteina_g, gordura_g, hidratos_g, fibra_g, fonte, barcode`

---

## Fase 8: Configuração

### 8.1 Config

**Ficheiro:** `src/config.py`

```python
# Novo campo (opcional)
anthropic_api_key: str | None = None
```

**Ficheiro:** `.env.example`

```bash
# Nutrição (opcional - desativa /comi e barcode se não definido)
ANTHROPIC_API_KEY=sk-ant-...
```

**Comportamento:** Se `anthropic_api_key` é `None`:
- `/comi` responde: "⚠️ Funcionalidade de nutrição não configurada. Adiciona `ANTHROPIC_API_KEY` ao ficheiro .env."
- Handler de fotos ignora (não intercepta fotos para barcode)
- Relatórios diários/semanais não mostram secção nutrição
- Restante bot funciona normalmente

### 8.2 `allowed_updates`

**Ficheiro:** `src/main.py`

Alterar `app.run_polling(allowed_updates=["message"])` para:
```python
app.run_polling(allowed_updates=["message", "callback_query"])
```

Necessário para receber cliques nos botões inline do teclado de confirmação.

---

## Fase 9: Dependências

### 9.1 Novas dependências

**Ficheiro:** `requirements.txt` — adicionar:

```
anthropic>=0.39.0
pyzbar>=0.1.9
Pillow>=10.0.0
requests>=2.31.0
```

**Notas:**
- `anthropic`: SDK oficial da Anthropic para Claude API
- `pyzbar`: leitor de barcode. No Windows pode precisar de `vcredist` instalado
- `Pillow`: processamento de imagem (abrir foto para pyzbar)
- `requests`: HTTP client para OpenFoodFacts API (lightweight, sem necessidade de async)

---

## Fase 10: Testes

### 10.1 Testes por módulo

| Ficheiro de teste | O que testa | Mocks |
|---|---|---|
| `tests/test_parser.py` | Parsing de texto PT → items estruturados | `anthropic.Anthropic` |
| `tests/test_openfoodfacts.py` | Lookup barcode + pesquisa textual | `requests.get` |
| `tests/test_barcode.py` | Descodificação de barcode de imagem | Imagem de teste real |
| `tests/test_nutrition_service.py` | Orquestração completa (parse → lookup → fallback) | Parser + OFF |
| `tests/test_database.py` (estender) | CRUD food entries, totais diários/semanais | Nenhum (SQLite temp) |
| `tests/test_formatters.py` (estender) | Formatação nutrição, confirmação, défice | Nenhum |

### 10.2 Casos de teste críticos

**Parser:**
- Múltiplos items separados por "e"
- Quantidades em gramas ("150g de arroz")
- Sem quantidade explícita (assume 1 un)
- "+" como parte do nome do produto
- Input vazio ou inválido

**OpenFoodFacts:**
- Barcode válido → dados corretos
- Barcode inexistente → None
- Pesquisa com resultado → primeiro match
- Pesquisa sem resultado → None
- Timeout → None (graceful)

**Service:**
- Texto → OFF encontra tudo → resultados corretos
- Texto → OFF não encontra → fallback Claude → source="claude_estimate"
- Barcode → decode OK → OFF encontra → resultado
- Barcode → decode falha → None
- Cálculo nutrientes: 150g × 200kcal/100g = 300kcal

**Défice calórico:**
- Défice positivo (comeu menos)
- Excedente (comeu mais)
- Dados Garmin em falta → None
- Dados nutrição em falta → None

---

## Resumo de Impacto por Ficheiro

| Ficheiro | Alteração |
|---|---|
| `src/database/models.py` | Novo modelo `FoodEntry` |
| `src/database/repository.py` | 5 novos métodos + migration |
| `src/nutrition/__init__.py` | Novo (vazio) |
| `src/nutrition/parser.py` | Novo — Claude API parsing |
| `src/nutrition/openfoodfacts.py` | Novo — OFF API client |
| `src/nutrition/barcode.py` | Novo — pyzbar decoding |
| `src/nutrition/service.py` | Novo — orquestrador |
| `src/telegram/bot.py` | ConversationHandler, 4 novos handlers, photo handler |
| `src/telegram/formatters.py` | 3 novos formatters + modificar daily/weekly |
| `src/config.py` | `anthropic_api_key` opcional |
| `src/main.py` | Criar NutritionService, `allowed_updates` += `callback_query` |
| `src/scheduler/jobs.py` | Nutrição nos relatórios diário/semanal |
| `requirements.txt` | +4 dependências |
| `.env.example` | `ANTHROPIC_API_KEY` |
| `README.md` | Documentar comandos nutrição + setup |
| `tests/test_parser.py` | Novo |
| `tests/test_openfoodfacts.py` | Novo |
| `tests/test_barcode.py` | Novo |
| `tests/test_nutrition_service.py` | Novo |
| `tests/test_database.py` | Estender |
| `tests/test_formatters.py` | Estender |

## Ordem de Implementação

```
Fase 1  — Modelo de dados + migration + repository methods + testes DB
Fase 2  — Parser (Claude API) + testes
Fase 3  — OpenFoodFacts client + testes
Fase 4  — Barcode decoder + testes
Fase 5  — Service orquestrador + testes
Fase 6  — Formatters nutrição + testes
Fase 7  — Config (ANTHROPIC_API_KEY)
Fase 8  — Handlers Telegram (ConversationHandler, /comi, foto, /nutricao, /apagar)
Fase 9  — Integração nos relatórios diário/semanal
Fase 10 — Main.py wiring + allowed_updates
Fase 11 — requirements.txt, .env.example, README
```

## Estimativa de Complexidade

| Fase | Complexidade | Notas |
|---|---|---|
| 1. Modelo + Repository | Baixa | Segue padrões existentes |
| 2. Parser | Média | Prompt engineering + parsing JSON |
| 3. OpenFoodFacts | Baixa | REST API simples |
| 4. Barcode | Baixa | pyzbar é direto |
| 5. Service | Média | Orquestração + fallback + cálculos |
| 6. Formatters | Baixa | Segue padrões existentes |
| 7. Config | Baixa | 1 campo novo |
| 8. Handlers Telegram | Alta | ConversationHandler + inline keyboard + photo handler |
| 9. Integração relatórios | Média | Modificar jobs + formatters existentes |
| 10. Wiring | Baixa | Ligar tudo no main.py |
| 11. Docs | Baixa | requirements + README |

---

*Plano gerado a 2026-02-13. Baseia-se no estado atual do GarminBot e nas decisões tomadas com o utilizador.*
