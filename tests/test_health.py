import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from settings_fixtures import TEST_IDENTITY_SALT

from yura_chess.adapters.alice.webhook import ALICE_WEBHOOK_PATH, LEGACY_ALICE_WEBHOOK_PATH
from yura_chess.main import _purge_retained_data, create_app
from yura_chess.presentation.social_card import SOCIAL_CARD_PATH
from yura_chess.presentation.website import (
    _COMMANDS_TITLE,
    ACCESSIBILITY_PAGE_HTML,
    ACCESSIBILITY_PATH,
    ALICE_SKILL_URL,
    BLINDFOLD_PAGE_HTML,
    BLINDFOLD_PATH,
    COACH_PAGE_HTML,
    COACH_PATH,
    COMMANDS_PAGE_HTML,
    COMMANDS_PATH,
    FAVICON_SVG,
    HOW_TO_PLAY_PAGE_HTML,
    HOW_TO_PLAY_PATH,
    INDEXNOW_KEY,
    INDEXNOW_KEY_PATH,
    LANDING_FAQ,
    LANDING_PAGE_HTML,
    LANDING_PATH,
    PUZZLES_PAGE_HTML,
    PUZZLES_PATH,
    ROBOTS_PATH,
    ROBOTS_TEXT,
    SITEMAP_ENTRIES,
    SITEMAP_PATH,
    SITEMAP_XML,
    STATISTICS_PAGE_HTML,
    STATISTICS_PATH,
    WEBMASTER_VERIFICATION_HTML,
    WEBMASTER_VERIFICATION_PATH,
    YANDEX_DIALOG_URL,
    YANDEX_REVIEW_URL,
)
from yura_chess.settings import Settings
from yura_chess.storage.usage_repository import DailyUsage, DashboardSnapshot, UsageTotals


def test_liveness_does_not_depend_on_the_database(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "yura-chess",
        "version": "0.1.0",
        "components": None,
    }


