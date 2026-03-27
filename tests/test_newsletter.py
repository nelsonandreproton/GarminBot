"""Tests for the newsletter scraper, analyser, and repository methods."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.newsletter.scraper import (
    PostMeta,
    _extract_posts_from_page,
    _parse_date,
    scrape_post_content,
    scrape_post_list,
)
from src.newsletter.analyser import _format_metrics, analyse_daily_post


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_iso_format(self):
        assert _parse_date("2024-03-15") == date(2024, 3, 15)

    def test_iso_with_time(self):
        assert _parse_date("2024-03-15T08:00:00Z") == date(2024, 3, 15)

    def test_long_month(self):
        assert _parse_date("March 15, 2024") == date(2024, 3, 15)

    def test_short_month(self):
        assert _parse_date("Mar 15, 2024") == date(2024, 3, 15)

    def test_long_month_no_comma(self):
        assert _parse_date("March 15 2024") == date(2024, 3, 15)

    def test_unrecognised_returns_none(self):
        assert _parse_date("not a date at all") is None

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_whitespace_stripped(self):
        assert _parse_date("  2024-01-01  ") == date(2024, 1, 1)


# ---------------------------------------------------------------------------
# _extract_posts_from_page
# ---------------------------------------------------------------------------


class TestExtractPostsFromPage:
    def _soup(self, html: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser")

    def test_extracts_post_with_date(self):
        html = """
        <article>
          <a href="/blogs/newsletter/hello-world"><h2>Hello World</h2></a>
          <time datetime="2024-05-01">May 1, 2024</time>
        </article>
        """
        posts = _extract_posts_from_page(self._soup(html))
        assert len(posts) == 1
        assert posts[0].url == "https://arnoldspumpclub.com/blogs/newsletter/hello-world"
        assert posts[0].title == "Hello World"
        assert posts[0].published_date == date(2024, 5, 1)

    def test_skips_links_without_newsletter_path(self):
        html = """
        <a href="/about">About</a>
        <a href="/blogs/newsletter/real-post">Real Post</a>
        """
        posts = _extract_posts_from_page(self._soup(html))
        assert len(posts) == 1
        assert "real-post" in posts[0].url

    def test_deduplicates_same_url(self):
        html = """
        <a href="/blogs/newsletter/dupe">Post A</a>
        <a href="/blogs/newsletter/dupe">Post A again</a>
        """
        posts = _extract_posts_from_page(self._soup(html))
        assert len(posts) == 1

    def test_absolute_url_preserved(self):
        html = '<a href="https://arnoldspumpclub.com/blogs/newsletter/abs">Full URL Post</a>'
        posts = _extract_posts_from_page(self._soup(html))
        assert posts[0].url == "https://arnoldspumpclub.com/blogs/newsletter/abs"

    def test_no_links_returns_empty(self):
        posts = _extract_posts_from_page(self._soup("<p>Nothing here</p>"))
        assert posts == []


# ---------------------------------------------------------------------------
# scrape_post_list (mocked HTTP)
# ---------------------------------------------------------------------------


class TestScrapePostList:
    def _make_listing_html(self, posts: list[tuple]) -> str:
        """Build a minimal Shopify-style listing page."""
        items = ""
        for url, title, dt in posts:
            items += f"""
            <article>
              <a href="{url}"><h2>{title}</h2></a>
              <time datetime="{dt}">{dt}</time>
            </article>
            """
        return f"<html><body>{items}</body></html>"

    @patch("src.newsletter.scraper._get")
    def test_returns_posts_sorted_oldest_first(self, mock_get):
        from bs4 import BeautifulSoup
        html = self._make_listing_html([
            ("/blogs/newsletter/b", "Post B", "2024-06-01"),
            ("/blogs/newsletter/a", "Post A", "2024-01-01"),
        ])
        mock_get.return_value = BeautifulSoup(html, "html.parser")
        posts = scrape_post_list()
        assert len(posts) == 2
        assert posts[0].published_date < posts[1].published_date

    @patch("src.newsletter.scraper._get")
    def test_empty_page_returns_empty_list(self, mock_get):
        from bs4 import BeautifulSoup
        mock_get.return_value = BeautifulSoup("<html><body></body></html>", "html.parser")
        posts = scrape_post_list()
        assert posts == []

    @patch("src.newsletter.scraper._get")
    def test_http_error_returns_empty_list(self, mock_get):
        mock_get.side_effect = Exception("network error")
        posts = scrape_post_list()
        assert posts == []


# ---------------------------------------------------------------------------
# scrape_post_content (mocked HTTP)
# ---------------------------------------------------------------------------


class TestScrapePostContent:
    @patch("src.newsletter.scraper._get")
    def test_extracts_article_text(self, mock_get):
        from bs4 import BeautifulSoup
        html = """
        <html><body>
          <nav>Skip me</nav>
          <article>This is the main content. Very useful text here.</article>
          <footer>Footer noise</footer>
        </body></html>
        """
        mock_get.return_value = BeautifulSoup(html, "html.parser")
        content = scrape_post_content("https://arnoldspumpclub.com/p/test")
        assert "main content" in content
        assert "Footer noise" not in content

    @patch("src.newsletter.scraper._get")
    def test_falls_back_to_body_if_no_article(self, mock_get):
        from bs4 import BeautifulSoup
        html = "<html><body><p>Fallback content</p></body></html>"
        mock_get.return_value = BeautifulSoup(html, "html.parser")
        content = scrape_post_content("https://arnoldspumpclub.com/p/test")
        assert "Fallback content" in content


# ---------------------------------------------------------------------------
# _format_metrics
# ---------------------------------------------------------------------------


class TestFormatMetrics:
    def test_all_fields(self):
        metrics = {
            "date": date(2024, 5, 1),
            "sleep_hours": 7.5,
            "sleep_score": 80,
            "steps": 5200,
            "body_battery_high": 85,
            "body_battery_low": 20,
            "resting_heart_rate": 58,
            "avg_stress": 30,
            "active_calories": 450,
            "weight_kg": 91.5,
            "spo2_avg": 97.2,
        }
        result = _format_metrics(metrics)
        assert "7.5h" in result
        assert "5.200" in result or "5200" in result
        assert "91.5" in result
        assert "58 bpm" in result

    def test_empty_metrics(self):
        result = _format_metrics({})
        assert "Sem dados" in result

    def test_partial_metrics(self):
        result = _format_metrics({"sleep_hours": 6.0})
        assert "6.0h" in result


# ---------------------------------------------------------------------------
# analyse_daily_post (mocked Groq)
# ---------------------------------------------------------------------------


class TestAnalyseDailyPost:
    @patch("src.newsletter.analyser._get_client")
    def test_returns_llm_response(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="📰 *The Pump* — insight aqui"))]
        )
        result = analyse_daily_post(
            groq_api_key="test-key",
            post_title="Treino de força",
            post_content="Arnold diz para treinar com consistência.",
            yesterday_metrics={"steps": 4000, "sleep_hours": 7.0},
        )
        assert "insight aqui" in result
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.newsletter.analyser._get_client")
    def test_truncates_long_content(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok"))]
        )
        long_content = "x" * 10_000
        analyse_daily_post("key", "Title", long_content, {})
        call_args = mock_client.chat.completions.create.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        # Content should be truncated to 6000 chars in the prompt
        assert len(user_msg) < 9000

    @patch("src.newsletter.analyser._get_client")
    def test_propagates_groq_exception(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError, match="API down"):
            analyse_daily_post("key", "Title", "Content", {})


# ---------------------------------------------------------------------------
# Repository newsletter methods
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_repo():
    """Provide a fresh Repository backed by a temp SQLite file."""
    from src.database.repository import Repository
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    repo = Repository(db_path)
    repo.init_database()
    yield repo
    repo._engine.dispose()
    os.unlink(db_path)


class TestNewsletterRepository:
    def test_get_latest_date_empty(self, temp_repo):
        assert temp_repo.get_latest_newsletter_post_date() is None

    def test_save_and_retrieve_post(self, temp_repo):
        temp_repo.save_newsletter_post(
            url="https://arnoldspumpclub.com/p/test",
            title="Test Post",
            published_date=date(2024, 5, 1),
            content_text="Some content",
        )
        latest = temp_repo.get_latest_newsletter_post_date()
        assert latest == date(2024, 5, 1)

    def test_save_duplicate_post_is_idempotent(self, temp_repo):
        for _ in range(3):
            temp_repo.save_newsletter_post(
                url="https://arnoldspumpclub.com/p/dupe",
                title="Dupe",
                published_date=date(2024, 5, 1),
                content_text="Content",
            )
        posts = temp_repo.get_all_newsletter_posts()
        assert len(posts) == 1

    def test_save_and_get_unsent_insight(self, temp_repo):
        temp_repo.save_newsletter_insight(
            insight_pt="Hoje treina as pernas.",
            insight_type="daily",
            post_url=None,
        )
        insight = temp_repo.get_unsent_daily_insight()
        assert insight is not None
        assert "pernas" in insight.insight_pt
        assert insight.sent is False

    def test_mark_insight_sent(self, temp_repo):
        temp_repo.save_newsletter_insight(
            insight_pt="Descansa bem.",
            insight_type="daily",
        )
        insight = temp_repo.get_unsent_daily_insight()
        assert insight is not None
        temp_repo.mark_insight_sent(insight.id)
        assert temp_repo.get_unsent_daily_insight() is None

    def test_historical_insight_not_returned_as_daily(self, temp_repo):
        temp_repo.save_newsletter_insight(
            insight_pt="Resumo histórico.",
            insight_type="historical",
        )
        assert temp_repo.get_unsent_daily_insight() is None

    def test_get_all_posts_ordered_by_date(self, temp_repo):
        for d, title in [
            (date(2024, 3, 1), "Old Post"),
            (date(2024, 6, 1), "New Post"),
            (date(2024, 1, 1), "Oldest Post"),
        ]:
            temp_repo.save_newsletter_post(
                url=f"https://arnoldspumpclub.com/p/{title.lower().replace(' ', '-')}",
                title=title,
                published_date=d,
                content_text="content",
            )
        posts = temp_repo.get_all_newsletter_posts()
        dates = [p.published_date for p in posts]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# make_newsletter_job orchestration
# ---------------------------------------------------------------------------


class TestMakeNewsletterJob:
    """Tests for the make_newsletter_job orchestration logic."""

    def _make_repo(self, known_urls=None):
        """Build a mock repo with configurable known post URLs."""
        repo = MagicMock()
        known_posts = []
        for url in (known_urls or []):
            post = MagicMock()
            post.url = url
            known_posts.append(post)
        repo.get_all_newsletter_posts.return_value = known_posts
        repo.get_unsent_daily_insight.return_value = None
        repo.get_metrics_by_date.return_value = MagicMock()
        return repo

    def _make_post_meta(self, url="https://arnoldspumpclub.com/blogs/newsletter/new-post", title="New Post", published_date=None):
        from src.newsletter.scraper import PostMeta
        return PostMeta(url=url, title=title, published_date=published_date or date(2024, 6, 1))

    @patch("src.newsletter.scraper.scrape_latest_post")
    @patch("src.newsletter.scraper.scrape_post_content")
    @patch("src.newsletter.analyser.analyse_daily_post")
    def test_saves_insight_for_latest_post(self, mock_analyse, mock_scrape_content, mock_scrape_latest):
        """Job saves both the post and the insight for the latest post."""
        new_url = "https://arnoldspumpclub.com/blogs/newsletter/new-post"
        post_meta = self._make_post_meta(url=new_url, title="New Post", published_date=date(2024, 6, 1))
        repo = self._make_repo(known_urls=[])

        mock_scrape_latest.return_value = post_meta
        mock_scrape_content.return_value = "content"
        mock_analyse.return_value = "insight text"

        from src.scheduler.jobs import make_newsletter_job
        job = make_newsletter_job(repo, MagicMock(), groq_api_key="test-key")
        job()

        repo.save_newsletter_post.assert_called_once_with(
            url=new_url,
            title="New Post",
            published_date=date(2024, 6, 1),
            content_text="content",
        )
        repo.save_newsletter_insight.assert_called_once()
        call_kwargs = repo.save_newsletter_insight.call_args.kwargs
        assert call_kwargs["insight_pt"] == "insight text"
        assert call_kwargs["insight_type"] == "daily"
        assert call_kwargs["post_url"] == new_url

    @patch("src.newsletter.scraper.scrape_latest_post")
    @patch("src.newsletter.scraper.scrape_post_content")
    @patch("src.newsletter.analyser.analyse_daily_post")
    def test_raises_when_no_post_found(self, mock_analyse, mock_scrape_content, mock_scrape_latest):
        """Job raises RuntimeError when listing page returns no posts."""
        mock_scrape_latest.return_value = None
        repo = self._make_repo()

        from src.scheduler.jobs import make_newsletter_job
        job = make_newsletter_job(repo, MagicMock(), groq_api_key="test-key")

        with pytest.raises(RuntimeError, match="Nenhum artigo"):
            job()

        mock_scrape_content.assert_not_called()
        mock_analyse.assert_not_called()

    @patch("src.newsletter.scraper.scrape_latest_post")
    @patch("src.newsletter.scraper.scrape_post_content")
    @patch("src.newsletter.analyser.analyse_daily_post")
    def test_raises_on_content_scrape_failure(self, mock_analyse, mock_scrape_content, mock_scrape_latest):
        """Job raises when content scraping fails so caller can show the error."""
        mock_scrape_latest.return_value = self._make_post_meta()
        mock_scrape_content.side_effect = Exception("network error")
        repo = self._make_repo()

        from src.scheduler.jobs import make_newsletter_job
        job = make_newsletter_job(repo, MagicMock(), groq_api_key="test-key")

        with pytest.raises(Exception, match="network error"):
            job()

        mock_analyse.assert_not_called()
        repo.save_newsletter_insight.assert_not_called()

    @patch("src.newsletter.scraper.scrape_latest_post")
    @patch("src.newsletter.scraper.scrape_post_content")
    @patch("src.newsletter.analyser.analyse_daily_post")
    def test_raises_on_llm_failure(self, mock_analyse, mock_scrape_content, mock_scrape_latest):
        """Job raises when LLM analysis fails so caller can show the error."""
        mock_scrape_latest.return_value = self._make_post_meta()
        mock_scrape_content.return_value = "content"
        mock_analyse.side_effect = RuntimeError("LLM unavailable")
        repo = self._make_repo()

        from src.scheduler.jobs import make_newsletter_job
        job = make_newsletter_job(repo, MagicMock(), groq_api_key="test-key")

        with pytest.raises(RuntimeError, match="LLM unavailable"):
            job()

        repo.save_newsletter_post.assert_called_once()
        repo.save_newsletter_insight.assert_not_called()
