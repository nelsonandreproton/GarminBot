"""Tests for src/training/recommender.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.training.recommender import generate_workout


def _sample_metrics():
    return {
        "sleep_hours": 7.5,
        "sleep_score": 82,
        "steps": 10000,
        "active_calories": 400,
        "avg_stress": 35,
        "body_battery_high": 85,
        "body_battery_low": 25,
    }


def _sample_nutrition():
    return {"calories": 1850.0, "protein_g": 120.0}


def test_generate_workout_success():
    """Successful LLM call returns workout text."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Aquecimento: 5min caminhada\nSquat: Goblet squat 3x12"

    with patch("src.training.recommender.Groq") as mock_groq_cls:
        mock_client = MagicMock()
        mock_groq_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_workout(
            metrics=_sample_metrics(),
            nutrition=_sample_nutrition(),
            equipment="dumbbells, bench",
            training_minutes=45,
            api_key="test-key",
            weekday=0,
        )

    assert result is not None
    assert "Goblet squat" in result
    mock_groq_cls.assert_called_once_with(api_key="test-key")


def test_generate_workout_prompt_contains_equipment():
    """Prompt includes the equipment list."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "treino"

    with patch("src.training.recommender.Groq") as mock_groq_cls:
        mock_client = MagicMock()
        mock_groq_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        generate_workout(
            metrics=_sample_metrics(),
            nutrition=None,
            equipment="2 dumbbells max 14kg, bench",
            training_minutes=60,
            api_key="key",
            weekday=2,
        )

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = messages[1]["content"]
        assert "2 dumbbells max 14kg" in user_msg
        assert "60 minutos" in user_msg
        assert "Quarta" in user_msg


def test_generate_workout_prompt_contains_sleep_data():
    """Prompt includes sleep metrics."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "treino"

    with patch("src.training.recommender.Groq") as mock_groq_cls:
        mock_client = MagicMock()
        mock_groq_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        generate_workout(
            metrics={"sleep_hours": 5.0, "sleep_score": 40, "steps": 8000},
            nutrition=None,
            equipment="bands",
            training_minutes=30,
            api_key="key",
            weekday=0,
        )

        user_msg = mock_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "5.0h" in user_msg
        assert "40/100" in user_msg


def test_generate_workout_without_nutrition():
    """Missing nutrition data still generates a workout."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "treino leve"

    with patch("src.training.recommender.Groq") as mock_groq_cls:
        mock_client = MagicMock()
        mock_groq_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_workout(
            metrics=_sample_metrics(),
            nutrition=None,
            equipment="bands",
            training_minutes=30,
            api_key="key",
            weekday=4,
        )

    assert result == "treino leve"
    user_msg = mock_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Nutrição" not in user_msg


def test_generate_workout_api_error_returns_none():
    """API failure returns None instead of raising."""
    with patch("src.training.recommender.Groq") as mock_groq_cls:
        mock_client = MagicMock()
        mock_groq_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API error")

        result = generate_workout(
            metrics=_sample_metrics(),
            nutrition=None,
            equipment="bands",
            training_minutes=30,
            api_key="key",
        )

    assert result is None


def test_generate_workout_strips_code_fences():
    """Response with markdown fences is cleaned."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "```\ntreino do dia\n```"

    with patch("src.training.recommender.Groq") as mock_groq_cls:
        mock_client = MagicMock()
        mock_groq_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_workout(
            metrics={},
            nutrition=None,
            equipment="bands",
            training_minutes=30,
            api_key="key",
        )

    assert result == "treino do dia"
    assert "```" not in result


def test_generate_workout_system_prompt_has_patterns():
    """System prompt includes the 5 movement patterns."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "treino"

    with patch("src.training.recommender.Groq") as mock_groq_cls:
        mock_client = MagicMock()
        mock_groq_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        generate_workout(
            metrics={}, nutrition=None, equipment="bands",
            training_minutes=30, api_key="key",
        )

        system_msg = mock_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert "Squat" in system_msg
        assert "Push" in system_msg
        assert "Pull" in system_msg
        assert "Hinge" in system_msg
        assert "Carry" in system_msg
