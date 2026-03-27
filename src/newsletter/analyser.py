"""LLM-powered analysis of The Pump newsletter posts using Groq."""

from __future__ import annotations

import logging
from typing import Any

import re as _re

from groq import Groq

logger = logging.getLogger(__name__)

_groq_clients: dict[str, "Groq"] = {}

_INJECTION_RE = _re.compile(
    r"(ignore\s+(all\s+)?(previous|prior|above)\s+instructions?|"
    r"^system:\s|^assistant:\s|<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\])",
    _re.IGNORECASE | _re.MULTILINE,
)


def _get_client(api_key: str) -> Groq:
    if api_key not in _groq_clients:
        _groq_clients[api_key] = Groq(api_key=api_key)
    return _groq_clients[api_key]


def _sanitise(text: str) -> str:
    """Remove lines that match common prompt injection patterns."""
    lines = text.splitlines()
    return "\n".join(l for l in lines if not _INJECTION_RE.search(l))


_MODEL = "llama-3.3-70b-versatile"

_USER_PROFILE = """
Perfil do utilizador:
- Nome: Nelson, 44 anos, Portugal
- Objetivo: perda de gordura (93 kg → 85 kg), saúde geral, longevidade funcional
  ("ser um velhote saudável e funcional para os meus netos")
- Treino: ginásio em casa, dias alternados força / passadeira
- Equipamento: barra olímpica com discos (até ~30 kg total), 2 halteres (até 14 kg cada),
  banco articulado, passadeira (12 km/h, 10% inclinação)
- Metodologia de força: 6 movimentos fundamentais (squat, hinge, push, pull, plank, carry)
  — 2-3 sets, 6-10 reps, parando 1-2 reps antes da falha
- Ponto fraco: poucos passos diários (programador, trabalho muito sedentário)
- Nutrição: inconsistente em jantares sociais (vários por mês)
- Dados Garmin sincronizados diariamente: sono (horas, score, fases), passos, body battery,
  FC repouso, stress médio, calorias ativas/repouso, SpO2, intensidade, peso
""".strip()

_DAILY_SYSTEM = f"""És um personal trainer e coach de saúde especializado em longevidade e fitness funcional.
O teu trabalho é analisar o artigo diário do newsletter "The Pump" de Arnold Schwarzenegger
e extrair insights relevantes para o utilizador abaixo.

{_USER_PROFILE}

Instruções:
1. Lê o artigo e identifica 2-3 pontos mais relevantes para este utilizador em específico.
2. Liga pelo menos um desses pontos aos dados Garmin de ontem (fornecidos).
3. Responde SEMPRE em Português de Portugal.
4. Formato obrigatório:

📰 *The Pump — [título do artigo]*

• [insight 1 relevante para o utilizador]
• [insight 2 relevante para o utilizador]
• [insight 3 se aplicável]

💡 *Hoje para ti:* [1-2 frases que ligam o artigo aos teus dados de ontem — específico e accionável]

Sê direto, prático e motivador. Máximo 150 palavras no total."""

_HISTORICAL_SYSTEM = f"""És um personal trainer e coach de saúde especializado em longevidade e fitness funcional.
O teu trabalho é analisar o arquivo completo do newsletter "The Pump" de Arnold Schwarzenegger
e criar um documento de referência personalizado para o utilizador abaixo.

{_USER_PROFILE}

Com base em TODOS os artigos fornecidos, cria um documento estruturado em Português de Portugal
com os insights mais valiosos para este utilizador.

Estrutura do documento:
# The Pump — Insights Personalizados para Nelson

## Treino de Força
[insights mais relevantes sobre treino, adaptados ao equipamento e metodologia do utilizador]

## Nutrição e Composição Corporal
[insights sobre perda de gordura, nutrição, estratégias práticas para o dia-a-dia]

## Recuperação e Sono
[insights sobre sono, recuperação, redução de stress — ligados aos dados Garmin]

## Mentalidade e Consistência
[frases, princípios e estratégias de Arnold e convidados sobre consistência a longo prazo]

## Longevidade e Saúde Funcional
[insights sobre saúde a longo prazo, mobilidade, funcionalidade para envelhecer bem]

## Top 10 Acções Práticas
[lista numerada das 10 recomendações mais accionáveis extraídas do arquivo, adaptadas ao perfil]

Sê específico, cita exemplos concretos dos artigos, e adapta tudo ao perfil e equipamento do utilizador."""


