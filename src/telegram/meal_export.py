"""Build CSV/JSON/XLSX byte payloads for exporting registered meals (food_entries)."""

from __future__ import annotations

import csv
import io
import json

_COLUMNS = (
    "data", "nome", "quantidade", "unidade", "calorias",
    "proteina_g", "gordura_g", "hidratos_g", "fibra_g", "fonte", "barcode",
)


def _row(entry) -> tuple:
    return (
        entry.date.isoformat(), entry.name, entry.quantity, entry.unit, entry.calories,
        entry.protein_g, entry.fat_g, entry.carbs_g, entry.fiber_g, entry.source, entry.barcode,
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
    ws.title = "Refeições"
    ws.append(list(_COLUMNS))
    for entry in entries:
        ws.append(list(_row(entry)))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_BUILDERS = {"csv": build_csv, "json": build_json, "xlsx": build_xlsx}
_MIME_TYPES = {
    "csv": "text/csv",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def build_export(fmt: str, entries: list) -> bytes:
    """Build the export payload for the given format ('csv', 'json', or 'xlsx')."""
    return _BUILDERS[fmt](entries)
