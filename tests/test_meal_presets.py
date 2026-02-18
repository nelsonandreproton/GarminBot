"""Tests for meal preset feature: DB models, repository methods, and formatters."""

import os
import tempfile
from datetime import date

import pytest

from src.database.repository import Repository
from src.database.models import MealPreset, MealPresetItem


# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #

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


def _lanche_items():
    return [
        {
            "name": "Pudim Continente +Proteína",
            "quantity": 1.0,
            "unit": "un",
            "calories": 148.0,
            "protein_g": 19.0,
            "fat_g": 3.0,
            "carbs_g": 10.0,
            "fiber_g": 1.0,
        },
        {
            "name": "Mini Babybell Light",
            "quantity": 2.0,
            "unit": "un",
            "calories": 100.0,
            "protein_g": 12.0,
            "fat_g": 6.0,
            "carbs_g": 0.0,
            "fiber_g": 0.0,
        },
    ]


# ------------------------------------------------------------------ #
# Repository: save and retrieve presets                               #
# ------------------------------------------------------------------ #

def test_save_and_get_meal_preset(repo):
    repo.save_meal_preset("Lanche", _lanche_items())
    preset = repo.get_meal_preset_by_name("Lanche")
    assert preset is not None
    assert preset.name == "Lanche"
    assert len(preset.items) == 2


def test_get_meal_preset_case_insensitive(repo):
    repo.save_meal_preset("Lanche", _lanche_items())
    assert repo.get_meal_preset_by_name("lanche") is not None
    assert repo.get_meal_preset_by_name("LANCHE") is not None


def test_get_meal_preset_not_found_returns_none(repo):
    assert repo.get_meal_preset_by_name("NãoExiste") is None


def test_save_meal_preset_items_values(repo):
    repo.save_meal_preset("Lanche", _lanche_items())
    preset = repo.get_meal_preset_by_name("Lanche")
    item = preset.items[0]
    assert item.name == "Pudim Continente +Proteína"
    assert item.calories == 148.0
    assert item.protein_g == 19.0
    assert item.carbs_g == 10.0


def test_save_meal_preset_replaces_existing(repo):
    """Saving a preset with the same name replaces the old one."""
    repo.save_meal_preset("Lanche", _lanche_items())
    new_items = [{"name": "Iogurte", "quantity": 1.0, "unit": "un",
                  "calories": 80.0, "protein_g": 6.0, "fat_g": 1.0, "carbs_g": 10.0, "fiber_g": 0.0}]
    repo.save_meal_preset("Lanche", new_items)

    preset = repo.get_meal_preset_by_name("Lanche")
    assert len(preset.items) == 1
    assert preset.items[0].name == "Iogurte"


def test_save_meal_preset_replaces_case_insensitive(repo):
    """Replacement also works when name casing differs."""
    repo.save_meal_preset("Lanche", _lanche_items())
    new_items = [{"name": "Banana", "quantity": 1.0, "unit": "un",
                  "calories": 90.0, "protein_g": 1.0, "fat_g": 0.0, "carbs_g": 20.0, "fiber_g": 2.0}]
    repo.save_meal_preset("lanche", new_items)

    preset = repo.get_meal_preset_by_name("Lanche")
    assert len(preset.items) == 1


def test_list_meal_presets_empty(repo):
    assert repo.list_meal_presets() == []


def test_list_meal_presets_returns_all(repo):
    repo.save_meal_preset("Lanche", _lanche_items())
    repo.save_meal_preset("Pequeno-almoço", [
        {"name": "Ovo", "quantity": 2.0, "unit": "un",
         "calories": 140.0, "protein_g": 12.0, "fat_g": 10.0, "carbs_g": 1.0, "fiber_g": 0.0}
    ])
    presets = repo.list_meal_presets()
    assert len(presets) == 2
    # Ordered by name
    names = [p.name for p in presets]
    assert names == sorted(names)


def test_list_meal_presets_loads_items(repo):
    repo.save_meal_preset("Lanche", _lanche_items())
    presets = repo.list_meal_presets()
    assert len(presets[0].items) == 2


def test_delete_meal_preset_existing(repo):
    repo.save_meal_preset("Lanche", _lanche_items())
    result = repo.delete_meal_preset("Lanche")
    assert result is True
    assert repo.get_meal_preset_by_name("Lanche") is None


def test_delete_meal_preset_nonexistent(repo):
    result = repo.delete_meal_preset("NãoExiste")
    assert result is False


def test_delete_meal_preset_case_insensitive(repo):
    repo.save_meal_preset("Lanche", _lanche_items())
    result = repo.delete_meal_preset("lanche")
    assert result is True


def test_delete_meal_preset_cascades_items(repo):
    """Deleting a preset must also delete its items (cascade)."""
    from sqlalchemy.orm import Session
    repo.save_meal_preset("Lanche", _lanche_items())
    repo.delete_meal_preset("Lanche")

    with repo._Session() as session:
        items = session.query(MealPresetItem).all()
    assert items == []


# ------------------------------------------------------------------ #
# Formatters                                                           #
# ------------------------------------------------------------------ #

from src.telegram.formatters import format_meal_preset_confirmation, format_meal_presets_list


class _FakeItem:
    def __init__(self, name, quantity, unit, calories, protein_g, fat_g, carbs_g, fiber_g):
        self.name = name
        self.quantity = quantity
        self.unit = unit
        self.calories = calories
        self.protein_g = protein_g
        self.fat_g = fat_g
        self.carbs_g = carbs_g
        self.fiber_g = fiber_g


class _FakePreset:
    def __init__(self, name, items):
        self.name = name
        self.items = items


def test_format_meal_preset_confirmation_totals():
    items = [
        _FakeItem("Pudim Proteína", 1, "un", 148.0, 19.0, 3.0, 10.0, 1.0),
        _FakeItem("Mini Babybell Light", 2, "un", 100.0, 12.0, 6.0, 0.0, 0.0),
    ]
    text = format_meal_preset_confirmation("Lanche", items)
    assert "Lanche" in text
    assert "248 kcal" in text  # 148 + 100
    assert "P: 31g" in text    # 19 + 12
    assert "Pudim Proteína" in text.lower() or "Pudim Proteína".lower() in text.lower()


def test_format_meal_preset_confirmation_single_item():
    items = [_FakeItem("Banana", 1, "un", 90.0, 1.0, 0.0, 20.0, 2.0)]
    text = format_meal_preset_confirmation("Snack", items)
    assert "Snack" in text
    assert "90 kcal" in text


def test_format_meal_presets_list_empty():
    text = format_meal_presets_list([])
    assert "Sem presets" in text


def test_format_meal_presets_list_shows_name_and_cals():
    items = [
        _FakeItem("Pudim", 1, "un", 148.0, 19.0, 3.0, 10.0, 1.0),
        _FakeItem("Babybell", 2, "un", 100.0, 12.0, 6.0, 0.0, 0.0),
    ]
    presets = [_FakePreset("Lanche", items)]
    text = format_meal_presets_list(presets)
    assert "Lanche" in text
    assert "248 kcal" in text
    assert "2 item" in text
