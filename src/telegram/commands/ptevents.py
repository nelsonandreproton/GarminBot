"""Command handlers: /alertas, /alertas_tipos, /alertas_severidade — PTEvents integration."""

from __future__ import annotations

import logging

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_ALL_TYPES: list[tuple[str, str]] = [
    ("FIRE",                  "INCENDIO"),
    ("CIVIL_PROTECTION",      "PROTECAO CIVIL"),
    ("EVACUATION",            "EVACUACAO"),
    ("STORM",                 "TEMPESTADE"),
    ("WIND",                  "VENTO FORTE"),
    ("RAIN",                  "CHUVA INTENSA"),
    ("HEAT",                  "CALOR EXTREMO"),
    ("COLD",                  "FRIO EXTREMO"),
    ("FLOOD",                 "INUNDACAO"),
    ("DROUGHT",               "SECA"),
    ("EARTHQUAKE",            "SISMO"),
    ("TSUNAMI",               "TSUNAMI"),
    ("LANDSLIDE",             "DESLIZAMENTO"),
    ("ACCIDENT",              "ACIDENTE"),
    ("ROAD_CLOSURE",          "CORTE DE TRANSITO"),
    ("CONGESTION",            "CONGESTIONAMENTO"),
    ("ROADWORK",              "OBRAS NA VIA"),
    ("POWER_OUTAGE",          "CORTE DE ENERGIA"),
    ("WATER_OUTAGE",          "CORTE DE AGUA"),
    ("GAS_LEAK",              "FUGA DE GAS"),
    ("TELECOM",               "FALHA TELECOM"),
    ("STRIKE",                "GREVE"),
    ("SERVICE_DISRUPTION",    "PERTURBACAO"),
    ("DELAY",                 "ATRASO"),
    ("PLANNED_WORKS",         "OBRAS PLANEADAS"),
    ("EVENT_CLOSURE",         "EVENTO/ENCERRAMENTO"),
    ("SCHEDULED_MAINTENANCE", "MANUTENCAO"),
    ("AIR_QUALITY",           "QUALIDADE DO AR"),
    ("FIRE_RISK",             "RISCO DE INCENDIO"),
    ("UV_ALERT",              "ALERTA UV"),
]
_ALL_TYPE_VALUES: list[str] = [v for v, _ in _ALL_TYPES]
_LABEL: dict[str, str] = dict(_ALL_TYPES)
_VALID_TYPES: frozenset[str] = frozenset(_ALL_TYPE_VALUES)

_SEVERITY_EMOJI = {"LOW": "🔵", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}
_SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

TYPES_PER_PAGE = 10
TOTAL_PAGES = (len(_ALL_TYPES) + TYPES_PER_PAGE - 1) // TYPES_PER_PAGE


def _normalize(enabled_types: list[str] | None) -> set[str]:
    return set(enabled_types) if enabled_types is not None else set(_ALL_TYPE_VALUES)


def _build_types_kb(enabled: set[str], page: int = 0) -> InlineKeyboardMarkup:
    if not 0 <= page < TOTAL_PAGES:
        page = 0
    chunk = _ALL_TYPES[page * TYPES_PER_PAGE:(page + 1) * TYPES_PER_PAGE]
    rows = [
        [InlineKeyboardButton(
            f"{'✅' if v in enabled else '❌'} {label}",
            callback_data=f"pte_tt:{page}:{v}",
        )]
        for v, label in chunk
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Anterior", callback_data=f"pte_tp:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{TOTAL_PAGES}", callback_data="pte_noop"))
    if page < TOTAL_PAGES - 1:
        nav.append(InlineKeyboardButton("Seguinte »", callback_data=f"pte_tp:{page + 1}"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton("✅ Todos", callback_data=f"pte_tx:all:{page}"),
        InlineKeyboardButton("❌ Nenhum", callback_data=f"pte_tx:none:{page}"),
    ])
    return InlineKeyboardMarkup(rows)


def _build_severity_kb(current: str) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(
            f"{'▶ ' if s == current else ''}{_SEVERITY_EMOJI[s]} {s}",
            callback_data=f"pte_sv:{s}",
        )
        for s in _SEVERITIES
    ]
    return InlineKeyboardMarkup([row])