def _format_metrics(metrics: dict[str, Any]) -> str:
    """Format yesterday's Garmin metrics as a readable string for the LLM prompt."""
    parts = []
    if metrics.get("date"):
        parts.append(f"Data: {metrics['date']}")
    if metrics.get("sleep_hours") is not None:
        parts.append(f"Sono: {metrics['sleep_hours']:.1f}h (score: {metrics.get('sleep_score', '—')})")
    if metrics.get("steps") is not None:
        parts.append(f"Passos: {metrics['steps']:,}".replace(",", "."))
    if metrics.get("body_battery_high") is not None:
        parts.append(f"Body Battery: {metrics['body_battery_low']}–{metrics['body_battery_high']}")
    if metrics.get("resting_heart_rate") is not None:
        parts.append(f"FC repouso: {metrics['resting_heart_rate']} bpm")
    if metrics.get("avg_stress") is not None:
        parts.append(f"Stress médio: {metrics['avg_stress']}")
    if metrics.get("active_calories") is not None:
        parts.append(f"Calorias ativas: {metrics['active_calories']} kcal")
    if metrics.get("weight_kg") is not None:
        parts.append(f"Peso: {metrics['weight_kg']:.1f} kg")
    if metrics.get("spo2_avg") is not None:
        parts.append(f"SpO2 médio: {metrics['spo2_avg']:.1f}%")
    return "\n".join(parts) if parts else "Sem dados Garmin disponíveis."


def analyse_daily_post(
    groq_api_key: str,
    post_title: str,
    post_content: str,
    yesterday_metrics: dict[str, Any],
) -> str:
    """Generate a personalised Portuguese insight from a single newsletter post.

    Args:
        groq_api_key: Groq API key.
        post_title: Title of the newsletter post.
        post_content: Full text content of the post.
        yesterday_metrics: Dict of Garmin metrics for yesterday.

    Returns:
        Formatted insight string in Portuguese.
    """
    client = _get_client(groq_api_key)
    metrics_text = _format_metrics(yesterday_metrics)

    # Truncate very long posts to stay within context limits (~6000 chars)
    content_snippet = post_content[:6000] if len(post_content) > 6000 else post_content
    content_snippet = _sanitise(content_snippet)

    user_message = (
        f"Título do artigo: {post_title}\n\n"
        f"--- ARTIGO ---\n{content_snippet}\n--- FIM DO ARTIGO ---\n\n"
        f"--- DADOS GARMIN DE ONTEM ---\n{metrics_text}\n--- FIM DOS DADOS ---"
    )

    try:
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _DAILY_SYSTEM},
                {"role": "user", "content": user_message},
            ],
            temperature=0.4,
            max_tokens=400,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("Newsletter analyser: daily analysis failed: %s", exc)
        raise


def analyse_historical_posts(groq_api_key: str, posts: list[dict[str, str]]) -> str:
    """Generate a comprehensive reference document from all historical posts.

    Args:
        groq_api_key: Groq API key.
        posts: List of dicts with "title" and "content" keys.

    Returns:
        Markdown document string in Portuguese.
    """
    client = _get_client(groq_api_key)

    # Build a condensed digest — keep first 800 chars per post to fit context
    digest_parts = []
    for i, post in enumerate(posts, 1):
        snippet = post["content"][:800] if len(post["content"]) > 800 else post["content"]
        snippet = _sanitise(snippet)
        digest_parts.append(f"### Artigo {i}: {post['title']}\n{snippet}")

    # Split into batches if too many posts (Groq context limit ~32k tokens)
    # ~800 chars per post × 100 posts ≈ 80k chars — need batching above ~40 posts
    if len(digest_parts) > 40:
        return _analyse_historical_batched(client, digest_parts)

    full_digest = "\n\n".join(digest_parts)
    return _call_historical(client, full_digest)


def _call_historical(client: Groq, digest: str) -> str:
    """Single Groq call for historical analysis."""
    digest = _sanitise(digest)
    try:
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _HISTORICAL_SYSTEM},
                {"role": "user", "content": f"Aqui estão todos os artigos:\n\n{digest}"},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("Newsletter analyser: historical analysis failed: %s", exc)
        raise


def _analyse_historical_batched(client: Groq, parts: list[str]) -> str:
    """Process posts in batches and merge into a final document."""
    batch_size = 40
    summaries: list[str] = []

    for i in range(0, len(parts), batch_size):
        batch = parts[i: i + batch_size]
        digest = _sanitise("\n\n".join(batch))
        logger.info(
            "Newsletter analyser: historical batch %d/%d (%d posts)",
            i // batch_size + 1,
            -(-len(parts) // batch_size),
            len(batch),
        )
        try:
            response = client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _HISTORICAL_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Aqui está um lote de artigos (lote {i // batch_size + 1}):\n\n{digest}\n\n"
                            "Extrai os pontos-chave deste lote seguindo a estrutura pedida."
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=1500,
            )
            summaries.append(response.choices[0].message.content.strip())
        except Exception as exc:
            logger.error("Newsletter analyser: batch %d failed: %s", i // batch_size + 1, exc)

    # Merge all batch summaries into a final coherent document
    if not summaries:
        raise RuntimeError("All historical batches failed")

    if len(summaries) == 1:
        return summaries[0]

    merged_input = "\n\n---\n\n".join(summaries)
    try:
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _HISTORICAL_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "Aqui estão os resumos de vários lotes de artigos. "
                        "Consolida-os num único documento coerente seguindo a estrutura pedida, "
                        "eliminando duplicados e mantendo os melhores insights:\n\n" + merged_input
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("Newsletter analyser: merge step failed: %s", exc)
        # Return best effort concatenation
        return "\n\n".join(summaries)
