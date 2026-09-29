from __future__ import annotations

import json
import re

import pytest

from yura_chess.presentation import website


@pytest.mark.parametrize(
    "html",
    [
        website.LANDING_PAGE_HTML,
        website.HOW_TO_PLAY_PAGE_HTML,
        website.COMMANDS_PAGE_HTML,
        website.COACH_PAGE_HTML,
        website.PUZZLES_PAGE_HTML,
        website.ACCESSIBILITY_PAGE_HTML,
        website.BLINDFOLD_PAGE_HTML,
        website.STATISTICS_PAGE_HTML,
        website.REVIEWS_PAGE_HTML,
    ],
)
def test_every_public_page_has_a_review_link_in_header_and_footer(html: str) -> None:
    navs = re.findall(r"<nav\b[^>]*>(.*?)</nav>", html, re.DOTALL)

    assert len(navs) == 2
    assert all('href="/reviews"' in nav and "Оставить отзыв" in nav for nav in navs)


def test_review_guide_provides_the_steps_without_redirecting_readers_to_itself() -> None:
    html = website.REVIEWS_PAGE_HTML
    skill_section = html.split('aria-labelledby="skill-review-heading">', 1)[1].split("</section>", 1)[0]

    assert '<a href="/reviews" aria-current="page">Оставить отзыв</a>' in html
    assert 'href="/reviews/dialogs?source=guide"' in html
    assert "Яндекс ID" in skill_section
    assert "Оцените “Шахматы с Юрой”" in skill_section
    assert "звёзд" in skill_section
    assert "напишите впечатления об игре и отправьте отзыв" in skill_section
    assert "Если вы играете на Станции" not in skill_section
    assert "Эта инструкция находится по адресу" not in skill_section


def test_browser_review_guide_keeps_website_reviews_separate_from_skill_ratings() -> None:
    html = website.REVIEWS_PAGE_HTML
    browser_section = html.split('aria-labelledby="website-review-heading">', 1)[1].split("</section>", 1)[0]

    assert "не увеличивают число оценок навыка" in browser_section
    assert "на компьютере" in browser_section
    assert "«Нет отзывов»" in browser_section
    assert "«Нейропротект»" in browser_section
    assert "«Отправить»" in browser_section
    assert 'href="https://browser.yandex.ru/help/ru/recommendation/review"' in browser_section


def test_review_guide_is_canonical_indexable_and_has_matching_structured_data() -> None:
    html = website.REVIEWS_PAGE_HTML
    canonical = "https://yurachess.ru/reviews"

    assert f'<link rel="canonical" href="{canonical}">' in html
    assert '<meta name="robots" content="index,follow' in html
    assert f"<loc>{canonical}</loc>" in website.SITEMAP_XML
    graph = json.loads(html.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0])["@graph"]
    page = next(node for node in graph if node["@type"] == "WebPage")
    assert page["url"] == canonical


def test_landing_review_cta_tracks_clicks_and_offers_instructions() -> None:
    support = website.LANDING_PAGE_HTML.split('id="support"', 1)[1].split("</section>", 1)[0]

    assert 'href="/reviews/dialogs?source=landing"' in support
    assert 'href="/reviews"' in support
    assert "Где и как написать отзыв" in support


def test_voice_review_and_dismissal_commands_are_discoverable() -> None:
    assert "«Где оставить отзыв?»" in website.COMMANDS_PAGE_HTML
    assert "«Не напоминай мне об отзывах»" in website.COMMANDS_PAGE_HTML
