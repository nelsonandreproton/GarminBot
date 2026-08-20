"""Build CSV/JSON/XLSX byte payloads for exporting synced Garmin activities."""

from __future__ import annotations

import csv
import io
import json

_COLUMNS = (
    "data", "tipo", "duracao_min", "calorias",
    "bpm_min", "bpm_medio", "bpm_max", "distancia_km", "nome",
)


def _row(entry) -> tuple:
    return (
        entry.date.isoformat(), entry.type_key, entry.duration_min, entry.calories,
        entry.min_hr, entry.avg_hr, entry.max_hr, entry.distance_km, entry.name,
    )


def build_csv(entries: list) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_COLUMNS)
    for entry in entries:
        writer.writerow(_row(entry))
    return buf.getvalue().encode("utf-8")


def build_json(entries: list) -> bytes:
    payload = [dict(zip(_COLUMNS, _row(entry))) for entry in entries]
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def build_xlsx(entries: list) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Treinos"
    ws.append(list(_COLUMNS))
    for entry in entries:
        ws.append(list(_row(entry)))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_BUILDERS = {"csv": build_csv, "json": build_json, "xlsx": build_xlsx}


def build_export(fmt: str, entries: list) -> bytes:
    """Build the export payload for the given format ('csv', 'json', or 'xlsx')."""
    return _BUILDERS[fmt](entries)