def test_public_landing_page_describes_the_skill_for_everyone(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    totals = UsageTotals(2, 1, 1, 1, 1, 1, 0, 0)
    snapshot = DashboardSnapshot(
        "real",
        "all",
        datetime(2026, 7, 23, 12, 0, 0),
        totals,
        (DailyUsage(date(2026, 7, 23), requests=2),),
    )
    monkeypatch.setattr(
        "yura_chess.main.UsageRepository.dashboard",
        lambda self, source, *, period: snapshot,
    )
    with TestClient(create_app(offline_settings)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=300"
    assert "Шахматы с Юрой" in response.text
    assert "Stockfish" in response.text
    assert "Называйте ходы обычными" in response.text
    assert "Включи режим тренера" in response.text
    assert "Настоящие шахматы в Алисе" in response.text
    assert "Продолжайте позже" in response.text
    assert "Играйте без экрана" in response.text
    assert "Как играть в шахматы с Алисой?" in response.text
    assert "Нужен ли экран, чтобы играть?" in response.text
    assert "Есть ли режим тренера и разбор партии?" in response.text
    assert "Как узнать все команды?" in response.text
    assert "Задача на мат в два хода" in response.text
    assert 'class="command-list"' in response.text
    assert response.text.index('class="site-top"') < response.text.index("<header>")
    assert response.text.count('aria-label="Разделы сайта"') == 1
    assert f'class="launch-action" href="{ALICE_SKILL_URL}"' in response.text
    assert "Запустить в браузере" in response.text
    assert 'class="launch-label">Скажите Алисе</span>' in response.text
    assert response.text.index("«Запусти навык Шахматы с Юрой»") < response.text.index('class="launch-action"')
    assert 'class="voice-demo" aria-label="Пример голосовой партии"' in response.text
    assert ".hero-support" not in response.text
    assert 'class="footer-brand"' in response.text
    assert 'class="footer-brand" href="/" aria-label=' not in response.text
    assert 'class="footer-nav" aria-label="Дополнительные страницы"' in response.text
    assert "min-height: 100vh;" in response.text
    assert "margin-top: auto;" in response.text
    assert ".footer-nav { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));" in response.text
    assert response.text.index('id="statistics-summary"') < response.text.index("Конфиденциальность")
    assert response.text.index('id="support"') < response.text.index('id="statistics-summary"')
    assert 'id="statistics"' not in response.text
    assert response.text.count('class="stats-summary-card"') == 3
    assert 'class="stats-summary-link" href="/statistics"' in response.text
    assert "Вся статистика" in response.text
    assert '<div class="stats-summary-value">1</div>' in response.text
    assert '<div class="stats-summary-label">игрок</div>' in response.text
    assert '<div class="stats-summary-label">партия с ходом</div>' in response.text
    assert '<div class="stats-summary-label">завершённых партий</div>' in response.text
    assert "Ваше мнение помогает" not in response.text
    assert "Использование навыка" not in response.text
    assert 'href="https://pay.cloudtips.ru/p/f604e20f"' in response.text
    assert response.text.count('href="https://pay.cloudtips.ru/p/f604e20f"') == 1
    assert 'rel="noopener noreferrer nofollow"' in response.text
    assert 'class="support-donation-link"' in response.text
    assert "Поддержать работу навыка" in response.text
    assert "Хотите помочь с оплатой сервера?" not in response.text
    assert f'class="support-action"\n          href="{YANDEX_REVIEW_URL}"' in response.text
    assert "Оставить отзыв в Яндексе" in response.text
    assert "Отзыв поможет другим игрокам найти навык" in response.text
    assert "а нам — понять, что сделать лучше" in response.text
    assert '<link rel="icon" href="/favicon.svg"' in response.text
    assert '<link rel="canonical" href="https://yurachess.ru/">' in response.text
    assert '<meta property="og:type" content="website">' in response.text
    assert '<script type="application/ld+json">' in response.text
    assert "IntersectionObserver" in response.text
    assert "chart.scrollWidth - chart.clientWidth" in response.text
    assert "current.replaceWith(replacement)" in response.text
    assert 'history.pushState({ statistics: true }, "", url)' in response.text
    assert "window.scrollTo({ top: scrollPosition })" in response.text
    assert 'window.addEventListener("popstate"' in response.text
    assert "prefers-reduced-motion: reduce" in response.text
    assert "Как играть в голосовые шахматы с Алисой" in response.text
    structured_data = response.text.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
    graph = json.loads(structured_data)["@graph"]
    assert {item["@type"] for item in graph} == {"WebSite", "SoftwareApplication", "FAQPage"}
    skill = next(item for item in graph if item["@type"] == "SoftwareApplication")
    assert skill["installUrl"] == ALICE_SKILL_URL
    assert skill["sameAs"] == [YANDEX_DIALOG_URL]
    faq = next(item for item in graph if item["@type"] == "FAQPage")
    assert [item["name"] for item in faq["mainEntity"]] == [question for question, _ in LANDING_FAQ]
    # A FAQ rich result is dropped when the marked-up answer is not on the page.
    visible_text = re.sub(r"<[^>]+>", "", response.text)
    for question, answer in LANDING_FAQ:
        assert question in response.text
        assert answer in visible_text
    assert "незряч" in response.text.lower()
    for path in (
        HOW_TO_PLAY_PATH,
        COMMANDS_PATH,
        COACH_PATH,
        PUZZLES_PATH,
        ACCESSIBILITY_PATH,
        BLINDFOLD_PATH,
        STATISTICS_PATH,
    ):
        assert f'href="{path}"' in response.text
    # The compact summary does not bring the full dashboard controls onto the landing page.
    assert "Что значит «пользователь»?" not in response.text
    assert 'class="stats-hint"' not in response.text
    assert 'class="stats-chart" aria-hidden="true"' not in response.text
    assert 'class="stats-table visually-hidden"' not in response.text
    assert "Автоматические проверки" not in response.text
    # The card, not the inline word, positions the tooltip: an inline anchor both
    # mispositions the panel and traps its z-index inside a sibling stacking context.
    assert ".stats-card { position: relative;" in response.text
    assert '.stats-card:has(.stats-hint[aria-expanded="true"]) { z-index: 4; }' in response.text
    # Default placement is under the card so the counter it explains stays readable.
    assert "top: calc(100% + 12px);" in response.text
    assert ".stats-card.tip-above .stats-tip {" in response.text
    assert 'card.classList.add("tip-above")' in response.text
    # Escape closes the panel without taking focus away from the trigger.
    assert "openHints().forEach(closeTooltip)" in response.text
    # The reveal must not depend on linear() being supported, or the page is blank.
    assert ".has-motion .motion-item.is-visible { opacity: 1; filter: none; transform: none; }" in response.text
    assert "@supports (animation-timing-function: linear(0, 1))" in response.text
    # The fallback above is already the final state, so the animation needs an
    # explicit start frame instead of inheriting that same state at both ends.
    assert "from { opacity: 0; filter: blur(4px); transform: translateY(28px) scale(.985); }" in response.text
    # Secondary pages reveal a heading with its content as one readable unit,
    # rather than animating every paragraph independently.
    assert 'element.matches("h2, h3")' in response.text
    assert "const revealGroups = new WeakMap();" in response.text
    assert 'targets.forEach((target) => target.classList.add("is-visible"))' in response.text
    # A stale period response must not overwrite a newer one, nor navigate away
    # from it when the stale request is the one that failed.
    assert response.text.count("if (request !== statisticsRequest) return;") == 2
    assert (
        """if (request !== statisticsRequest) return;
          window.location.assign(url);"""
        in response.text
    )
    # Sharing a link should unfurl into something.
    assert f'<meta property="og:image" content="https://yurachess.ru{SOCIAL_CARD_PATH}">' in response.text
    assert '<meta name="twitter:card" content="summary_large_image">' in response.text


def test_landing_snippet_uses_the_intent_aligned_description_everywhere(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    description = (
        "Играйте в шахматы с Алисой бесплатно и без экрана. Скажите: «Алиса, запусти навык "
        "Шахматы с Юрой» — 20 уровней, тренер и задачи."
    )
    monkeypatch.setattr(
        "yura_chess.main.UsageRepository.dashboard",
        lambda self, source, *, period: DashboardSnapshot(
            "real", "all", datetime(2026, 7, 23, 12, 0, 0), UsageTotals(2, 1, 1, 1, 1, 1, 0, 0), ()
        ),
    )
    with TestClient(create_app(offline_settings)) as client:
        response = client.get("/")

    assert f'<meta name="description" content="{description}">' in response.text
    assert f'<meta property="og:description" content="{description}">' in response.text
    assert f'<meta name="twitter:description" content="{description}">' in response.text


def test_mandatory_faq_questions_are_each_pinned_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    required_questions = (
        "Как играть в шахматы с Алисой?",
        "Умеет ли Алиса играть в шахматы?",
    )
    faq_questions = [question for question, _ in LANDING_FAQ]
    for required in required_questions:
        assert faq_questions.count(required) == 1

    monkeypatch.setattr(
        "yura_chess.main.UsageRepository.dashboard",
        lambda self, source, *, period: DashboardSnapshot(
            "real", "all", datetime(2026, 7, 23, 12, 0, 0), UsageTotals(2, 1, 1, 1, 1, 1, 0, 0), ()
        ),
    )
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(LANDING_PATH)

    structured_data = response.text.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
    visible_html = re.sub(r'<script type="application/ld\+json">.*?</script>', "", response.text, flags=re.S)
    graph = json.loads(structured_data)["@graph"]
    faq = next(item for item in graph if item["@type"] == "FAQPage")
    schema_names = [item["name"] for item in faq["mainEntity"]]
    for required in required_questions:
        assert visible_html.count(required) == 1
        assert schema_names.count(required) == 1


_COMMANDS_CATALOGUE_SECTIONS = (
    "Ходы",
    "Позиция",
    "Факты о партии",
    "Управление партией",
    "Настройки речи и доски",
    "Режим тренера",
    "Разбор сыгранной партии",
    "Шахматные задачи",
    "Если Алиса не расслышала",
    "Справка голосом",
)
_COMMANDS_CATALOGUE_ITEM_COUNT = 31


def test_commands_h1_matches_structured_data_and_keeps_the_full_list(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(COMMANDS_PATH)

    assert _COMMANDS_TITLE == "Голосовые команды для шахмат с Алисой"
    assert f"<h1>{_COMMANDS_TITLE}</h1>" in response.text
    # The detailed command catalogue and the "no need to memorise exact wording" promise must survive.
    assert "навык понимает разные формулировки" in response.text
    assert "«включи режим тренера»" in response.text
    assert "«говори кратко»" in response.text
    # Snapshot guard: dropping a whole section would otherwise leave this test green.
    assert re.findall(r"<h2>(.*?)</h2>", response.text) == list(_COMMANDS_CATALOGUE_SECTIONS)
    assert len(re.findall(r"<li>.*?</li>", response.text, re.S)) == _COMMANDS_CATALOGUE_ITEM_COUNT
    structured_data = response.text.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
    graph = json.loads(structured_data)["@graph"]
    page = next(item for item in graph if item["@type"] == "WebPage")
    breadcrumb = next(item for item in graph if item["@type"] == "BreadcrumbList")
    assert page["name"] == _COMMANDS_TITLE
    assert breadcrumb["itemListElement"][-1]["name"] == _COMMANDS_TITLE


def test_sitemap_lists_exactly_the_eight_canonical_pages_without_lastmod(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(SITEMAP_PATH)

    assert len(SITEMAP_ENTRIES) == 8
    assert response.text.count("<url>") == 8
    assert response.text.count("<loc>") == 8
    assert "<lastmod>" not in response.text
    for path, _ in SITEMAP_ENTRIES:
        assert f"<loc>https://yurachess.ru{path}</loc>" in response.text


def test_all_eight_canonical_pages_have_unique_title_and_description() -> None:
    pages_by_path = {
        LANDING_PATH: LANDING_PAGE_HTML,
        STATISTICS_PATH: STATISTICS_PAGE_HTML,
        HOW_TO_PLAY_PATH: HOW_TO_PLAY_PAGE_HTML,
        COMMANDS_PATH: COMMANDS_PAGE_HTML,
        ACCESSIBILITY_PATH: ACCESSIBILITY_PAGE_HTML,
        BLINDFOLD_PATH: BLINDFOLD_PAGE_HTML,
        COACH_PATH: COACH_PAGE_HTML,
        PUZZLES_PATH: PUZZLES_PAGE_HTML,
    }
    assert set(pages_by_path) == {path for path, _ in SITEMAP_ENTRIES}
    assert len(pages_by_path) == 8

    titles = []
    descriptions = []
    for path, page_html in pages_by_path.items():
        title_match = re.search(r"<title>(.*?)</title>", page_html)
        description_match = re.search(r'<meta name="description" content="(.*?)">', page_html)
        assert title_match, f"missing <title> for {path}"
        assert description_match, f"missing meta description for {path}"
        titles.append(title_match.group(1))
        descriptions.append(description_match.group(1))

    assert len(set(titles)) == 8
    assert len(set(descriptions)) == 8


_TV_LONG_MARKERS = ("яндекс тв", "телевизор", "smart tv", "смарт-тв")
_TV_TOKEN_PATTERN = re.compile(r"\bтв\b|\btv\b")


def _assert_makes_no_tv_claim(text: str) -> None:
    normalized = text.casefold()
    for marker in _TV_LONG_MARKERS:
        assert marker not in normalized
    assert _TV_TOKEN_PATTERN.search(normalized) is None


def test_no_yandex_tv_claim_is_made_without_a_verified_device_check() -> None:
    for question, answer in LANDING_FAQ:
        _assert_makes_no_tv_claim(question)
        _assert_makes_no_tv_claim(answer)
    for page_html in (
        LANDING_PAGE_HTML,
        STATISTICS_PAGE_HTML,
        HOW_TO_PLAY_PAGE_HTML,
        COMMANDS_PAGE_HTML,
        COACH_PAGE_HTML,
        PUZZLES_PAGE_HTML,
        ACCESSIBILITY_PAGE_HTML,
        BLINDFOLD_PAGE_HTML,
    ):
        _assert_makes_no_tv_claim(page_html)


def test_yandex_webmaster_verification_file_is_served_verbatim(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(WEBMASTER_VERIFICATION_PATH)

    assert response.status_code == 200
    assert response.text == WEBMASTER_VERIFICATION_HTML


def test_search_engine_discovery_files_are_public_and_cacheable(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        robots = client.get(ROBOTS_PATH)
        sitemap = client.get(SITEMAP_PATH)

    assert robots.status_code == sitemap.status_code == 200
    assert robots.text == ROBOTS_TEXT
    assert "text/plain" in robots.headers["content-type"]
    assert "Clean-param: source&period /" in robots.text
    assert sitemap.text == SITEMAP_XML
    assert "application/xml" in sitemap.headers["content-type"]
    assert "https://yurachess.ru/" in sitemap.text
    for path, _ in SITEMAP_ENTRIES:
        assert f"<loc>https://yurachess.ru{path}</loc>" in sitemap.text


def test_a_secondary_page_revalidates_instead_of_serving_a_stale_release(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        first = client.get(COMMANDS_PATH)
        revalidated = client.get(COMMANDS_PATH, headers={"If-None-Match": first.headers["etag"]})
        changed = client.get(COMMANDS_PATH, headers={"If-None-Match": '"stale0000000000"'})

    assert revalidated.status_code == 304
    assert revalidated.headers["etag"] == first.headers["etag"]
    assert changed.status_code == 200
    assert "Голосовые команды для шахмат с Алисой" in changed.text


def test_both_webhook_paths_answer_so_the_console_never_races_a_deploy(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        canonical = client.post(ALICE_WEBHOOK_PATH, json={})
        legacy = client.post(LEGACY_ALICE_WEBHOOK_PATH, json={})
        robots = client.get(ROBOTS_PATH)

    # 422 is the validating endpoint rejecting an empty body; 404 would mean it is gone.
    assert canonical.status_code == legacy.status_code == 422
    assert "Disallow: /webhooks/" in robots.text
    assert "Disallow: /alice/" in robots.text


def test_social_card_is_served_for_link_previews(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(SOCIAL_CARD_PATH)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_statistics_expose_the_chart_data_as_a_table(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    """The audience this site is built for cannot read a bar chart."""
    snapshot = DashboardSnapshot(
        "real",
        "month",
        datetime(2026, 7, 23, 12, 0, 0),
        UsageTotals(2, 1, 1, 1, 1, 1, 0, 0),
        (
            DailyUsage(date(2026, 7, 22), engaged_games=7),
            DailyUsage(date(2026, 7, 23), engaged_games=9),
        ),
    )
    monkeypatch.setattr(
        "yura_chess.main.UsageRepository.dashboard",
        lambda self, source, *, period: snapshot,
    )
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(STATISTICS_PATH)

    assert '<th scope="row">22.07.2026</th><td>7</td>' in response.text
    assert '<th scope="row">23.07.2026</th><td>9</td>' in response.text
    # The decorative bars must not also be announced.
    assert 'class="stats-chart" aria-hidden="true"' in response.text
    assert 'role="img"' not in response.text
    assert "<h1>Статистика навыка</h1>" in response.text
    assert "<h2>Статистика</h2>" not in response.text
    assert '<a href="/statistics" aria-current="page">Статистика</a>' in response.text
    assert 'aria-label="Подробная статистика"' in response.text
    assert "Что значит «пользователь»?" in response.text
    assert '<button type="button" class="stats-hint"' in response.text
    assert 'aria-expanded="false"' in response.text
    assert 'class="stats-value-shown" aria-hidden="true"' in response.text
    assert '<span class="visually-hidden">' in response.text
    assert '<div class="visually-hidden"><table class="stats-table">' in response.text
    assert 'class="stats-table visually-hidden"' not in response.text
    assert '<th scope="col">Партий с ходом</th>' in response.text
    assert 'href="/statistics?period=year&amp;metric=engaged_games#statistics"' in response.text
    assert 'action="/statistics#statistics"' in response.text


def test_indexnow_key_is_served_so_submissions_are_accepted(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(INDEXNOW_KEY_PATH)

    assert response.status_code == 200
    assert response.text == INDEXNOW_KEY
    assert "text/plain" in response.headers["content-type"]


@pytest.mark.parametrize(
    ("path", "title", "marker"),
    [
        (HOW_TO_PLAY_PATH, "Как играть в шахматы с Алисой голосом", "Уровень Stockfish — от нуля"),
        (COMMANDS_PATH, "Голосовые команды для шахмат с Алисой", "«повтори координаты по буквам»"),
        (COACH_PATH, "Шахматный тренер голосом", "Подсказки: от намёка к ходу"),
        (PUZZLES_PATH, "Шахматные задачи голосом", "Мат по последней горизонтали"),
        (ACCESSIBILITY_PATH, "Шахматы для незрячих голосом", "Тренер и задачи тоже без экрана"),
        (BLINDFOLD_PATH, "Шахматы вслепую с Алисой", "Как наращивать сложность"),
    ],
)
def test_secondary_pages_are_crawlable_and_self_describing(
    offline_settings: Settings,
    path: str,
    title: str,
    marker: str,
) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert title in response.text
    assert marker in response.text
    # Without a validator a reader keeps the previous release for the whole max-age.
    assert response.headers["cache-control"] == "public, max-age=300, stale-while-revalidate=3600"
    assert response.headers["etag"]
    assert f'<link rel="canonical" href="https://yurachess.ru{path}">' in response.text
    # Every secondary page must lead back to the others, or a crawler reaches none of them.
    every_page = {
        LANDING_PATH,
        HOW_TO_PLAY_PATH,
        COMMANDS_PATH,
        COACH_PATH,
        PUZZLES_PATH,
        ACCESSIBILITY_PATH,
        BLINDFOLD_PATH,
        STATISTICS_PATH,
    }
    for linked in every_page - {path}:
        assert f'href="{linked}"' in response.text
    # Getting home must not depend on the browser's back button.
    assert 'class="piece home" href="/"' in response.text
    assert f'<a href="{path}" aria-current="page">' in response.text
    assert response.text.index('class="site-top"') < response.text.index("<header>")
    assert response.text.count('aria-label="Разделы сайта"') == 1
    assert response.text.count('class="footer-brand"') == 1
    assert response.text.count('class="footer-nav"') == 1
    assert response.text.count(f'class="launch-action" href="{ALICE_SKILL_URL}"') == 1
    assert response.text.count('class="launch-command">«Запусти навык Шахматы с Юрой»') == 1
    assert "Запустить в браузере" in response.text
    # The trail belongs in the search result, not above the hero.
    assert 'class="breadcrumbs"' not in response.text
    structured_data = response.text.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0]
    graph = json.loads(structured_data)["@graph"]
    assert "BreadcrumbList" in {item["@type"] for item in graph}
    # The full dashboard belongs to its own dynamic page.
    assert 'id="statistics"' not in response.text


def test_public_pages_keep_internal_qa_language_out_of_player_copy() -> None:
    pages = (
        LANDING_PAGE_HTML,
        STATISTICS_PAGE_HTML,
        HOW_TO_PLAY_PAGE_HTML,
        COMMANDS_PAGE_HTML,
        COACH_PAGE_HTML,
        PUZZLES_PAGE_HTML,
        ACCESSIBILITY_PAGE_HTML,
        BLINDFOLD_PAGE_HTML,
    )
    combined = "\n".join(pages)

    for internal_phrase in (
        "ступенчат",
        "Конев три",
        "Разбор реального цикла",
        "последовательность запрос — согласие",
        "HMAC-ключ",
        "Это уже не честная партия",
        "партия ещё честная",
        "с привычными шахматными фразами",
        "вместо него используется необратимый код",
        "уровнями силы",
        "Ещё не запускали",
    ):
        assert internal_phrase not in combined
    assert f'href="{YANDEX_DIALOG_URL}#surfaces"' in HOW_TO_PLAY_PAGE_HTML
    assert "Скажите <code>«Алиса, запусти навык Шахматы с Юрой»</code>" in HOW_TO_PLAY_PAGE_HTML
    assert "по запросу <code>«все команды»</code>" in COMMANDS_PAGE_HTML
    assert "Команда <code>«какая позиция»</code>" in ACCESSIBILITY_PAGE_HTML
    assert "<code>«оцени позицию»</code>" in COACH_PAGE_HTML
    assert "<code>«сколько я сделал ходов»</code>" in BLINDFOLD_PAGE_HTML
    assert "<code>«дай задачу»</code>" in PUZZLES_PAGE_HTML
    assert ".hero-actions .launch:only-child" in LANDING_PAGE_HTML
    assert "border-left: 2px solid var(--gold)" in LANDING_PAGE_HTML
    assert ".launch-command { display: block; color: var(--gold); font-weight: 700; }" in LANDING_PAGE_HTML
    assert "code { color: var(--gold); font: inherit; font-weight: 400; }" in LANDING_PAGE_HTML
    assert "<strong><code>" not in combined


def test_favicon_is_served_for_modern_and_legacy_browser_paths(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        svg = client.get("/favicon.svg")
        ico = client.get("/favicon.ico")
        head = client.head("/favicon.svg")

    assert svg.status_code == ico.status_code == head.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.headers["cache-control"] == "public, max-age=86400"
    assert svg.text == ico.text == FAVICON_SVG


def test_public_statistics_pages_use_real_traffic_and_accept_period_filters(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    queries: list[tuple[str, str]] = []
    totals = UsageTotals(2, 1, 1, 1, 1, 1, 0, 0)
    snapshot = DashboardSnapshot(
        "real",
        "month",
        datetime(2026, 7, 23, 12, 0, 0),
        totals,
        (DailyUsage(date(2026, 7, 23), requests=2),),
    )

    @contextmanager
    def fake_session_scope(session_factory: object) -> Iterator[object]:
        yield object()

    class Repository:
        def __init__(self, session: object) -> None:
            return None

        def dashboard(self, source: str, *, period: str) -> DashboardSnapshot:
            queries.append((source, period))
            return snapshot

    monkeypatch.setattr("yura_chess.main.session_scope", fake_session_scope)
    monkeypatch.setattr("yura_chess.main.UsageRepository", Repository)
    with TestClient(create_app(offline_settings)) as client:
        default = client.get("/")
        landing_with_noise = client.get("/?source=test&period=year")
        head = client.head("/")
        statistics = client.get(STATISTICS_PATH)
        year = client.get(f"{STATISTICS_PATH}?source=test&period=year")
        invalid = client.get(f"{STATISTICS_PATH}?source=private")
        invalid_period = client.get(f"{STATISTICS_PATH}?period=week")
        removed_dashboard = client.get("/dashboard")

    assert default.status_code == landing_with_noise.status_code == head.status_code == 200
    assert statistics.status_code == year.status_code == 200
    assert default.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=300"
    # The landing summary is all-time; the detailed page owns period filtering.
    assert queries == [("real", "all"), ("real", "month"), ("real", "year")]
    assert '<link rel="canonical" href="https://yurachess.ru/">' in landing_with_noise.text
    assert '<link rel="canonical" href="https://yurachess.ru/statistics">' in year.text
    assert invalid.status_code == 200
    assert invalid_period.status_code == 422
    assert removed_dashboard.status_code == 404


def test_statistics_metrics_share_one_query_per_period(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    queries: list[tuple[str, str]] = []
    totals = UsageTotals(2, 1, 1, 1, 1, 1, 0, 0)

    @contextmanager
    def fake_session_scope(session_factory: object) -> Iterator[object]:
        yield object()

    class Repository:
        def __init__(self, session: object) -> None:
            return None

        def dashboard(self, source: str, *, period: str) -> DashboardSnapshot:
            queries.append((source, period))
            return DashboardSnapshot(
                "real",
                period,  # type: ignore[arg-type]
                datetime(2026, 7, 23, 12, 0, 0),
                totals,
                (DailyUsage(date(2026, 7, 23), requests=2),),
            )

    monkeypatch.setattr("yura_chess.main.session_scope", fake_session_scope)
    monkeypatch.setattr("yura_chess.main.UsageRepository", Repository)
    metrics = ("engaged_games", "player_moves", "users")
    with TestClient(create_app(offline_settings)) as client:
        responses = [
            client.get(f"{STATISTICS_PATH}?period={period}&metric={metric}")
            for period in ("month", "year", "all")
            for metric in metrics
        ]

    assert [response.status_code for response in responses] == [200] * 9
    # The metric picks a column out of the snapshot; only the period reaches the database.
    assert queries == [("real", "month"), ("real", "year"), ("real", "all")]


def test_analytics_failure_serves_the_pages_without_their_counters(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    @contextmanager
    def fake_session_scope(session_factory: object) -> Iterator[object]:
        yield object()

    class BrokenRepository:
        def __init__(self, session: object) -> None:
            return None

        def dashboard(self, source: str, *, period: str) -> DashboardSnapshot:
            raise RuntimeError("database is unreachable")

    monkeypatch.setattr("yura_chess.main.session_scope", fake_session_scope)
    monkeypatch.setattr("yura_chess.main.UsageRepository", BrokenRepository)
    with TestClient(create_app(offline_settings)) as client:
        landing = client.get("/")
        statistics = client.get(STATISTICS_PATH)

    assert landing.status_code == statistics.status_code == 200
    assert "Статистика временно недоступна" in landing.text
    assert "Статистика временно недоступна" in statistics.text
    # The pages carry the content search engines index; only the counters are missing.
    assert ALICE_SKILL_URL in landing.text
    assert landing.headers["cache-control"] == statistics.headers["cache-control"] == "no-store"


def test_analytics_failure_keeps_serving_the_last_good_counters(
    monkeypatch: pytest.MonkeyPatch,
    offline_settings: Settings,
) -> None:
    failing = False
    totals = UsageTotals(2, 1, 1, 1, 1, 1, 0, 0)
    snapshot = DashboardSnapshot(
        "real",
        "all",
        datetime(2026, 7, 23, 12, 0, 0),
        totals,
        (DailyUsage(date(2026, 7, 23), requests=2),),
    )

    @contextmanager
    def fake_session_scope(session_factory: object) -> Iterator[object]:
        yield object()

    class FlakyRepository:
        def __init__(self, session: object) -> None:
            return None

        def dashboard(self, source: str, *, period: str) -> DashboardSnapshot:
            if failing:
                raise RuntimeError("database is unreachable")
            return snapshot

    monkeypatch.setattr("yura_chess.main.session_scope", fake_session_scope)
    monkeypatch.setattr("yura_chess.main.UsageRepository", FlakyRepository)
    monkeypatch.setattr("yura_chess.main.DASHBOARD_CACHE_SECONDS", 0.0)
    with TestClient(create_app(offline_settings)) as client:
        warm = client.get("/")
        failing = True
        degraded = client.get("/")

    assert warm.status_code == degraded.status_code == 200
    assert "Статистика временно недоступна" not in degraded.text
    assert "stats-summary-value" in degraded.text


def test_readiness_reports_an_unreachable_database(offline_settings: Settings) -> None:
    with TestClient(create_app(offline_settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["http"] == "ready"
    assert body["components"]["database"].startswith("unavailable")
    assert body["components"]["engine"].endswith("workers")


def test_readiness_counts_ready_engine_workers_without_searching(offline_settings: Settings) -> None:
    searches = 0

    class NeverSearchedProcess:
        def best_move(self, board: object, search_time: float) -> str:
            nonlocal searches
            searches += 1
            return "e2e4"

        def close(self) -> None:
            return None

    app = create_app(offline_settings)
    app.state.engine_process_factory = NeverSearchedProcess
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.json()["components"]["engine"] == "ready: 2/2 workers"
    assert searches == 0


def test_periodic_maintenance_includes_remote_board_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    @contextmanager
    def fake_session_scope(session_factory: object) -> Iterator[object]:
        yield object()

    class TranscriptRepository:
        def __init__(self, session: object) -> None:
            return None

        def purge_expired(self, now: object, retention_days: int) -> None:
            calls.append("transcripts")

    class GameRepository:
        def __init__(self, session: object) -> None:
            return None

        def purge_request_replays(self, now: object, retention_days: int) -> None:
            calls.append("replays")

        def purge_test_games(self, now: object, retention_days: int) -> None:
            calls.append("test-games")

    class AnalysisRepository:
        def __init__(self, session: object) -> None:
            return None

        def purge_expired(self, now: object, retention_days: int) -> None:
            calls.append("analysis")

    class ReviewRepository:
        def __init__(self, session: object) -> None:
            return None

        def purge_expired(self, now: object, retention_days: int) -> None:
            calls.append("reviews")

    monkeypatch.setattr("yura_chess.main.session_scope", fake_session_scope)
    monkeypatch.setattr("yura_chess.main.TranscriptRepository", TranscriptRepository)
    monkeypatch.setattr("yura_chess.main.GameRepository", GameRepository)
    monkeypatch.setattr("yura_chess.main.AnalysisRepository", AnalysisRepository)
    monkeypatch.setattr("yura_chess.main.ReviewRepository", ReviewRepository)
    app = SimpleNamespace(
        state=SimpleNamespace(
            session_factory=object(),
            settings=SimpleNamespace(
                asr_transcript_retention_days=30,
                request_replay_retention_days=7,
                test_game_retention_days=7,
                analysis_checkpoint_retention_days=180,
                review_state_retention_days=30,
            ),
            board_images=SimpleNamespace(maintain_cache=lambda: calls.append("images")),
        )
    )

    _purge_retained_data(app)

    assert calls == ["transcripts", "replays", "test-games", "analysis", "reviews", "images"]


def test_a_missing_stockfish_binary_does_not_block_startup(offline_settings: Settings) -> None:
    app = create_app(offline_settings.model_copy(update={"stockfish_path": Path("/nonexistent/stockfish")}))
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.json()["components"]["engine"] == "degraded: 0/2 workers"


class _StubProcess:
    """Stands in for a live Stockfish so readiness does not need a host binary."""

    def best_move(self, board: object, search_time: float) -> str:
        return "e2e4"

    def close(self) -> None:
        return None


def _migrated_settings() -> Settings:
    dsn = os.environ.get("YURA_CHESS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("YURA_CHESS_TEST_DATABASE_URL is not set; readiness needs a migrated MariaDB")
    return Settings(environment="test", database_url=dsn, identity_salt=TEST_IDENTITY_SALT)  # type: ignore[arg-type]


def test_readiness_is_green_against_a_migrated_database() -> None:
    app = create_app(_migrated_settings())
    app.state.engine_process_factory = _StubProcess
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    components = response.json()["components"]
    assert components["http"] == "ready"
    assert components["database"] == "ready"
    assert components["engine"].startswith("ready")


def test_readiness_fails_when_the_database_is_up_but_no_engine_worker_is() -> None:
    settings = _migrated_settings().model_copy(update={"stockfish_path": Path("/nonexistent/stockfish")})
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    components = response.json()["components"]
    assert components["database"] == "ready"
    assert components["engine"] == "degraded: 0/2 workers"
