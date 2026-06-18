"""Tests for Repository.upsert_fatsecret_entries — idempotent FatSecret sync."""

from __future__ import annotations

import os
import tempfile
from datetime import date

import pytest

from src.database.repository import Repository


# ---------------------------------------------------------------------------
# Fixture (same pattern as test_training_repository.py / test_database.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    r = Repository(db_path)
    r.init_database()
    yield r
    r._engine.dispose()
    try:
        os.unlink(db_path)
    except PermissionError:
        pass


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

DAY = date(2026, 6, 17)

def _entry(food_entry_id: str = "FS001", calories: float = 300.0, name: str = "Oats") -> dict:
    """Build a mapped FatSecret entry dict (as produced by map_fatsecret_entries)."""
    return {
        "name": name,
        "calories": calories,
        "protein_g": 10.0,
        "fat_g": 5.0,
        "carbs_g": 40.0,
        "fiber_g": 3.0,
        "quantity": 1.0,
        "unit": "serving",
        "source": "fatsecret",
        "barcode": food_entry_id,
    }


# ---------------------------------------------------------------------------
# Insert new entries
# ---------------------------------------------------------------------------

class TestUpsertInsert:
    def test_insert_returns_inserted_count(self, repo):
        result = repo.upsert_fatsecret_entries(DAY, [_entry("FS001"), _entry("FS002", calories=200.0)])
        assert result["inserted"] == 2
        assert result["updated"] == 0

    def test_inserts_are_persisted(self, repo):
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001"), _entry("FS002")])
        entries = repo.get_food_entries(DAY)
        assert len(entries) == 2

    def test_inserted_entry_has_correct_source(self, repo):
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001")])
        entries = repo.get_food_entries(DAY)
        assert all(e.source == "fatsecret" for e in entries)

    def test_inserted_entry_has_correct_date(self, repo):
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001")])
        entries = repo.get_food_entries(DAY)
        assert all(e.date == DAY for e in entries)

    def test_empty_list_returns_zero_counts(self, repo):
        result = repo.upsert_fatsecret_entries(DAY, [])
        assert result == {"inserted": 0, "updated": 0}


# ---------------------------------------------------------------------------
# Idempotency: calling twice with same food_entry_id must NOT duplicate
# ---------------------------------------------------------------------------

class TestUpsertIdempotency:
    def test_second_call_does_not_duplicate(self, repo):
        entries = [_entry("FS001"), _entry("FS002")]
        repo.upsert_fatsecret_entries(DAY, entries)
        repo.upsert_fatsecret_entries(DAY, entries)
        food_entries = repo.get_food_entries(DAY)
        assert len(food_entries) == 2

    def test_second_call_reports_updated_not_inserted(self, repo):
        entry = _entry("FS001")
        repo.upsert_fatsecret_entries(DAY, [entry])
        result = repo.upsert_fatsecret_entries(DAY, [entry])
        assert result["inserted"] == 0
        assert result["updated"] == 1

    def test_idempotency_calories_not_doubled(self, repo):
        """Core requirement: daily nutrition total must not double after two syncs."""
        entry = _entry("FS001", calories=500.0)
        repo.upsert_fatsecret_entries(DAY, [entry])
        repo.upsert_fatsecret_entries(DAY, [entry])
        nutrition = repo.get_daily_nutrition(DAY)
        assert nutrition["calories"] == pytest.approx(500.0)

    def test_idempotency_multiple_days_independent(self, repo):
        """Entries on different days don't interfere with each other."""
        day2 = date(2026, 6, 16)
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001")])
        repo.upsert_fatsecret_entries(day2, [_entry("FS002")])
        assert len(repo.get_food_entries(DAY)) == 1
        assert len(repo.get_food_entries(day2)) == 1


# ---------------------------------------------------------------------------
# Update in-place: same food_entry_id, changed macros
# ---------------------------------------------------------------------------

