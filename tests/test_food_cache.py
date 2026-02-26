"""Tests for food cache: FoodCache model and Repository methods."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from src.database.repository import Repository


# ------------------------------------------------------------------ #
# Fixtures                                                              #
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


_ITEM = {
    "name": "banana média",
    "quantity": 1.0,
    "unit": "un",
    "calories": 89.0,
    "protein_g": 1.1,
    "fat_g": 0.3,
    "carbs_g": 23.0,
    "fiber_g": 2.6,
    "source": "llm_estimate",
    "barcode": None,
}


# ------------------------------------------------------------------ #
# get_food_cache — miss cases                                          #
# ------------------------------------------------------------------ #

def test_get_food_cache_miss_returns_none(repo):
    assert repo.get_food_cache("banana") is None


def test_get_food_cache_miss_unknown_query(repo):
    repo.set_food_cache("1 banana", [_ITEM])
    assert repo.get_food_cache("2 bananas") is None


# ------------------------------------------------------------------ #
# set_food_cache + get_food_cache — round-trip                         #
# ------------------------------------------------------------------ #

def test_set_and_get_food_cache_single_item(repo):
    repo.set_food_cache("1 banana média", [_ITEM])
    result = repo.get_food_cache("1 banana média")
    assert result is not None
    assert len(result) == 1
    assert result[0]["name"] == "banana média"
    assert result[0]["calories"] == 89.0


def test_set_and_get_food_cache_multiple_items(repo):
    items = [
        _ITEM,
        {"name": "ovo", "quantity": 2.0, "unit": "un",
         "calories": 140.0, "protein_g": 12.0, "fat_g": 10.0,
         "carbs_g": 0.6, "fiber_g": 0.0, "source": "openfoodfacts", "barcode": None},
    ]
    repo.set_food_cache("1 banana e 2 ovos", items)
    result = repo.get_food_cache("1 banana e 2 ovos")
    assert result is not None
    assert len(result) == 2
    assert result[1]["name"] == "ovo"


# ------------------------------------------------------------------ #
# Normalisation                                                         #
# ------------------------------------------------------------------ #

def test_get_food_cache_case_insensitive(repo):
    """set with uppercase, get with lowercase → same entry."""
    repo.set_food_cache("1 Banana Média", [_ITEM])
    result = repo.get_food_cache("1 banana média")
    assert result is not None


def test_set_food_cache_normalises_before_storing(repo):
    """Two different casings should resolve to one cache entry."""
    repo.set_food_cache("1 Banana", [_ITEM])
    repo.set_food_cache("1 banana", [_ITEM])
    # Only one entry should exist (not two)
    from src.database.models import FoodCache
    from sqlalchemy.orm import Session
    with repo._session() as session:
        count = session.query(FoodCache).count()
    assert count == 1


def test_get_food_cache_strips_whitespace(repo):
    repo.set_food_cache("  1 banana  ", [_ITEM])
    result = repo.get_food_cache("1 banana")
    assert result is not None


# ------------------------------------------------------------------ #
# use_count and last_used_at                                            #
# ------------------------------------------------------------------ #

def test_get_food_cache_increments_use_count(repo):
    from src.database.models import FoodCache
    repo.set_food_cache("1 banana", [_ITEM])

    # Initial use_count is 1 (set on create)
    repo.get_food_cache("1 banana")  # first hit → 2
    repo.get_food_cache("1 banana")  # second hit → 3

    with repo._session() as session:
        entry = session.get(FoodCache, "1 banana")
        assert entry.use_count == 3


# ------------------------------------------------------------------ #
# Overwrite                                                             #
# ------------------------------------------------------------------ #

def test_set_food_cache_overwrites_existing(repo):
    repo.set_food_cache("1 banana", [_ITEM])
    updated = dict(_ITEM, calories=99.0)
    repo.set_food_cache("1 banana", [updated])
    result = repo.get_food_cache("1 banana")
    assert result[0]["calories"] == 99.0


# ------------------------------------------------------------------ #
# All fields preserved                                                  #
# ------------------------------------------------------------------ #

def test_get_food_cache_preserves_all_fields(repo):
    repo.set_food_cache("1 banana", [_ITEM])
    result = repo.get_food_cache("1 banana")
    item = result[0]
    assert item["quantity"] == 1.0
    assert item["unit"] == "un"
    assert item["protein_g"] == 1.1
    assert item["fat_g"] == 0.3
    assert item["carbs_g"] == 23.0
    assert item["fiber_g"] == 2.6
    assert item["source"] == "llm_estimate"
    assert item["barcode"] is None