async def _get_filters(base_url: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{base_url}/ptevents/api/filters")
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.error("PTEvents GET /filters failed: %s", exc)
        return None


async def _put_filters(base_url: str, payload: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.put(f"{base_url}/ptevents/api/filters", json=payload)
            r.raise_for_status()
            return True
    except Exception as exc:
        logger.error("PTEvents PUT /filters failed: %s", exc)
        return False


async def _get_status(base_url: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{base_url}/ptevents/api/status")
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.error("PTEvents GET /status failed: %s", exc)
        return None


def register_ptevents_handler(app, base_url: str) -> None:
    """Register /alertas, /alertas_tipos, /alertas_severidade and their callbacks."""
    from telegram.ext import CallbackQueryHandler, CommandHandler

    mixin = _PTEventsMixin(base_url)

    def _cmd(command, handler):
        return CommandHandler(command, handler)

    app.add_handler(_cmd("alertas", mixin.cmd_alertas))
    app.add_handler(_cmd("alertas_tipos", mixin.cmd_alertas_tipos))
    app.add_handler(_cmd("alertas_severidade", mixin.cmd_alertas_severidade))
    app.add_handler(CallbackQueryHandler(mixin.cb_ptevents, pattern=r"^pte_"))

    logger.info("PTEvents handlers registered (base_url=%s)", base_url)


class _PTEventsMixin:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def cmd_alertas(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        status = await _get_status(self._base_url)
        if status is None:
            await update.message.reply_text("PTEvents indisponivel de momento.")
            return
        sev = status.get("min_severity", "LOW")
        active = status.get("active_events", 0)
        et = status.get("enabled_types")
        n_types = len(_ALL_TYPE_VALUES)
        types_line = f"Todos os {n_types} tipos ativos" if et is None else f"{len(et)}/{n_types} tipos ativos"
        await update.message.reply_text(
            f"Eventos activos: {active}\n"
            f"Severidade minima: {_SEVERITY_EMOJI.get(sev, '')} {sev}\n"
            f"{types_line}\n\n"
            f"/alertas_tipos — gerir tipos\n"
            f"/alertas_severidade — gerir severidade",
        )

    async def cmd_alertas_tipos(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        filters = await _get_filters(self._base_url)
        if filters is None:
            await update.message.reply_text("PTEvents indisponivel de momento.")
            return
        enabled = _normalize(filters.get("enabled_types"))
        await update.message.reply_text(
            "Tipos de eventos (✅ ativo / ❌ inativo):",
            reply_markup=_build_types_kb(enabled, page=0),
        )

    async def cmd_alertas_severidade(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        filters = await _get_filters(self._base_url)
        if filters is None:
            await update.message.reply_text("PTEvents indisponivel de momento.")
            return
        current = (filters.get("min_severity") or "LOW").upper()
        await update.message.reply_text(
            f"Severidade minima atual: {current}",
            reply_markup=_build_severity_kb(current),
        )

    async def cb_ptevents(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        data = query.data or ""

        if data == "pte_noop":
            await query.answer()
            return

        if data.startswith("pte_tp:"):
            try:
                page = int(data.split(":", 1)[1])
            except ValueError:
                await query.answer()
                return
            filters = await _get_filters(self._base_url)
            if filters is None:
                await query.answer("PTEvents indisponivel")
                return
            enabled = _normalize(filters.get("enabled_types"))
            await _try_edit_markup(query, reply_markup=_build_types_kb(enabled, page=page))
            await query.answer()
            return

        if data.startswith("pte_tt:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                await query.answer()
                return
            try:
                page = int(parts[1])
            except ValueError:
                page = 0
            type_value = parts[2]
            if type_value not in _VALID_TYPES:
                await query.answer("Tipo invalido")
                return
            filters = await _get_filters(self._base_url)
            if filters is None:
                await query.answer("PTEvents indisponivel")
                return
            enabled = _normalize(filters.get("enabled_types"))
            if type_value in enabled:
                enabled.discard(type_value)
            else:
                enabled.add(type_value)
            new_list = [v for v in _ALL_TYPE_VALUES if v in enabled]
            ok = await _put_filters(self._base_url, {"enabled_types": new_list})
            if not ok:
                await query.answer("Erro ao guardar")
                return
            await _try_edit_markup(query, reply_markup=_build_types_kb(enabled, page=page))
            state = "on" if type_value in enabled else "off"
            await query.answer(f"{state}: {_LABEL.get(type_value, type_value)}")
            return

        if data.startswith("pte_tx:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                await query.answer()
                return
            action, page_str = parts[1], parts[2]
            try:
                page = int(page_str)
            except ValueError:
                page = 0
            if action == "all":
                new_list = list(_ALL_TYPE_VALUES)
                answer = "Todos ativos"
            elif action == "none":
                new_list = []
                answer = "Todos inativos"
            else:
                await query.answer()
                return
            ok = await _put_filters(self._base_url, {"enabled_types": new_list})
            if not ok:
                await query.answer("Erro ao guardar")
                return
            await _try_edit_markup(query, reply_markup=_build_types_kb(set(new_list), page=page))
            await query.answer(answer)
            return

        if data.startswith("pte_sv:"):
            sev = data.split(":", 1)[1].upper()
            if sev not in _SEVERITIES:
                await query.answer("Severidade invalida")
                return
            ok = await _put_filters(self._base_url, {"min_severity": sev})
            if not ok:
                await query.answer("Erro ao guardar")
                return
            await _try_edit_text(
                query,
                text=f"Severidade minima atual: {sev}",
                reply_markup=_build_severity_kb(sev),
            )
            await query.answer(f"-> {sev}")
            return

        await query.answer()


async def _try_edit_markup(query, **kwargs) -> None:
    try:
        await query.edit_message_reply_markup(**kwargs)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def _try_edit_text(query, **kwargs) -> None:
    try:
        await query.edit_message_text(**kwargs)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise
