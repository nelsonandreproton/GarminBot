"""Tests for src/nutrition/recommender.py."""

from unittest.mock import MagicMock, patch

from src.nutrition.recommender import generate_nutrition_recommendation


def _sample_nutrition():
    return {
        "calories": 1850.0,
        "protein_g": 120.0,
        "fat_g": 65.0,
        "carbs_g": 210.0,
        "fiber_g": 25.0,
        "entry_count": 5,
    }


def _sample_goals():
    return {"calories": 1750.0, "protein_g": 150.0, "fat_g": 60.0, "carbs_g": 200.0}


def _sample_metrics():
    return {
        "active_calories": 500,
        "resting_calories": 1600,
        "sleep_hours": 7.5,
        "steps": 10000,
    }


def test_generate_recommendation_success():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Recomendo aumentar a proteína ao pequeno-almoço."

    with patch("src.nutrition.recommender.Groq") as mock_groq:
        mock_client = MagicMock()
        mock_groq.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_nutrition_recommendation(
            nutrition=_sample_nutrition(),
            goals=_sample_goals(),
            api_key="test-key",
        )

    assert result is not None
    assert "proteína" in result


def test_returns_none_without_api_key():
    result = generate_nutrition_recommendation(
        nutrition=_sample_nutrition(),
        goals=_sample_goals(),
        api_key="",
    )
    assert result is None


def test_returns_none_without_macro_goals():
    result = generate_nutrition_recommendation(
        nutrition=_sample_nutrition(),
        goals={"steps": 10000.0, "sleep_hours": 7.0},
        api_key="test-key",
    )
    assert result is None


def test_api_error_returns_none():
    with patch("src.nutrition.recommender.Groq") as mock_groq:
        mock_client = MagicMock()
        mock_groq.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API error")

        result = generate_nutrition_recommendation(
            nutrition=_sample_nutrition(),
            goals=_sample_goals(),
            api_key="test-key",
        )

    assert result is None


def test_prompt_contains_nutrition_data():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "recomendação"

    with patch("src.nutrition.recommender.Groq") as mock_groq:
        mock_client = MagicMock()
        mock_groq.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        generate_nutrition_recommendation(
            nutrition=_sample_nutrition(),
            goals=_sample_goals(),
            metrics=_sample_metrics(),
            api_key="test-key",
        )

        user_msg = mock_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "1850 kcal" in user_msg  # eaten calories
        assert "1750 kcal" in user_msg  # goal calories
        assert "7.5h" in user_msg  # sleep
        assert "10000" in user_msg  # steps


def test_prompt_contains_weekly_averages():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "recomendação"

    with patch("src.nutrition.recommender.Groq") as mock_groq:
        mock_client = MagicMock()
        mock_groq.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        generate_nutrition_recommendation(
            nutrition=_sample_nutrition(),
            goals=_sample_goals(),
            weekly_nutrition={"avg_calories": 1800, "avg_protein": 110, "days_with_data": 5},
            api_key="test-key",
        )

        user_msg = mock_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "1800 kcal/dia" in user_msg
        assert "110g proteína" in user_msg


def test_strips_code_fences():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "```\nRecomendação aqui\n```"

    with patch("src.nutrition.recommender.Groq") as mock_groq:
        mock_client = MagicMock()
        mock_groq.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_nutrition_recommendation(
            nutrition=_sample_nutrition(),
            goals=_sample_goals(),
            api_key="test-key",
        )

    assert result == "Recomendação aqui"
