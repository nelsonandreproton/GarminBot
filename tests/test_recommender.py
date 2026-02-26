"""Tests for src/training/recommender.py."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from src.training.recommender import _build_user_prompt, generate_workout


def _make_metrics(**overrides) -> dict:
    base = {
        "date": date(2026, 2, 25),
        "sleep_hours": 7.5,
        "sleep_score": 82,
        "sleep_quality": "Bom",
        "steps": 8000,
        "active_calories": 400,
        "resting_calories": 1700,
        "total_calories": 2100,
        "resting_heart_rate": 58,
        "avg_stress": 30,
        "body_battery_high": 85,
        "body_battery_low": 15,
        "weight_kg": 80.0,
    }
    base.update(overrides)
    return base


def _make_groq_response(content: str) -> MagicMock:
    mock = MagicMock()
    mock.chat.completions.create.return_value.choices[0].message.content = content
    return mock


# ------------------------------------------------------------------ #
# _build_user_prompt                                                   #
# ------------------------------------------------------------------ #

class TestBuildUserPrompt:
    def test_includes_equipment(self):
        prompt = _build_user_prompt(_make_metrics(), None, "MY_UNIQUE_EQUIPMENT_STRING", 45, [])
        assert "MY_UNIQUE_EQUIPMENT_STRING" in prompt

    def test_includes_training_minutes(self):
        prompt = _build_user_prompt(_make_metrics(), None, "halteres", 60, [])
        assert "60 minutos" in prompt

    def test_no_nutrition_shows_sem_dados(self):
        prompt = _build_user_prompt(_make_metrics(), None, "halteres", 45, [])
        assert "Sem dados de nutrição" in prompt

    def test_with_nutrition_shows_values(self):
        nutrition = {"calories": 1850, "protein_g": 130, "fat_g": 55, "carbs_g": 200}
        prompt = _build_user_prompt(_make_metrics(), nutrition, "halteres", 45, [])
        assert "1850" in prompt
        assert "130g" in prompt

    def test_no_history_shows_nenhum(self):
        prompt = _build_user_prompt(_make_metrics(), None, "halteres", 45, [])
        assert "Nenhum treino registado" in prompt

    def test_with_history_shows_entries(self):
        history = [
            {"date": "2026-02-24", "description": "Bench press 4x8"},
            {"date": "2026-02-22", "description": "Pull-ups 3x10"},
        ]
        prompt = _build_user_prompt(_make_metrics(), None, "halteres", 45, history)
        assert "2026-02-24" in prompt
        assert "Bench press 4x8" in prompt
        assert "2026-02-22" in prompt

    def test_none_values_show_dash(self):
        metrics = _make_metrics(sleep_hours=None, sleep_score=None, steps=None, weight_kg=None)
        prompt = _build_user_prompt(metrics, None, "halteres", 45, [])
        assert "—" in prompt

    def test_includes_date(self):
        prompt = _build_user_prompt(_make_metrics(), None, "halteres", 45, [])
        assert "2026-02-25" in prompt

    def test_weight_history_shows_in_prompt(self):
        from datetime import date as _date
        wh = [(_date(2026, 2, 25), 81.2), (_date(2026, 2, 20), 82.0)]
        prompt = _build_user_prompt(_make_metrics(), None, "halteres", 45, [], weight_history=wh)
        assert "81.2 kg" in prompt
        assert "82.0 kg" in prompt

    def test_waist_history_shows_in_prompt(self):
        from datetime import date as _date
        wh = [(_date(2026, 2, 25), 94.5)]
        prompt = _build_user_prompt(_make_metrics(), None, "halteres", 45, [], waist_history=wh)
        assert "94.5 cm" in prompt

    def test_weight_goal_shows_in_prompt(self):
        from datetime import date as _date
        wh = [(_date(2026, 2, 25), 81.0)]
        prompt = _build_user_prompt(_make_metrics(), None, "halteres", 45, [],
                                    weight_history=wh, weight_goal=75.0)
        assert "75.0 kg" in prompt

    def test_no_history_shows_sem_registos(self):
        prompt = _build_user_prompt(_make_metrics(), None, "halteres", 45, [])
        assert "Sem registos" in prompt


# ------------------------------------------------------------------ #
# generate_workout                                                     #
# ------------------------------------------------------------------ #

class TestGenerateWorkout:
    def test_returns_text_on_success(self):
        mock_client = _make_groq_response("🏋️ TREINO — Push\nBench press 4x8")
        with patch("src.training.recommender.Groq", return_value=mock_client):
            result = generate_workout(_make_metrics(), None, "halteres", 45, [], "fake")
        assert result is not None
        assert "TREINO" in result

    def test_returns_none_on_api_error(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API down")
        with patch("src.training.recommender.Groq", return_value=mock_client):
            result = generate_workout(_make_metrics(), None, "halteres", 45, [], "fake")
        assert result is None

    def test_strips_markdown_code_fences(self):
        mock_client = _make_groq_response("```\n🏋️ TREINO\nBench press\n```")
        with patch("src.training.recommender.Groq", return_value=mock_client):
            result = generate_workout(_make_metrics(), None, "halteres", 45, [], "fake")
        assert result is not None
        assert "```" not in result
        assert "TREINO" in result

    def test_strips_code_fence_without_closing(self):
        mock_client = _make_groq_response("```\n🏋️ TREINO\nBench press")
        with patch("src.training.recommender.Groq", return_value=mock_client):
            result = generate_workout(_make_metrics(), None, "halteres", 45, [], "fake")
        assert result is not None
        assert "```" not in result

    def test_prompt_contains_equipment(self):
        """Verify equipment ends up in the actual API call."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        with patch("src.training.recommender.Groq", return_value=mock_client):
            generate_workout(_make_metrics(), None, "SPECIAL_EQUIPMENT_XYZ", 45, [], "fake")
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "SPECIAL_EQUIPMENT_XYZ" in user_content

    def test_prompt_contains_training_minutes(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        with patch("src.training.recommender.Groq", return_value=mock_client):
            generate_workout(_make_metrics(), None, "halteres", 75, [], "fake")
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "75 minutos" in user_content

    def test_uses_correct_model(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        with patch("src.training.recommender.Groq", return_value=mock_client):
            generate_workout(_make_metrics(), None, "halteres", 45, [], "fake")
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "llama-3.3-70b-versatile"

    def test_with_nutrition_passes_it(self):
        nutrition = {"calories": 1800, "protein_g": 120, "fat_g": 60, "carbs_g": 200}
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        with patch("src.training.recommender.Groq", return_value=mock_client):
            generate_workout(_make_metrics(), nutrition, "halteres", 45, [], "fake")
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "1800" in user_content

    def test_with_history_passes_it(self):
        history = [{"date": "2026-02-24", "description": "Deadlift 4x5"}]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        with patch("src.training.recommender.Groq", return_value=mock_client):
            generate_workout(_make_metrics(), None, "halteres", 45, history, "fake")
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "Deadlift 4x5" in user_content

    def test_weight_history_in_prompt(self):
        from datetime import date as _date
        weight_history = [(_date(2026, 2, 25), 81.2), (_date(2026, 2, 22), 81.5)]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        with patch("src.training.recommender.Groq", return_value=mock_client):
            generate_workout(
                _make_metrics(), None, "halteres", 45, [], "fake",
                weight_history=weight_history,
            )
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "81.2 kg" in user_content
        assert "81.5 kg" in user_content

    def test_waist_history_in_prompt(self):
        from datetime import date as _date
        waist_history = [(_date(2026, 2, 25), 94.0), (_date(2026, 2, 10), 95.5)]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        with patch("src.training.recommender.Groq", return_value=mock_client):
            generate_workout(
                _make_metrics(), None, "halteres", 45, [], "fake",
                waist_history=waist_history,
            )
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "94.0 cm" in user_content
        assert "95.5 cm" in user_content

    def test_weight_goal_in_prompt(self):
        from datetime import date as _date
        weight_history = [(_date(2026, 2, 25), 81.0)]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        with patch("src.training.recommender.Groq", return_value=mock_client):
            generate_workout(
                _make_metrics(), None, "halteres", 45, [], "fake",
                weight_history=weight_history,
                weight_goal=75.0,
            )
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "75.0 kg" in user_content

    def test_no_weight_or_waist_history_shows_sem_registos(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        with patch("src.training.recommender.Groq", return_value=mock_client):
            generate_workout(_make_metrics(), None, "halteres", 45, [], "fake")
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "Sem registos" in user_content