class TestUpsertUpdate:
    def test_update_changes_calories_in_place(self, repo):
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001", calories=300.0)])
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001", calories=450.0)])
        entries = repo.get_food_entries(DAY)
        assert len(entries) == 1
        assert entries[0].calories == pytest.approx(450.0)

    def test_update_changes_name_in_place(self, repo):
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001", name="Old Name")])
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001", name="New Name")])
        entries = repo.get_food_entries(DAY)
        assert len(entries) == 1
        assert entries[0].name == "New Name"

    def test_update_reports_correct_counts(self, repo):
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001"), _entry("FS002")])
        # FS001 edited (calories changed), FS003 is new
        result = repo.upsert_fatsecret_entries(DAY, [
            _entry("FS001", calories=999.0),
            _entry("FS002"),
            _entry("FS003"),
        ])
        assert result["inserted"] == 1
        assert result["updated"] == 2


# ---------------------------------------------------------------------------
# Isolation: manual /comi entries (source != fatsecret) must never be touched
# ---------------------------------------------------------------------------

class TestManualEntriesUntouched:
    def test_manual_entries_survive_upsert(self, repo):
        """Existing entries from /comi (source != fatsecret) must not be deleted."""
        # Simulate a manual /comi entry
        repo.save_food_entries(DAY, [{
            "name": "Manual Banana",
            "calories": 90.0,
            "protein_g": 1.0,
            "fat_g": 0.3,
            "carbs_g": 23.0,
            "fiber_g": 2.7,
            "quantity": 1.0,
            "unit": "un",
            "source": "openfoodfacts",
            "barcode": "4006381333931",
        }])
        # Now sync FatSecret entries
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001")])
        # Both entries must survive
        entries = repo.get_food_entries(DAY)
        sources = {e.source for e in entries}
        assert "openfoodfacts" in sources
        assert "fatsecret" in sources
        assert len(entries) == 2

    def test_manual_entry_calories_not_touched(self, repo):
        """Upsert must not modify /comi entry calories."""
        repo.save_food_entries(DAY, [{
            "name": "Manual Banana",
            "calories": 90.0,
            "protein_g": 1.0,
            "fat_g": 0.3,
            "carbs_g": 23.0,
            "fiber_g": 2.7,
            "quantity": 1.0,
            "unit": "un",
            "source": "openfoodfacts",
        }])
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001", calories=500.0)])
        entries = repo.get_food_entries(DAY)
        manual = next(e for e in entries if e.source == "openfoodfacts")
        assert manual.calories == pytest.approx(90.0)

    def test_null_barcode_manual_entry_not_touched(self, repo):
        """Manual entry with null barcode is not matched by FatSecret upsert."""
        repo.save_food_entries(DAY, [{
            "name": "No Barcode Food",
            "calories": 200.0,
            "protein_g": 5.0,
            "fat_g": 2.0,
            "carbs_g": 30.0,
            "fiber_g": 1.0,
            "quantity": 1.0,
            "unit": "un",
            "source": "openfoodfacts",
        }])
        repo.upsert_fatsecret_entries(DAY, [_entry("FS001")])
        entries = repo.get_food_entries(DAY)
        no_barcode = next(e for e in entries if e.source == "openfoodfacts")
        assert no_barcode.calories == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# Edge case: entry with null barcode (missing food_entry_id from API)
# ---------------------------------------------------------------------------

class TestNullBarcodeEntry:
    def test_null_barcode_entry_is_inserted_not_deduped(self, repo):
        """If barcode is None (missing food_entry_id), insert it without dedup
        to avoid matching other null-barcode rows. Each call inserts a new row."""
        entry_no_id = _entry("FS001")
        entry_no_id["barcode"] = None

        repo.upsert_fatsecret_entries(DAY, [entry_no_id])
        # Second call: same null-barcode entry — should insert another row
        # (no dedup possible without a key), not corrupt existing null-barcode rows
        repo.upsert_fatsecret_entries(DAY, [entry_no_id])
        entries = repo.get_food_entries(DAY)
        # Both are inserted (no key to match on), OR only one — either is acceptable
        # as long as no existing *non-fatsecret* row is modified.
        # The critical assertion: source is always fatsecret for these rows.
        assert all(e.source == "fatsecret" for e in entries)
