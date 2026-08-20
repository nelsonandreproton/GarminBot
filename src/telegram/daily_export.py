"""Build CSV/JSON/XLSX byte payloads for exporting daily Garmin metrics
(the same data shown by /sync's daily summary, one row per day)."""

from __future__ import annotations

import csv
import io
import json

_COLUMNS = (
    "data", "sono_horas", "sono_score", "sono_qualidade", "passos",
    "calorias_ativas", "calorias_repouso", "fc_repouso", "stress_medio",
    "body_battery_max", "body_battery_min", "peso_kg", "perimetro_cm",
)


def _row(entry, waist_by_date: dict) -> tuple:
    return (
        entry.date.isoformat(), entry.sleep_hours, entry.sleep_score, entry.sleep_quality,
        entry.steps, entry.active_calories, entry.resting_calories,
        entry.resting_heart_rate, entry.avg_stress,
        entry.body_battery_high, entry.body_battery_low,
        entry.weight_kg, waist_by_date.get(entry.date),
    )


def build_csv(entries: list, waist_by_date: dict) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_COLUMNS)
    for entry in entries:
        writer.writerow(_row(entry, waist_by_date))
    return buf.getvalue().encode("utf-8")


def build_json(entries: list, waist_by_date: dict) -> bytes:
    payload = [dict(zip(_COLUMNS, _row(entry, waist_by_date))) for entry in entries]
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def build_xlsx(entries: list, waist_by_date: dict) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Métricas Diárias"
    ws.append(list(_COLUMNS))
    for entry in entries:
        ws.append(list(_row(entry, waist_by_date)))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_BUILDERS = {"csv": build_csv, "json": build_json, "xlsx": build_xlsx}


def build_export(fmt: str, entries: list, waist_by_date: dict) -> bytes:
    """Build the export payload for the given format ('csv', 'json', or 'xlsx')."""
    return _BUILDERS[fmt](entries, waist_by_date)
