# GarminBot v2 - Plano de Melhorias

## Contexto

O GarminBot v1 está funcional com sync diário, relatórios (diário/semanal/mensal), gráficos semanais, insights e backups automáticos. Este plano adiciona funcionalidades para tornar o bot mais útil, robusto e pessoal.

---

## Fase 1: Robustez Operacional

### 1.1 Retry Automático de Sync
**Problema:** Se o sync das 07:00 falhar, o utilizador só descobre quando o relatório das 08:00 diz "dados não encontrados".

**Implementação:**
- Adicionar um segundo job APScheduler `daily_sync_retry` agendado 30min após o sync principal (07:30 por defeito)
- O retry só executa se o último sync do dia falhou (verificar `sync_log` para hoje com status `"error"`)
- Configurável via `.env`: `SYNC_RETRY_DELAY_MINUTES=30`
- Máximo 1 retry automático por dia para evitar spam à API

**Ficheiros afetados:**
- `src/scheduler/jobs.py` — novo `make_sync_retry_job()`
- `src/database/repository.py` — novo `has_successful_sync_today()`
- `src/main.py` — registar o novo job
- `src/config.py` — novo campo opcional `SYNC_RETRY_DELAY_MINUTES`

**Testes:**
- Retry executa quando sync falhou
- Retry não executa quando sync teve sucesso
- Retry não executa mais do que 1x por dia

---

### 1.2 Backfill de Dados em Falta
**Problema:** Se o bot estiver offline vários dias, só sincroniza o dia anterior quando volta. Dias intermédios ficam sem dados.

**Implementação:**
- Novo comando Telegram `/backfill <N>` — sincroniza os últimos N dias (máximo 30)
- Lógica automática no startup: verificar gaps nos últimos 7 dias e preencher automaticamente
- `GarminClient` já suporta fetch por data específica, basta chamar `get_sleep_data(date)` e `get_activity_data(date)` para cada dia
- Rate limiting: 2 segundos entre chamadas à API Garmin para evitar bloqueio

**Ficheiros afetados:**
- `src/garmin/client.py` — novo `get_summary_for_date(date)` (generalizar `get_yesterday_summary`)
- `src/telegram/bot.py` — novo handler `/backfill`
- `src/main.py` — lógica de backfill no startup (após health checks)
- `src/database/repository.py` — novo `get_missing_dates(start, end)`

**Testes:**
- Backfill preenche dias em falta corretamente
- Não re-sincroniza dias que já têm dados
- Respeita limite máximo de 30 dias
- Rate limiting entre chamadas

---

### 1.3 Healthcheck HTTP Endpoint
**Problema:** Sem forma de monitorização externa (UptimeRobot, etc.) saber se o bot está vivo.

**Implementação:**
- Servidor HTTP mínimo numa thread separada (stdlib `http.server` ou `aiohttp`)
- Endpoint `GET /health` retorna JSON `{"status": "ok", "last_sync": "...", "scheduler_running": true, "uptime_seconds": N}`
- Retorna 200 se scheduler ativo e último sync < 48h, senão 503
- Porta configurável via `.env`: `HEALTH_PORT=8080` (desativado se não definido)

**Ficheiros afetados:**
- `src/utils/healthcheck.py` — novo módulo
- `src/main.py` — iniciar servidor se `HEALTH_PORT` configurado
- `src/config.py` — novo campo opcional `HEALTH_PORT`

**Testes:**
- Endpoint retorna 200 quando tudo OK
- Endpoint retorna 503 quando sync desatualizado
- Servidor não inicia se porta não configurada

---

## Fase 2: Novos Comandos Telegram

### 2.1 Comando `/ajuda`
**Problema:** O utilizador não tem como descobrir os comandos disponíveis sem ler o README.

**Implementação:**
- Handler para `/ajuda` e `/help` que lista todos os comandos com descrição curta
- Formato:
  ```
  🤖 Comandos disponíveis:

  /hoje — Resumo de hoje
  /ontem — Resumo de ontem
  /semana — Relatório semanal
  /mes — Relatório mensal
  /sync — Forçar sincronização
  /backfill <N> — Sincronizar últimos N dias
  /historico <data> — Ver dia específico
  /exportar — Exportar dados em CSV
  /objetivo — Ver/definir objetivos
  /status — Estado do bot
  /ajuda — Esta mensagem
  ```
- Registar os comandos no BotFather via `bot.set_my_commands()` no startup

**Ficheiros afetados:**
- `src/telegram/bot.py` — novo handler + `set_my_commands()` no init
- `src/telegram/formatters.py` — novo `format_help_message()`

**Testes:**
- Comando retorna lista completa
- Inclui comandos novos adicionados neste plano

---

### 2.2 Comando `/historico <data>`
**Problema:** Não há forma de consultar um dia específico ou os últimos N dias.

**Implementação:**
- `/historico 2025-02-10` — mostra resumo desse dia (mesmo formato do `/ontem`)
- `/historico 5` ou `/ultimos 5` — mostra resumo dos últimos 5 dias (tabela compacta)
- Validação: data não pode ser futura, máximo 90 dias atrás, N entre 1 e 14

**Ficheiros afetados:**
- `src/telegram/bot.py` — novo handler com parsing de argumentos
- `src/telegram/formatters.py` — novo `format_history_table(metrics_list)` para vista multi-dia

**Testes:**
- Data específica retorna dados corretos
- Últimos N dias retorna lista ordenada
- Data futura retorna erro amigável
- Dia sem dados retorna mensagem adequada

---

### 2.3 Comando `/exportar`
**Problema:** Sem forma de extrair dados para análise externa ou backup pessoal.

**Implementação:**
- `/exportar` — gera CSV com todo o histórico e envia como documento no Telegram
- `/exportar 30` — exporta só os últimos 30 dias
- Colunas: `data, sono_horas, sono_score, sono_qualidade, passos, calorias_ativas, calorias_repouso`
- Usar `csv.writer` com `io.StringIO`, enviar via `bot.send_document()`

**Ficheiros afetados:**
- `src/telegram/bot.py` — novo handler
- `src/database/repository.py` — novo `get_all_metrics()` (ou reutilizar `get_metrics_range`)

**Testes:**
- CSV gerado com headers corretos
- Dados correspondem ao que está na DB
- Ficheiro vazio retorna mensagem em vez de CSV vazio

---

## Fase 3: Dados de Saúde Adicionais

### 3.1 Heart Rate, Stress e Body Battery
**Problema:** O Garmin tem dados valiosos de HR, stress e Body Battery que o bot ignora.

**Implementação:**
- Adicionar colunas ao modelo `DailyMetrics`:
  - `resting_heart_rate` (Integer, nullable) — FC em repouso
  - `avg_stress` (Integer, nullable) — stress médio do dia (0-100)
  - `body_battery_high` (Integer, nullable) — máximo Body Battery
  - `body_battery_low` (Integer, nullable) — mínimo Body Battery
- Buscar via `garminconnect`:
  - `client.get_stats(date)` já retorna `restingHeartRate`
  - `client.get_stress_data(date)` para stress médio
  - `client.get_body_battery(date)` para Body Battery
- Incluir nos formatters (diário e semanal)
- Falha silenciosa: se algum campo não estiver disponível, continua sem ele

**Ficheiros afetados:**
- `src/database/models.py` — novas colunas (migration: `ALTER TABLE ADD COLUMN`)
- `src/garmin/client.py` — novos métodos de fetch + incluir no `DailySummary`
- `src/telegram/formatters.py` — nova secção "❤️ Saúde" nos relatórios
- `src/database/repository.py` — incluir nos cálculos de stats

**Testes:**
- Parsing correto dos novos campos da API
- Formatação com e sem dados (nullable)
- Stats semanais incluem médias dos novos campos
- Migration não quebra DB existente

**Nota:** Requer Alembic ou script de migration manual para DBs existentes. Alternativa simples: `ALTER TABLE daily_metrics ADD COLUMN resting_heart_rate INTEGER;` no `init_database()` com `IF NOT EXISTS` check.

---

## Fase 4: Personalização

### 4.1 Objetivos Configuráveis
**Problema:** Goals de passos (10.000) e sono (7h) estão hardcoded em `insights.py`.

**Implementação:**
- Nova tabela `user_goals`:
  - `id` (PK)
  - `metric` (String) — "steps" | "sleep_hours"
  - `target_value` (Float)
  - `updated_at` (DateTime)
- Comando `/objetivo passos 8000` — define objetivo de passos
- Comando `/objetivo sono 7.5` — define objetivo de sono (em horas)
- Comando `/objetivo` (sem args) — mostra objetivos atuais
- `insights.py` e `charts.py` leem goals da DB em vez de constantes

**Ficheiros afetados:**
- `src/database/models.py` — novo modelo `UserGoal`
- `src/database/repository.py` — `get_goals()`, `set_goal(metric, value)`
- `src/telegram/bot.py` — novo handler `/objetivo`
- `src/utils/insights.py` — receber goals como parâmetro
- `src/utils/charts.py` — linhas de referência dinâmicas

**Testes:**
- Guardar e recuperar goals
- Goals refletidos nos insights
- Goals refletidos nas linhas de referência dos charts
- Valores inválidos rejeitados (passos < 0, sono < 0 ou > 24)

---

### 4.2 Alertas Diários Inteligentes
**Problema:** Os insights só aparecem no relatório semanal. Padrões importantes passam despercebidos durante a semana.

**Implementação:**
- No `send_daily_report_job`, após enviar o resumo diário, verificar:
  - Sono < 6h → "⚠️ Dormiste pouco esta noite. Tenta descansar mais hoje."
  - Streak de passos ≥ 5 dias → "🔥 5 dias seguidos acima do objetivo!"
  - Sono excelente (score ≥ 85) → "🌟 Excelente noite de sono!"
  - Sem atividade (passos < 1000) → "🚶 Dia muito parado ontem. Tenta mexer-te hoje."
- Anexar ao final do relatório diário (não como mensagem separada)
- Configurável: `DAILY_ALERTS=true` no `.env` (ativo por defeito)

**Ficheiros afetados:**
- `src/utils/insights.py` — novo `generate_daily_alerts(metrics, goals)`
- `src/telegram/formatters.py` — secção de alertas no formato diário
- `src/scheduler/jobs.py` — integrar alertas no daily report job
- `src/config.py` — novo campo `DAILY_ALERTS`

**Testes:**
- Alerta de sono curto com < 6h
- Alerta de streak com dados consecutivos
- Sem alertas quando tudo normal
- Alertas desativados quando `DAILY_ALERTS=false`

---

## Fase 5: Visualização Melhorada

### 5.1 Gráfico Mensal
**Problema:** O relatório semanal tem gráfico, mas o mensal é só texto. Tendências de 30 dias são difíceis de interpretar sem visualização.

**Implementação:**
- Novo `generate_monthly_chart(rows)` em `charts.py`
- Gráfico de linha (não barras — 30 barras ficam ilegíveis) com:
  - Painel 1: Passos diários + linha de tendência (média móvel 7 dias)
  - Painel 2: Sono diário + linha de tendência
- Linhas de referência para goals
- Enviar no relatório mensal (`/mes`)

**Ficheiros afetados:**
- `src/utils/charts.py` — novo `generate_monthly_chart()`
- `src/telegram/bot.py` — enviar chart no handler `/mes`

**Testes:**
- Chart gerado com 30 dias de dados
- Chart funciona com dados parciais (< 30 dias)
- Média móvel calculada corretamente

---

### 5.2 Comparação Semana-a-Semana
**Problema:** O relatório semanal não compara com a semana anterior. Não há noção de progresso.

**Implementação:**
- No `get_weekly_stats`, calcular também stats da semana anterior (dias -14 a -8)
- Adicionar deltas no formatter semanal:
  - "Sono médio: 7h 18min (+12min vs semana anterior)"
  - "Passos médios: 11.274 (-820 vs semana anterior)"
- Só mostrar comparação se houver dados da semana anterior

**Ficheiros afetados:**
- `src/database/repository.py` — novo `get_previous_weekly_stats(end_date)` ou alterar `get_weekly_stats` para retornar ambos
- `src/telegram/formatters.py` — deltas no formato semanal

**Testes:**
- Deltas calculados corretamente
- Sem comparação quando não há dados anteriores
- Formatação com deltas positivos e negativos

---

## Fase 6: Mensagens de Erro Acionáveis

### 6.1 Erros Contextuais
**Problema:** `format_error_message` é genérico. O utilizador não sabe o que fazer quando algo falha.

**Implementação:**
- Mapear exceções comuns para mensagens de ajuda:
  - `GarminConnectAuthenticationError` → "Token expirado. Usa /sync para re-autenticar. Se persistir, verifica as credenciais no .env."
  - `ConnectionError` / `Timeout` → "Falha de rede. O bot vai tentar novamente automaticamente."
  - `GarminConnectTooManyRequestsError` → "A API Garmin bloqueou temporariamente. Tenta novamente em 15 minutos."
  - DB errors → "Erro na base de dados. Verifica os logs para mais detalhes."
- Manter mensagem genérica como fallback

**Ficheiros afetados:**
- `src/telegram/formatters.py` — alterar `format_error_message()` com mapeamento de exceções

**Testes:**
- Cada tipo de exceção gera mensagem específica
- Exceções desconhecidas usam formato genérico
- Mensagens não expõem detalhes internos sensíveis

---

## Resumo de Impacto por Ficheiro

| Ficheiro | Fases |
|---|---|
| `src/config.py` | 1.1, 1.3, 4.2 |
| `src/main.py` | 1.1, 1.2, 1.3 |
| `src/garmin/client.py` | 1.2, 3.1 |
| `src/database/models.py` | 3.1, 4.1 |
| `src/database/repository.py` | 1.1, 1.2, 2.3, 3.1, 4.1, 5.2 |
| `src/telegram/bot.py` | 1.2, 2.1, 2.2, 2.3, 4.1, 5.1 |
| `src/telegram/formatters.py` | 2.1, 2.2, 3.1, 4.2, 5.2, 6.1 |
| `src/scheduler/jobs.py` | 1.1, 4.2 |
| `src/utils/insights.py` | 4.1, 4.2 |
| `src/utils/charts.py` | 4.1, 5.1 |
| `src/utils/healthcheck.py` | 1.3 (novo) |

## Ordem de Implementação Recomendada

```
Fase 1 (Robustez)     ← Prioridade alta, impacto imediato
  1.1 Retry de sync
  1.2 Backfill
  1.3 Healthcheck

Fase 2 (Comandos)     ← Prioridade alta, UX
  2.1 /ajuda
  2.2 /historico
  2.3 /exportar

Fase 6 (Erros)        ← Prioridade alta, rápido de implementar
  6.1 Erros contextuais

Fase 4 (Personalização) ← Prioridade média
  4.2 Alertas diários     ← antes de 4.1 (não depende de goals)
  4.1 Objetivos configuráveis

Fase 3 (Dados)        ← Prioridade média, requer migration
  3.1 HR/Stress/Body Battery

Fase 5 (Visualização) ← Prioridade baixa, nice-to-have
  5.1 Gráfico mensal
  5.2 Comparação semanal
```

## Estimativa de Esforço

| Fase | Complexidade | Dependências |
|---|---|---|
| 1.1 Retry sync | Baixa | Nenhuma |
| 1.2 Backfill | Média | Nenhuma |
| 1.3 Healthcheck | Baixa | Nenhuma |
| 2.1 /ajuda | Baixa | Nenhuma |
| 2.2 /historico | Baixa | Nenhuma |
| 2.3 /exportar | Baixa | Nenhuma |
| 3.1 Novos dados saúde | Alta | Migration DB |
| 4.1 Objetivos | Média | Nova tabela DB |
| 4.2 Alertas diários | Baixa | Nenhuma (goals opcionais) |
| 5.1 Chart mensal | Média | Nenhuma |
| 5.2 Comparação semanal | Baixa | Nenhuma |
| 6.1 Erros contextuais | Baixa | Nenhuma |

---

*Plano gerado a 2026-02-13. Baseia-se no estado atual do GarminBot v1.*
