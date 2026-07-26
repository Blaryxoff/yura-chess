"""Public website shown to players, moderators and Yandex Webmaster."""

from __future__ import annotations

import json
from typing import Any

from yura_chess.presentation.dashboard import DASHBOARD_CSS
from yura_chess.presentation.social_card import CARD_HEIGHT, CARD_WIDTH, SOCIAL_CARD_ALT, SOCIAL_CARD_PATH

# One node of the schema.org graph embedded in every page.
Schema = dict[str, Any]

WEBMASTER_VERIFICATION_PATH = "/yandex_67cb474818f8d2b2.html"
# IndexNow proves host control by serving the key from the site itself, which lets
# Yandex and Bing be told about a change instead of waiting for their next crawl.
# The key is a public identifier, not a credential.
INDEXNOW_KEY = "3e123263cd3a154a8aa32da5bc28cebd"
INDEXNOW_KEY_PATH = f"/{INDEXNOW_KEY}.txt"
ROBOTS_PATH = "/robots.txt"
SITEMAP_PATH = "/sitemap.xml"
PUBLIC_SITE_URL = "https://yurachess.ru/"
ALICE_SKILL_URL = "https://alice.yandex.ru/skill/1778b5e3-d1d2-487e-bcb2-cb335c1e0e5d/"
YANDEX_DIALOG_URL = "https://dialogs.yandex.ru/store/skills/9ec272d2-shahmaty-s-yuroj"
YANDEX_REVIEW_URL = f"{YANDEX_DIALOG_URL}#ratings"
LANDING_PATH = "/"
HOW_TO_PLAY_PATH = "/how-to-play"
COMMANDS_PATH = "/commands"
COACH_PATH = "/coach"
PUZZLES_PATH = "/puzzles"
ACCESSIBILITY_PATH = "/accessibility"
BLINDFOLD_PATH = "/blindfold"
FAVICON_PATH = "/favicon.svg"
FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="15" fill="#171a17"/>
  <circle cx="32" cy="32" r="24" fill="#9bd36a"/>
  <text x="32" y="45" text-anchor="middle" font-family="Georgia,serif" font-size="40" fill="#121412">♞</text>
</svg>"""
WEBMASTER_VERIFICATION_HTML = """<html>
    <head>
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
    </head>
    <body>Verification: 67cb474818f8d2b2</body>
</html>"""
ROBOTS_TEXT = f"""User-agent: *
Allow: /
Disallow: /alice/
Disallow: /webhooks/
Disallow: /health/
Clean-param: source&period /
Sitemap: {PUBLIC_SITE_URL}sitemap.xml
"""

# Every indexable page, with the weight given to it relative to the landing page.
SITEMAP_ENTRIES: tuple[tuple[str, str], ...] = (
    (LANDING_PATH, "1.0"),
    (HOW_TO_PLAY_PATH, "0.8"),
    (COMMANDS_PATH, "0.8"),
    (ACCESSIBILITY_PATH, "0.8"),
    (BLINDFOLD_PATH, "0.7"),
    (COACH_PATH, "0.7"),
    (PUZZLES_PATH, "0.7"),
)
SITEMAP_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    + "".join(
        f"""  <url>
    <loc>{PUBLIC_SITE_URL.rstrip("/")}{path}</loc>
    <changefreq>weekly</changefreq>
    <priority>{priority}</priority>
  </url>\n"""
        for path, priority in SITEMAP_ENTRIES
    )
    + "</urlset>\n"
)

SITE_CSS = (
    """
    :root {
      color-scheme: dark;
      --bg: #171613;
      --panel: #24221e;
      --text: #f6f0e3;
      --muted: #c9c0ae;
      --gold: #e8bd66;
      --line: #3c3830;
    }
    * { box-sizing: border-box; }
    html { background: var(--bg); }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 20% 0%, #3a2f1f 0, transparent 36rem),
        var(--bg);
      color: var(--text);
      font: 17px/1.6 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main { width: min(1080px, calc(100% - 32px)); margin: 0 auto; }
    .site-top { display: flex; justify-content: center; padding-top: 18px; }
    .site-nav {
      display: inline-flex;
      justify-content: center;
      flex-wrap: wrap;
      gap: 4px;
      margin: 0;
      padding: 5px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #1d1c19e8;
      box-shadow: 0 12px 34px #00000026;
    }
    .site-nav a {
      padding: 7px 11px;
      border: 1px solid transparent;
      border-radius: 11px;
      color: var(--muted);
      font-size: 15px;
      font-weight: 700;
      text-decoration: none;
      transition: border-color 180ms ease, background 180ms ease, color 180ms ease;
    }
    .site-nav a:hover { border-color: #5a513f; background: #29261f; color: var(--text); }
    .site-nav a:focus-visible { outline: 2px solid var(--gold); outline-offset: 2px; }
    .site-nav a[aria-current="page"] {
      border-color: #e8bd6659;
      background: linear-gradient(135deg, #e8bd662e, #e8bd6614);
      color: var(--text);
    }
    /* Tied to viewport height so the hero never fills a short screen on its own:
       the next section has to peek above the fold, or nothing invites a scroll. */
    header { padding: clamp(28px, 5vh, 52px) 0 clamp(28px, 5vh, 52px); text-align: center; }
    .piece { color: var(--gold); font-size: clamp(72px, 12vw, 126px); line-height: 1; }
    h1 { margin: 18px 0 12px; font-size: clamp(38px, 7vw, 72px); line-height: 1.05; }
    h2 { margin: 0 0 18px; font-size: clamp(26px, 4vw, 38px); }
    h3 { margin: 26px 0 10px; font-size: clamp(20px, 2.6vw, 24px); color: var(--gold); }
    .lead { max-width: 900px; margin: 0 auto; color: var(--muted); font-size: clamp(19px, 3vw, 24px); }
    .hero-actions {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 30px;
    }
    .launch {
      display: grid;
      gap: 2px;
      min-width: min(100%, 470px);
      padding: 12px 18px;
      border: 1px solid #9a783c;
      border-radius: 16px;
      background: linear-gradient(135deg, #302719, #24211b);
      text-align: left;
    }
    .launch-action {
      display: inline-flex;
      min-width: min(100%, 210px);
      min-height: 50px;
      align-items: center;
      justify-content: center;
      padding: 13px 24px;
      border: 1px solid #f0ca7a;
      border-radius: 999px;
      background: var(--gold);
      box-shadow: 0 12px 30px #e8b85424;
      color: #171613;
      font-size: 17px;
      font-weight: 800;
      text-decoration: none;
      transition: transform 520ms var(--spring), box-shadow 220ms ease, background 180ms ease;
    }
    .launch-action:hover {
      background: #f3ca78;
      transform: translateY(-3px) scale(1.025);
      box-shadow: 0 16px 36px #e8b85433;
    }
    .launch-action:focus-visible { outline: 2px solid var(--text); outline-offset: 4px; }
    .launch-label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .1em;
      text-transform: uppercase;
    }
    .launch-command { display: block; color: var(--text); font-weight: 700; }
    section {
      margin: 0 0 24px;
      padding: 34px;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--panel);
    }
    .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    .feature { padding: 22px; border-radius: 18px; background: #1d1c19; }
    .feature strong { display: block; margin-bottom: 7px; color: var(--gold); font-size: 19px; }
    .voice-demo {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 42px minmax(0, 1fr);
      align-items: center;
      gap: 14px;
      margin: 0 0 22px;
      padding: 18px;
      border: 1px solid #55472f;
      border-radius: 18px;
      background: linear-gradient(135deg, #1b1915, #211d16);
    }
    .voice-turn { min-width: 0; }
    .voice-speaker {
      display: block;
      margin-bottom: 4px;
      color: var(--gold);
      font-size: 13px;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .voice-phrase { color: var(--text); font-weight: 700; }
    .voice-signal { display: flex; justify-content: center; align-items: center; gap: 4px; color: var(--gold); }
    .voice-signal span { width: 3px; border-radius: 999px; background: currentColor; }
    .voice-signal span:nth-child(1), .voice-signal span:nth-child(5) { height: 8px; opacity: .45; }
    .voice-signal span:nth-child(2), .voice-signal span:nth-child(4) { height: 18px; opacity: .7; }
    .voice-signal span:nth-child(3) { height: 28px; }
    .faq { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0 32px; margin: 0; }
    .faq div { padding: 18px 0; border-top: 1px solid var(--line); }
    .faq dt { color: var(--gold); font-weight: 700; }
    .faq dd { margin: 8px 0 0; color: var(--muted); }
    ul { margin: 0; padding-left: 22px; }
    li + li { margin-top: 8px; }
    .section-intro { margin: -6px 0 20px; color: var(--muted); }
    .command-list { columns: 2; column-gap: 48px; }
    .command-list li { break-inside: avoid; }
    code { color: var(--gold); font: inherit; }
    a { color: var(--gold); }
    a.piece {
      display: inline-block;
      text-decoration: none;
      transition: transform 520ms var(--spring);
    }
    a.piece:hover { transform: translateY(-5px) scale(1.05); }
    a.piece:focus-visible { outline: 2px solid var(--gold); outline-offset: 8px; border-radius: 12px; }
    .article p { color: var(--muted); }
    .article > * { max-width: 72ch; margin-inline: auto; }
    .article p:first-of-type { margin-top: 0; }
    .article li { color: var(--muted); }
    .article strong { color: var(--text); }
    .steps { padding-left: 22px; }
    .steps li { margin-top: 14px; }
    .steps li strong { display: block; color: var(--gold); }
    /* A voluntary ask, not a paywall: it stays visible but never outweighs the skill itself. */
    .support { padding: 26px 34px; text-align: center; }
    .support h2 { margin-bottom: 10px; font-size: clamp(20px, 2.4vw, 25px); }
    .support p { max-width: 680px; margin: 0 auto 18px; color: var(--muted); font-size: 16px; }
    .support-action {
      display: inline-block;
      padding: 14px 24px;
      border-radius: 999px;
      background: var(--gold);
      color: #171613;
      font-weight: 800;
      text-decoration: none;
      transition: transform 520ms var(--spring), box-shadow 220ms ease, background 180ms ease;
    }
    .support .support-action { padding: 11px 20px; font-size: 15px; }
    .support-action:hover {
      background: #f3ca78;
      transform: translateY(-3px) scale(1.025);
      box-shadow: 0 14px 34px #e8b8542e;
    }
    .support .support-note { margin-top: 16px; margin-bottom: 0; font-size: 14px; }
    .support .support-review { margin: 13px auto 0; font-size: 15px; }
    footer {
      display: grid;
      grid-template-columns: minmax(220px, .7fr) minmax(0, 1.3fr);
      gap: 34px;
      align-items: start;
      margin-top: 36px;
      padding: 32px 4px 48px;
      border-top: 1px solid var(--line);
      color: var(--muted);
    }
    .footer-brand { display: flex; align-items: center; gap: 13px; color: var(--text); text-decoration: none; }
    .footer-piece {
      display: grid;
      width: 44px;
      height: 44px;
      place-items: center;
      flex: 0 0 auto;
      border: 1px solid #69542f;
      border-radius: 13px;
      background: #242018;
      color: var(--gold);
      font: 28px/1 Georgia, serif;
    }
    .footer-brand-copy strong { display: block; font-size: 17px; }
    .footer-brand-copy span { display: block; margin-top: 2px; color: var(--muted); font-size: 14px; }
    .footer-nav { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 24px; }
    .footer-nav a {
      color: var(--muted);
      text-decoration-color: #6d5d3d;
      text-underline-offset: 4px;
      transition: color 180ms ease, text-decoration-color 180ms ease;
    }
    .footer-nav a:hover { color: var(--gold); text-decoration-color: currentColor; }
    .footer-brand:focus-visible, .footer-nav a:focus-visible { outline: 2px solid var(--gold); outline-offset: 4px; }
    :root {
      --spring: linear(
        0, .009, .035 2.1%, .141 4.4%, .723 12.9%, .938 16.7%, 1.017 20.4%,
        1.051 24.7%, 1.019 30.1%, .995 36%, 1.002 43%, 1
      );
    }
    .feature, section {
      transition: transform 520ms var(--spring), border-color 220ms ease, box-shadow 220ms ease;
    }
    .has-motion .motion-item { opacity: 0; filter: blur(4px); transform: translateY(28px) scale(.985); }
    /* The visible state is set outright, never by the animation alone. `--spring`
       is a linear() easing: where that is unsupported the var() substitution makes
       the whole `animation` declaration invalid, and a page whose reveal lived only
       in that declaration would stay blank for good. */
    .has-motion .motion-item.is-visible { opacity: 1; filter: none; transform: none; }
    @supports (animation-timing-function: linear(0, 1)) {
      .has-motion .motion-item.is-visible {
        animation: page-reveal 760ms var(--spring) both;
        animation-delay: var(--motion-delay, 0ms);
      }
    }
    @keyframes page-reveal { to { opacity: 1; filter: none; transform: none; } }
    @media (hover: hover) and (prefers-reduced-motion: no-preference) {
      .feature:hover {
        border-color: #e8b85466;
        transform: translateY(-4px);
        box-shadow: 0 18px 42px #0000002e;
      }
    }
    /* Colour-only hover for anyone who asked not to be moved. */
    @media (hover: hover) and (prefers-reduced-motion: reduce) {
      .feature:hover { border-color: #e8b85466; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        scroll-behavior: auto !important;
        animation: none !important;
        transition: none !important;
      }
      .motion-item, .stats-card, .stats-bar, .stats-bar-value {
        opacity: 1 !important;
        filter: none !important;
        transform: none !important;
      }
      /* Killing the transition alone leaves the displacement — it just arrives
         instantly instead of gliding, which is the jump the setting asks to avoid. */
      a.piece:hover, .launch-action:hover, .support-action:hover, .stats-tab:hover { transform: none !important; }
    }
    @media (max-width: 760px) {
      .site-top { padding-top: 12px; }
      .site-nav { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); width: 100%; }
      .site-nav a {
        display: flex;
        min-height: 40px;
        align-items: center;
        justify-content: center;
        padding: 7px 6px;
        font-size: 14px;
      }
      header { padding-top: 32px; }
      section { padding: 24px; }
      .grid { grid-template-columns: 1fr; }
      .faq { grid-template-columns: 1fr; }
      .voice-demo { grid-template-columns: 1fr; gap: 12px; text-align: left; }
      .voice-signal { justify-content: center; height: 28px; }
      .command-list { columns: 1; }
      footer { grid-template-columns: 1fr; gap: 24px; margin-top: 28px; padding-inline: 2px; }
      .footer-nav { gap: 12px 18px; }
    }
"""
    + DASHBOARD_CSS
)

SITE_SCRIPT = """
    (() => {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const showLatest = (root = document) => {
        const chart = root.querySelector(".stats-chart");
        if (chart) chart.scrollLeft = chart.scrollWidth - chart.clientWidth;
      };
      requestAnimationFrame(() => showLatest());
      window.addEventListener("load", () => showLatest(), { once: true });

      const formatter = new Intl.NumberFormat("ru-RU");
      // Only the aria-hidden layer is animated; the figure a screen reader reads
      // is the server-rendered one and never passes through "0".
      const animateCounter = (element) => {
        const target = Number(element.dataset.count || 0);
        const started = performance.now();
        const duration = 900;
        const tick = (now) => {
          const progress = Math.min((now - started) / duration, 1);
          const eased = 1 - Math.pow(1 - progress, 3);
          element.textContent = formatter.format(Math.round(target * eased));
          if (progress < 1) requestAnimationFrame(tick);
        };
        element.textContent = "0";
        requestAnimationFrame(tick);
      };

      let reveal;
      const prepareStatistics = (section) => {
        if (!section) return;
        requestAnimationFrame(() => showLatest(section));
        // Switching the period replaces the section, and with it every hint listener.
        wireTooltips(section);
        if (!reveal) return;
        section.querySelectorAll(".stats-cards, .stats-chart").forEach((element) => reveal.observe(element));
      };
      // Clicking two periods quickly used to let the slower answer win and leave
      // the section disagreeing with the address bar.
      let statisticsRequest = 0;
      const loadStatistics = async (url, updateHistory = true) => {
        const current = document.querySelector("#statistics");
        if (!current) return;
        const request = ++statisticsRequest;
        current.setAttribute("aria-busy", "true");
        try {
          const response = await fetch(url, { headers: { "X-Requested-With": "statistics" } });
          if (!response.ok) throw new Error(`Statistics request failed: ${response.status}`);
          const page = new DOMParser().parseFromString(await response.text(), "text/html");
          const replacement = page.querySelector("#statistics");
          if (!replacement) throw new Error("Statistics section is missing");
          if (request !== statisticsRequest) return;
          const scrollPosition = window.scrollY;
          const wasFocused = document.activeElement?.closest?.(".stats-tab") !== null
            && document.activeElement?.classList?.contains("stats-tab");
          current.replaceWith(replacement);
          if (updateHistory) history.pushState({ statistics: true }, "", url);
          window.scrollTo({ top: scrollPosition });
          // Replacing the section destroys the link that was just activated, which
          // would otherwise drop keyboard focus to the top of the document.
          if (wasFocused) replacement.querySelector(".stats-tab.active")?.focus({ preventScroll: true });
          prepareStatistics(replacement);
        } catch (error) {
          window.location.assign(url);
        } finally {
          if (request === statisticsRequest) {
            document.querySelector("#statistics")?.setAttribute("aria-busy", "false");
          }
        }
      };
      // The tooltip sits under the card so it never covers the number it explains,
      // and flips only when the side it is on has less room than the other.
      const placeTooltip = (hint) => {
        const card = hint.closest(".stats-card");
        const tip = hint.nextElementSibling;
        if (!card || !tip) return;
        card.classList.remove("tip-above");
        const box = card.getBoundingClientRect();
        const needed = tip.offsetHeight + 24;
        const below = window.innerHeight - box.bottom;
        if (below < needed && box.top > below) card.classList.add("tip-above");
      };
      const openTooltip = (hint) => {
        placeTooltip(hint);
        hint.setAttribute("aria-expanded", "true");
      };
      const closeTooltip = (hint) => hint.setAttribute("aria-expanded", "false");
      const openHints = () => document.querySelectorAll('.stats-hint[aria-expanded="true"]');
      const wireTooltips = (root) => {
        (root || document).querySelectorAll(".stats-hint").forEach((hint) => {
          hint.addEventListener("pointerenter", () => placeTooltip(hint));
          hint.addEventListener("focus", () => openTooltip(hint));
          hint.addEventListener("blur", () => closeTooltip(hint));
          // Touch has no hover, so the tap has to toggle. Pressing the button also
          // focuses it, and focus opens the panel — so the state is read on
          // pointerdown, before that happens, or every tap would close what it opened.
          let wasOpen = false;
          hint.addEventListener("pointerdown", () => {
            wasOpen = hint.getAttribute("aria-expanded") === "true";
          });
          hint.addEventListener("click", (event) => {
            event.preventDefault();
            wasOpen ? closeTooltip(hint) : openTooltip(hint);
            wasOpen = false;
          });
        });
      };
      wireTooltips(document);

      // Escape must close the panel without throwing focus away — losing your
      // place in the page is a worse outcome than the panel staying open.
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        openHints().forEach(closeTooltip);
      });
      // An open panel is positioned against a box that scrolling and resizing move.
      ["resize", "scroll"].forEach((event) =>
        window.addEventListener(event, () => openHints().forEach(placeTooltip), { passive: true })
      );

      document.addEventListener("click", (event) => {
        const tab = event.target.closest(".stats-tab");
        if (!tab || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        loadStatistics(tab.href);
      });
      window.addEventListener("popstate", () => loadStatistics(window.location.href, false));

      if (reducedMotion) return;
      document.documentElement.classList.add("has-motion");

      reveal = new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          if (entry.target.classList.contains("stats-cards")) {
            entry.target.querySelectorAll(".stats-value-shown[data-count]").forEach(animateCounter);
          }
          observer.unobserve(entry.target);
        });
      // A ratio threshold is unreachable for a section taller than a few screens:
      // the strip visible at the fold is a small fraction of it, so it would stay
      // invisible until scrolled and the page would look like it ends at the hero.
      // Firing on first contact, held off the very bottom edge, works at any height.
      }, { threshold: 0, rootMargin: "0px 0px -10%" });

      document.querySelectorAll("main > header, main > section, .feature, .faq > div").forEach((element, index) => {
        element.classList.add("motion-item");
        element.style.setProperty("--motion-delay", `${(index % 4) * 45}ms`);
        reveal.observe(element);
      });
      prepareStatistics(document.querySelector("#statistics"));
    })();
"""

NAV_ITEMS: tuple[tuple[str, str], ...] = (
    (LANDING_PATH, "Навык"),
    (HOW_TO_PLAY_PATH, "Как играть"),
    (COMMANDS_PATH, "Команды"),
    (COACH_PATH, "Тренер"),
    (PUZZLES_PATH, "Задачи"),
    (ACCESSIBILITY_PATH, "Без экрана"),
    (BLINDFOLD_PATH, "Вслепую"),
)


def _nav(current: str) -> str:
    """Internal links are how a crawler reaches the secondary pages, and how a reader leaves one."""
    links = "\n        ".join(
        f'<a href="{path}"{' aria-current="page"' if path == current else ""}>{label}</a>' for path, label in NAV_ITEMS
    )
    return f"""<nav class="site-nav" aria-label="Разделы сайта">
        {links}
      </nav>"""


# Longer anchor text than the top nav: the same pages, described the way people
# search for them.
FOOTER_HTML = f"""<footer>
    <a class="footer-brand" href="{LANDING_PATH}">
      <span class="footer-piece" aria-hidden="true">♞</span>
      <span class="footer-brand-copy">
        <strong>Шахматы с Юрой</strong>
        <span>Голосовая игра с Алисой</span>
      </span>
    </a>
    <nav class="footer-nav" aria-label="Дополнительные страницы">
      <a href="{HOW_TO_PLAY_PATH}">Как играть в шахматы голосом</a>
      <a href="{COMMANDS_PATH}">Голосовые команды</a>
      <a href="{COACH_PATH}">Шахматный тренер голосом</a>
      <a href="{PUZZLES_PATH}">Шахматные задачи</a>
      <a href="{ACCESSIBILITY_PATH}">Шахматы для незрячих</a>
      <a href="{BLINDFOLD_PATH}">Игра вслепую</a>
    </nav>
  </footer>"""


WEBSITE_SCHEMA: Schema = {
    "@type": "WebSite",
    "@id": f"{PUBLIC_SITE_URL}#website",
    "url": PUBLIC_SITE_URL,
    "name": "Шахматы с Юрой",
    "description": "Голосовые шахматы с Алисой против Stockfish",
    "inLanguage": "ru-RU",
}


def _absolute(path: str) -> str:
    return f"{PUBLIC_SITE_URL.rstrip('/')}{path}"


def _breadcrumb_schema(title: str, path: str) -> Schema:
    """Search results get the trail; the page itself does not.

    A visible trail above the hero orphaned a line of small text over the glyph
    and broke the composition. The nav below the hero already says where you are
    (`aria-current`) and how to leave (the glyph and «Навык»), so the trail only
    ever earned its place in the SERP.
    """
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Шахматы с Юрой", "item": PUBLIC_SITE_URL},
            {"@type": "ListItem", "position": 2, "name": title, "item": _absolute(path)},
        ],
    }


def _page_schema(name: str, path: str) -> Schema:
    return {
        "@type": "WebPage",
        "@id": _absolute(path),
        "url": _absolute(path),
        "name": name,
        "inLanguage": "ru-RU",
        "isPartOf": {"@id": f"{PUBLIC_SITE_URL}#website"},
        "about": {"@id": f"{PUBLIC_SITE_URL}#skill"},
    }


def _document(*, title: str, description: str, path: str, structured_data: list[Schema], body: str) -> str:
    canonical = _absolute(path) if path != LANDING_PATH else PUBLIC_SITE_URL
    # Serialised rather than hand-written: a stray newline inside a JSON string
    # silently invalidates the whole graph for a crawler.
    graph = json.dumps(
        {"@context": "https://schema.org", "@graph": [WEBSITE_SCHEMA, *structured_data]},
        ensure_ascii=False,
        indent=2,
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{description}">
  <meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">
  <meta name="theme-color" content="#171613">
  <link rel="canonical" href="{canonical}">
  <link rel="alternate" hreflang="ru-RU" href="{canonical}">
  <link rel="alternate" hreflang="x-default" href="{canonical}">
  <link rel="icon" href="{FAVICON_PATH}" type="image/svg+xml">
  <title>{title}</title>
  <meta property="og:type" content="website">
  <meta property="og:locale" content="ru_RU">
  <meta property="og:site_name" content="Шахматы с Юрой">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta property="og:url" content="{canonical}">
  <meta property="og:image" content="{_absolute(SOCIAL_CARD_PATH)}">
  <meta property="og:image:width" content="{CARD_WIDTH}">
  <meta property="og:image:height" content="{CARD_HEIGHT}">
  <meta property="og:image:alt" content="{SOCIAL_CARD_ALT}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{description}">
  <meta name="twitter:image" content="{_absolute(SOCIAL_CARD_PATH)}">
  <meta name="twitter:image:alt" content="{SOCIAL_CARD_ALT}">
  <script type="application/ld+json">
{graph}
  </script>
  <style>{SITE_CSS}
  </style>
</head>
<body>
  <main>
    <div class="site-top">
      {_nav(path)}
    </div>
{body}
  {FOOTER_HTML}
  </main>
  <script>{SITE_SCRIPT}
  </script>
</body>
</html>"""


SKILL_SCHEMA: Schema = {
    "@type": "SoftwareApplication",
    "@id": f"{PUBLIC_SITE_URL}#skill",
    "url": PUBLIC_SITE_URL,
    "name": "Шахматы с Юрой",
    "alternateName": "Шахматы с Алисой",
    "description": "Голосовой навык Алисы: партии против Stockfish, тренер, разбор игр и шахматные задачи без экрана.",
    "applicationCategory": "GameApplication",
    "operatingSystem": "Яндекс Алиса",
    "installUrl": ALICE_SKILL_URL,
    "sameAs": [YANDEX_DIALOG_URL],
    "inLanguage": "ru-RU",
    "isAccessibleForFree": True,
    "accessibilityFeature": ["fullAudioDescription", "structuralNavigation", "synchronizedAudioText"],
    "accessibilityHazard": "none",
    "accessMode": ["auditory"],
    "accessModeSufficient": ["auditory"],
    "featureList": [
        "Голосовые шахматные партии против Stockfish",
        "Режим тренера с оценкой, вариантами и подсказками",
        "Разбор завершённых партий и PGN",
        "Голосовые шахматные задачи и серии решений",
        "Полная игра без экрана для незрячих и слабовидящих",
    ],
    "offers": {"@type": "Offer", "price": "0", "priceCurrency": "RUB"},
}

# One source for the visible FAQ and its schema: a rich result is dropped when
# the marked-up answer is not the answer on the page.
LANDING_FAQ: tuple[tuple[str, str], ...] = (
    (
        "Как играть в шахматы с Алисой?",
        "Скажите «Алиса, запусти навык Шахматы с Юрой», затем «новая игра белыми уровень пять» "
        "и называйте ходы голосом: «пешка е два е четыре».",
    ),
    (
        "Умеет ли Алиса играть в шахматы?",
        "Сама Алиса партию не ведёт, но навык «Шахматы с Юрой» добавляет ей полноценные шахматы "
        "против движка Stockfish с двадцатью уровнями сложности.",
    ),
    (
        "Нужен ли экран, чтобы играть?",
        "Нет. Все обязательные действия доступны голосом: навык читает позицию, историю ходов "
        "и результат. Экранная доска — только дополнение.",
    ),
    (
        "Сколько стоит навык?",
        "Навык бесплатный: нет покупок, рекламы и платных функций. Поддержка проекта добровольная "
        "и не даёт преимуществ в игре.",
    ),
    (
        "Есть ли режим тренера и разбор партии?",
        "Да. Тренер даёт оценку позиции, варианты ходов и ступенчатые подсказки, а после партии "
        "навык находит перелом, главную ошибку и показывает PGN.",
    ),
    (
        "Как узнать все команды?",
        "Скажите «что ты умеешь?» для короткого меню или «все команды» для полного каталога. "
        "Полный список есть и на странице «Все команды».",
    ),
)

LANDING_FAQ_SCHEMA: Schema = {
    "@type": "FAQPage",
    "mainEntity": [
        {"@type": "Question", "name": question, "acceptedAnswer": {"@type": "Answer", "text": answer}}
        for question, answer in LANDING_FAQ
    ],
}

LANDING_FAQ_HTML = "\n".join(
    f"""        <div>
          <dt>{question}</dt>
          <dd>{answer}</dd>
        </div>"""
    for question, answer in LANDING_FAQ
)

LANDING_BODY = f"""    <header>
      <div class="piece" aria-hidden="true">♞</div>
      <h1>Шахматы с Юрой</h1>
      <p class="lead">
        Играйте в шахматы голосом с Алисой: полноценная партия против сильного движка Stockfish —
        с&nbsp;естественными командами, понятными объяснениями и сохранением игры. Экран не нужен.
      </p>
      <div class="hero-actions">
        <div class="launch">
          <span class="launch-label">Скажите Алисе</span>
          <span class="launch-command">«Запусти навык Шахматы с Юрой»</span>
        </div>
        <a class="launch-action" href="{ALICE_SKILL_URL}" target="_blank" rel="noopener noreferrer">
          Запустить навык
        </a>
      </div>
    </header>

    <section>
      <h2>Настоящие шахматы в Алисе</h2>
      <div class="voice-demo" aria-label="Пример голосовой партии">
        <div class="voice-turn">
          <span class="voice-speaker">Вы</span>
          <span class="voice-phrase">«Новая игра белыми, уровень пять»</span>
        </div>
        <div class="voice-signal" aria-hidden="true">
          <span></span><span></span><span></span><span></span><span></span>
        </div>
        <div class="voice-turn">
          <span class="voice-speaker">Алиса</span>
          <span class="voice-phrase">«Вы играете белыми, я — чёрными. Ваш ход»</span>
        </div>
      </div>
      <div class="grid">
        <div class="feature">
          <strong>Говорите естественно</strong>
          «Пешка е два е четыре», «конь эф три», «короткая рокировка» — навык понимает разные формы шахматной речи.
        </div>
        <div class="feature">
          <strong>Играйте на своём уровне</strong>
          Выбирайте цвет и силу Stockfish от спокойной партии до серьёзной тренировки.
        </div>
        <div class="feature">
          <strong>Продолжайте позже</strong>
          Незаконченная партия сохраняется. При следующем запуске Алиса напомнит последние два хода.
        </div>
        <div class="feature">
          <strong>Получайте объяснения</strong>
          Если ход невозможен, Алиса объяснит причину и не изменит позицию.
        </div>
        <div class="feature">
          <strong>Спрашивайте о доске</strong>
          Узнавайте, что находится на клетке, где стоят фигуры, чей ход и что произошло несколько ходов назад.
        </div>
        <div class="feature">
          <strong>Играйте без экрана</strong>
          Навык создавался для незрячих и слабовидящих игроков: вся партия доступна на слух.
          <a href="{ACCESSIBILITY_PATH}">Подробнее о доступности</a>.
        </div>
      </div>
    </section>

    <section>
      <h2>Что можно сказать</h2>
      <p class="section-intro">
        Это только примеры. Скажите «что ты умеешь?» или «все команды», чтобы услышать полный каталог,
        либо откройте <a href="{COMMANDS_PATH}">полный список голосовых команд</a>.
      </p>
      <ul class="command-list">
        <li><code>«Пешка е два е четыре»</code></li>
        <li><code>«Какая позиция?»</code></li>
        <li><code>«Что на эф три?»</code></li>
        <li><code>«Где белые слоны?»</code></li>
        <li><code>«Могу ли я сделать рокировку?»</code></li>
        <li><code>«Какой дебют?»</code></li>
        <li><code>«Включи режим тренера»</code></li>
        <li><code>«Оцени позицию»</code></li>
        <li><code>«Что будет, если я сыграю коня эф три?»</code></li>
        <li><code>«Подскажи»</code></li>
        <li><code>«Разбери партию»</code></li>
        <li><code>«Покажи PGN»</code></li>
        <li><code>«Сыграть эту позицию заново»</code></li>
        <li><code>«Дай задачу»</code></li>
        <li><code>«Задача на мат в два хода»</code></li>
        <li><code>«Какая у меня серия?»</code></li>
        <li><code>«Повтори медленно»</code></li>
      </ul>
    </section>

    <section>
      <h2>Как играть в голосовые шахматы с Алисой</h2>
      <p class="section-intro">
        Короткие ответы на частые вопросы. Пошаговая инструкция —
        на странице <a href="{HOW_TO_PLAY_PATH}">«Как играть»</a>.
      </p>
      <dl class="faq">
{LANDING_FAQ_HTML}
      </dl>
    </section>

    {{{{ dashboard }}}}

    <section id="support" class="support">
      <h2>Поддержать проект</h2>
      <p>
        «Шахматы с Юрой» остаются бесплатными для всех. Добровольная поддержка помогает оплачивать сервер
        и развивать навык.
      </p>
      <a
        class="support-action"
        href="https://pay.cloudtips.ru/p/f604e20f"
        target="_blank"
        rel="noopener noreferrer nofollow"
      >Поддержать «Шахматы с Юрой»</a>
      <p class="support-note">Поддержка не предоставляет платных функций или преимуществ в игре.</p>
      <p class="support-review">
        Уже сыграли?
        <a href="{YANDEX_REVIEW_URL}" target="_blank" rel="noopener noreferrer">Оставьте отзыв в Яндексе</a>.
      </p>
    </section>

    <section>
      <h2>Конфиденциальность</h2>
      <p>
        Навык хранит состояние партий и технические данные, необходимые для продолжения игры и защиты от повторных
        запросов. Идентификатор пользователя сохраняется в необратимо псевдонимизированном виде. Обезличенные счётчики
        использования сохраняются без срока для статистики, а текст распознанных команд — до 30 дней для улучшения
        качества распознавания.
      </p>
      <p>Навык не использует покупки, рекламу или связку сторонних аккаунтов. Исходный код проекта доступен на <a href="https://github.com/Blaryxoff/yura-chess">GitHub</a>.</p>
    </section>
"""

LANDING_PAGE_HTML = _document(
    title="Шахматы с Юрой — играть в шахматы голосом с Алисой",
    description=(
        "Бесплатный навык Алисы: полноценная партия в шахматы голосом против Stockfish, режим тренера, "
        "разбор партий и шахматные задачи. Играть можно без экрана."
    ),
    path=LANDING_PATH,
    structured_data=[SKILL_SCHEMA, LANDING_FAQ_SCHEMA],
    body=LANDING_BODY,
)

_HOW_TO_PLAY_TITLE = "Как играть в шахматы с Алисой голосом"
_HOW_TO_PLAY_CRUMB_SCHEMA = _breadcrumb_schema(_HOW_TO_PLAY_TITLE, HOW_TO_PLAY_PATH)

HOW_TO_PLAY_PAGE_HTML = _document(
    title="Как играть в шахматы с Алисой голосом — пошаговая инструкция",
    description=(
        "Пошагово: как запустить навык «Шахматы с Юрой», начать партию, называть ходы голосом, "
        "выбрать уровень Stockfish, вернуться к незаконченной игре и включить тренера."
    ),
    path=HOW_TO_PLAY_PATH,
    structured_data=[
        {
            "@type": "HowTo",
            "name": _HOW_TO_PLAY_TITLE,
            "inLanguage": "ru-RU",
            "totalTime": "PT2M",
            "step": [
                {
                    "@type": "HowToStep",
                    "name": "Запустить навык",
                    "text": "Скажите: «Алиса, запусти навык Шахматы с Юрой».",
                },
                {
                    "@type": "HowToStep",
                    "name": "Начать партию",
                    "text": "Скажите: «новая игра белыми уровень пять». Уровень — от нуля до двадцати.",
                },
                {
                    "@type": "HowToStep",
                    "name": "Назвать ход",
                    "text": "Скажите ход обычными словами: «пешка е два е четыре» или просто «е два е четыре».",
                },
                {
                    "@type": "HowToStep",
                    "name": "Уточнить позицию",
                    "text": "Спросите «какая позиция», «что на эф три» или «какой был последний ход».",
                },
                {
                    "@type": "HowToStep",
                    "name": "Продолжить позже",
                    "text": "Партия сохраняется: при следующем запуске скажите «продолжить последнюю партию».",
                },
            ],
        },
        _HOW_TO_PLAY_CRUMB_SCHEMA,
    ],
    body=f"""    <header>
      <a class="piece home" href="{LANDING_PATH}" aria-label="На главную «Шахматы с Юрой»">♟</a>
      <h1>{_HOW_TO_PLAY_TITLE}</h1>
      <p class="lead">
        Пять шагов от запуска навыка до первой партии против Stockfish. Ничего запоминать не нужно:
        навык понимает обычную шахматную речь и сам подсказывает, что сказать дальше.
      </p>
    </header>

    <section class="article">
      <h2>Пошаговая инструкция</h2>
      <ol class="steps">
        <li>
          <strong>Запустите навык</strong>
          Скажите «Алиса, запусти навык Шахматы с Юрой» любой колонке, приложению Яндекса или Станции.
          Навык бесплатный, устанавливать ничего не нужно.
        </li>
        <li>
          <strong>Начните партию</strong>
          Скажите «новая игра белыми уровень пять» или «новая игра чёрными». Уровень Stockfish — от нуля
          до двадцати: ноль подходит начинающим, двадцать — максимальный уровень.
        </li>
        <li>
          <strong>Называйте ходы обычными словами</strong>
          Подойдут «пешка е два е четыре», «конь эф три», «слон берёт на аш семь», «короткая рокировка»
          или просто «е два е четыре». Если фраза допускает несколько ходов, навык переспросит
          и не станет ходить наугад.
        </li>
        <li>
          <strong>Спрашивайте о доске</strong>
          «Какая позиция» читает доску по горизонталям, «что на эф три» называет фигуру на клетке,
          «где белые слоны», «чей ход», «есть ли шах», «какой был последний ход» — всё доступно на слух.
        </li>
        <li>
          <strong>Возвращайтесь к партии</strong>
          Незаконченная игра сохраняется. При следующем запуске скажите «продолжить последнюю партию»:
          Алиса напомнит последние ходы и продолжит с той же позиции.
        </li>
      </ol>

      <h3>Если ход не приняли</h3>
      <p>
        Навык никогда не меняет позицию молча. Если ход невозможен, он назовёт конкретную причину:
        поле занято, путь перекрыт, фигура так не ходит, рокировка недоступна или король останется под шахом.
        Если фразу распознали неточно, скажите «что ты услышала» или «повтори координаты по буквам».
      </p>

      <h3>Как выбрать уровень Stockfish</h3>
      <p>
        Уровни от нуля до двадцати задают силу движка. Начинающим подходят уровни 0–5, уверенным
        любителям — 6–12, для серьёзной тренировки — 13–20. Спросите «какой уровень», чтобы узнать текущий,
        и скажите «сыграем сложнее», чтобы поднять сложность в следующей партии.
      </p>

      <h3>Тренер, разбор и задачи</h3>
      <p>
        Скажите «включи режим тренера», чтобы получать оценку позиции, хорошие ходы и ступенчатые подсказки.
        После партии команда «разбери партию» найдёт перелом и главную ошибку, а «дай задачу» откроет
        голосовые шахматные задачи: мат в один и два хода, вилка, связка, сквозной удар.
        Полный перечень — на странице <a href="{COMMANDS_PATH}">«Все команды»</a>.
      </p>
    </section>
""",
)

_COMMANDS_TITLE = "Все голосовые команды навыка"
_COMMANDS_CRUMB_SCHEMA = _breadcrumb_schema(_COMMANDS_TITLE, COMMANDS_PATH)

COMMANDS_PAGE_HTML = _document(
    title="Голосовые команды шахмат в Алисе — полный список",
    description=(
        "Полный список голосовых команд навыка «Шахматы с Юрой»: ходы, вопросы о позиции и партии, "
        "настройки речи, режим тренера, разбор партии и шахматные задачи."
    ),
    path=COMMANDS_PATH,
    structured_data=[_page_schema(_COMMANDS_TITLE, COMMANDS_PATH), _COMMANDS_CRUMB_SCHEMA],
    body=f"""    <header>
      <a class="piece home" href="{LANDING_PATH}" aria-label="На главную «Шахматы с Юрой»">♜</a>
      <h1>{_COMMANDS_TITLE}</h1>
      <p class="lead">
        Дословно запоминать команды не нужно — навык понимает разные формулировки. Этот список показывает,
        о чём вообще можно попросить. В самом навыке те же разделы читает команда «все команды».
      </p>
    </header>

    <section class="article">
      <h2>Ходы</h2>
      <ul>
        <li><code>«пешка е два е четыре»</code>, <code>«конь эф три»</code>, <code>«е два е четыре»</code> — ход</li>
        <li><code>«слон берёт на аш семь»</code> — взятие</li>
        <li><code>«короткая рокировка»</code>, <code>«длинная рокировка»</code></li>
        <li><code>«отмени ход»</code>, <code>«отмени три полных хода»</code></li>
      </ul>

      <h2>Позиция</h2>
      <ul>
        <li><code>«какая позиция»</code> — чтение доски по горизонталям, дальше — <code>«дальше»</code></li>
        <li><code>«что на е четыре»</code>, <code>«где белые слоны»</code>, <code>«чей ход»</code>,
            <code>«есть ли шах»</code></li>
        <li><code>«какой был последний ход»</code>, <code>«что делали чёрные четыре хода назад»</code></li>
      </ul>

      <h2>Факты о партии</h2>
      <ul>
        <li><code>«за кого я играю»</code>, <code>«какой сейчас ход»</code>,
            <code>«сколько ходов мы сыграли»</code></li>
        <li><code>«какие фигуры съедены»</code>, <code>«могу ли я сделать рокировку»</code>,
            <code>«кто даёт шах»</code></li>
        <li><code>«какой дебют»</code>, <code>«какая стадия партии»</code>,
            <code>«что изменил последний ход»</code></li>
      </ul>

      <h2>Управление партией</h2>
      <ul>
        <li><code>«новая игра чёрными, уровень десять»</code> — уровень от нуля до двадцати</li>
        <li><code>«продолжить последнюю партию»</code>, <code>«какой уровень»</code></li>
        <li><code>«предлагаю ничью»</code>, <code>«сдаюсь»</code></li>
        <li><code>«реванш другим цветом»</code>, <code>«сыграем сложнее»</code></li>
      </ul>

      <h2>Настройки речи и доски</h2>
      <ul>
        <li><code>«говори кратко»</code>, <code>«обычная подробность ответов»</code>,
            <code>«говори подробно»</code></li>
        <li><code>«говори медленнее»</code>, <code>«говори быстрее»</code> — паузы между фразами</li>
        <li><code>«короткая нотация»</code>, <code>«полная нотация»</code></li>
        <li><code>«доска всегда за белых»</code>, <code>«доска всегда за чёрных»</code>,
            <code>«доска по моему цвету»</code></li>
      </ul>

      <h2>Режим тренера</h2>
      <ul>
        <li><code>«включи режим тренера»</code>, <code>«выключи тренера»</code></li>
        <li><code>«оцени позицию»</code>, <code>«назови оценку числом»</code>, <code>«чем ты угрожаешь»</code>,
            <code>«какие ходы хорошие»</code></li>
        <li><code>«почему ты так сходил»</code>,
            <code>«что будет, если я сыграю коня эф три»</code> — разбор без хода</li>
        <li><code>«подскажи»</code>, <code>«где я ошибся»</code>, <code>«оставить мой ход»</code></li>
      </ul>

      <h2>Разбор сыгранной партии</h2>
      <ul>
        <li><code>«разбери партию»</code>, <code>«продолжить разбор»</code>, <code>«выйти из разбора»</code></li>
        <li><code>«где перелом»</code>, <code>«главная ошибка»</code>, <code>«сколько я ошибся»</code></li>
        <li><code>«продиктуй ходы»</code>, <code>«покажи pgn»</code>, <code>«сыграть эту позицию заново»</code></li>
      </ul>

      <h2>Шахматные задачи</h2>
      <ul>
        <li><code>«дай задачу»</code>, <code>«задача на мат в два хода»</code> — темы: мат в один, мат в два,
            вилка, связка, сквозной удар</li>
        <li><code>«повтори задачу»</code>, <code>«следующая задача»</code>, <code>«покажи решение»</code></li>
        <li><code>«какая у меня серия»</code>, <code>«вернуться к партии»</code></li>
      </ul>

      <h2>Если Алиса не расслышала</h2>
      <ul>
        <li><code>«что ты услышала»</code> — повтор распознанной фразы</li>
        <li><code>«повтори ответ»</code>, <code>«повтори последнюю фразу»</code>, <code>«повтори медленно»</code></li>
        <li><code>«повтори координаты по буквам»</code></li>
      </ul>

      <h2>Справка голосом</h2>
      <p>
        Скажите <code>«что ты умеешь»</code> для короткого меню, <code>«все команды»</code> для полного каталога
        или назовите раздел: «ходы», «позиция», «факты», «партия», «настройки», «тренер», «разбор», «задачи».
        Перемещаться по справке помогают <code>«дальше»</code>, <code>«назад»</code> и <code>«сначала»</code>.
        Если вы здесь впервые, начните с инструкции
        <a href="{HOW_TO_PLAY_PATH}">«Как играть в шахматы с Алисой голосом»</a>.
      </p>
    </section>
""",
)

_ACCESSIBILITY_TITLE = "Шахматы для незрячих и слабовидящих голосом"
_ACCESSIBILITY_CRUMB_SCHEMA = _breadcrumb_schema(_ACCESSIBILITY_TITLE, ACCESSIBILITY_PATH)

ACCESSIBILITY_PAGE_HTML = _document(
    title="Шахматы для незрячих голосом — играть с Алисой без экрана",
    description=(
        "Навык «Шахматы с Юрой» создавался для незрячих и слабовидящих игроков: партия, позиция, "
        "история ходов, тренер и задачи полностью доступны на слух, без экрана и без доски."
    ),
    path=ACCESSIBILITY_PATH,
    structured_data=[_page_schema(_ACCESSIBILITY_TITLE, ACCESSIBILITY_PATH), _ACCESSIBILITY_CRUMB_SCHEMA],
    body=f"""    <header>
      <a class="piece home" href="{LANDING_PATH}" aria-label="На главную «Шахматы с Юрой»">♚</a>
      <h1>{_ACCESSIBILITY_TITLE}</h1>
      <p class="lead">
        «Шахматы с Юрой» создавались в первую очередь для незрячих и слабовидящих игроков.
        Экран не нужен ни для одного действия: вся партия ведётся голосом.
      </p>
    </header>

    <section class="article">
      <h2>Почему навык подходит для игры без зрения</h2>
      <p>
        Обычные шахматные приложения предполагают, что игрок видит доску: ход делают касанием, позицию
        оценивают взглядом. Здесь наоборот — экран считается дополнением, а не источником информации.
        Любое действие, нужное для партии, выполняется голосом, и любой ответ навыка звучит целиком,
        а не дублирует картинку.
      </p>

      <h3>Позицию можно услышать целиком</h3>
      <p>
        Команда «какая позиция» читает доску по горизонталям, по две за раз, чтобы информация помещалась
        в память на слух; «дальше» продолжает чтение. Отдельные вопросы уточняют детали:
        «что на е четыре», «где белые слоны», «чей ход», «есть ли шах», «какие фигуры съедены».
      </p>

      <h3>Историю партии не нужно держать в голове</h3>
      <p>
        «Какой был последний ход», «что делали чёрные четыре хода назад», «сколько ходов мы сыграли»,
        «что изменил последний ход» — навык помнит партию целиком и отвечает на слух.
        Незаконченная игра сохраняется между сессиями: «продолжить последнюю партию» вернёт позицию
        и напомнит последние ходы.
      </p>

      <h3>Распознавание можно проверить</h3>
      <p>
        Ошибка распознавания не приводит к случайному ходу. Если фраза допускает несколько прочтений,
        навык переспрашивает и не меняет позицию. Команда «что ты услышала» повторяет распознанную фразу,
        «повтори ответ» и «повтори медленно» возвращают прошлый ответ, а «повтори координаты по буквам»
        проговаривает поле по буквам — на случай, когда Алиса путает «б» и «п».
      </p>

      <h3>Темп и подробность настраиваются</h3>
      <p>
        «Говори кратко» и «говори подробно» меняют объём ответов, «говори медленнее» добавляет паузы между
        фразами, «короткая нотация» и «полная нотация» — стиль произнесения ходов. Настройки сохраняются
        между партиями, поэтому подбирать их заново не нужно.
      </p>

      <h2>Тренер и задачи тоже без экрана</h2>
      <p>
        <a href="{COACH_PATH}">Режим тренера</a> озвучивает оценку позиции, хорошие ходы, угрозы
        и ступенчатые подсказки. Разбор сыгранной партии называет перелом и главную ошибку.
        <a href="{PUZZLES_PATH}">Голосовые задачи</a> — мат в один и два хода, вилка, связка,
        сквозной удар — читаются вслух вместе с решением. Ничего из этого не требует зрительного
        контакта с доской.
      </p>
      <p>
        Зрячим игрокам тот же режим даёт тренировку игры вслепую — об этом отдельная страница
        <a href="{BLINDFOLD_PATH}">«Шахматы вслепую»</a>.
      </p>

      <h2>Как начать</h2>
      <p>
        Скажите «Алиса, запусти навык Шахматы с Юрой», затем «новая игра белыми уровень пять».
        Пошаговая инструкция — на странице <a href="{HOW_TO_PLAY_PATH}">«Как играть»</a>,
        полный перечень фраз — в <a href="{COMMANDS_PATH}">списке голосовых команд</a>.
      </p>
    </section>
""",
)


_COACH_TITLE = "Шахматный тренер голосом в Алисе"
_COACH_CRUMB_SCHEMA = _breadcrumb_schema(_COACH_TITLE, COACH_PATH)

COACH_PAGE_HTML = _document(
    title="Шахматный тренер голосом — оценка позиции и разбор партии с Алисой",
    description=(
        "Режим тренера в навыке «Шахматы с Юрой»: оценка позиции словами и числом, хорошие ходы, "
        "угрозы, ступенчатые подсказки и разбор сыгранной партии — всё голосом."
    ),
    path=COACH_PATH,
    structured_data=[_page_schema(_COACH_TITLE, COACH_PATH), _COACH_CRUMB_SCHEMA],
    body=f"""    <header>
      <a class="piece home" href="{LANDING_PATH}" aria-label="На главную «Шахматы с Юрой»">♛</a>
      <h1>{_COACH_TITLE}</h1>
      <p class="lead">
        Обычная партия остаётся честной: движок не подсказывает, пока вы не попросите.
        Тренер — отдельный режим, который включается одной фразой и объясняет позицию словами,
        а не строкой вариантов.
      </p>
    </header>

    <section class="article">
      <h2>Как включить тренера</h2>
      <p>
        Скажите <code>«включи режим тренера»</code> — прямо посреди партии, терять позицию не нужно.
        Команда <code>«выключи тренера»</code> возвращает обычную игру. Пока тренер выключен, оценки
        и подсказки недоступны: так партия против Stockfish остаётся спортивной.
      </p>

      <h2>Оценка позиции словами и числом</h2>
      <p>
        <code>«Оцени позицию»</code> отвечает по-человечески: у кого перевес, насколько он велик
        и чем он держится. <code>«Назови оценку числом»</code> даёт привычную шахматистам числовую
        оценку. Между этими двумя формами можно переключаться сколько угодно раз — позиция не меняется.
      </p>

      <h2>Ходы-кандидаты и угрозы</h2>
      <ul>
        <li><code>«Какие ходы хорошие»</code> — до трёх ходов-кандидатов, от лучшего к слабейшему</li>
        <li><code>«Чем ты угрожаешь»</code> — что готовит движок в ответ</li>
        <li><code>«Почему ты так сходил»</code> — цель последнего хода Алисы</li>
      </ul>

      <h2>Разбор варианта без хода</h2>
      <p>
        Вопрос <code>«что будет, если я сыграю коня эф три»</code> разбирает ход, но не играет его:
        позиция на доске не сдвигается. Это способ проверить идею до того, как за неё придётся отвечать.
      </p>

      <h2>Подсказки по ступеням</h2>
      <p>
        <code>«Подскажи»</code> не выдаёт готовый ход сразу. Сначала звучит направление идеи, затем
        участок доски и только потом сам ход — можно остановиться на любой ступени и додумать самому.
        Вопрос <code>«где я ошибся»</code> находит последнюю существенную ошибку в партии.
      </p>

      <h2>Предупреждение о грубой ошибке</h2>
      <p>
        Если ход заметно испортил позицию, тренер скажет об этом сразу после него и назовёт цену
        ошибки в пешках. Решение остаётся за игроком: <code>«оставить мой ход»</code> подтверждает
        ход как есть, <code>«вернуть ход»</code> отменяет его. Навык не переигрывает за вас
        и не отменяет ход молча.
      </p>

      <h2>Разбор сыгранной партии</h2>
      <p>
        После партии скажите <code>«разбери партию»</code>. Навык назовёт результат, перелом партии,
        главную ошибку, лучший практический ход и число неточностей. <code>«Где перелом»</code>,
        <code>«главная ошибка»</code> и <code>«сколько я ошибся»</code> спрашивают о том же по частям,
        <code>«продиктуй ходы»</code> читает партию постранично, <code>«покажи pgn»</code> выдаёт запись,
        а <code>«сыграть эту позицию заново»</code> начинает тренировку от переломного момента.
      </p>

      <h2>Что дальше</h2>
      <p>
        Полный перечень фраз — в <a href="{COMMANDS_PATH}">списке голосовых команд</a>.
        Если вы ещё не начинали, откройте <a href="{HOW_TO_PLAY_PATH}">инструкцию по первой партии</a>,
        а для отработки счёта вариантов посмотрите <a href="{PUZZLES_PATH}">голосовые шахматные задачи</a>.
      </p>
    </section>
""",
)

_PUZZLES_TITLE = "Шахматные задачи голосом"
_PUZZLES_CRUMB_SCHEMA = _breadcrumb_schema(_PUZZLES_TITLE, PUZZLES_PATH)

PUZZLES_PAGE_HTML = _document(
    title="Шахматные задачи голосом — мат в два хода, вилки и связки с Алисой",
    description=(
        "Голосовые шахматные задачи в Алисе: мат в один и два хода, вилка, связка, сквозной удар, "
        "мат по последней горизонтали. Позиция читается вслух, есть подсказки и серия решений."
    ),
    path=PUZZLES_PATH,
    structured_data=[_page_schema(_PUZZLES_TITLE, PUZZLES_PATH), _PUZZLES_CRUMB_SCHEMA],
    body=f"""    <header>
      <a class="piece home" href="{LANDING_PATH}" aria-label="На главную «Шахматы с Юрой»">♝</a>
      <h1>{_PUZZLES_TITLE}</h1>
      <p class="lead">
        Задача читается вслух — позицию нужно удержать в голове и найти решение на слух.
        Это тренирует ровно тот навык, который отличает сильного игрока: счёт вариантов без доски.
      </p>
    </header>

    <section class="article">
      <h2>Как открыть задачу</h2>
      <p>
        Скажите <code>«дай задачу»</code>. Прогресс задач хранится отдельно от партии, поэтому
        начатая игра никуда не денется: <code>«вернуться к партии»</code> возвращает к ней в той же позиции.
      </p>

      <h2>Темы задач</h2>
      <ul>
        <li><strong>Мат в один ход</strong> — <code>«задача на мат в один»</code></li>
        <li><strong>Мат в два хода</strong> — <code>«задача на мат в два хода»</code></li>
        <li><strong>Вилка</strong> — двойной удар одной фигурой</li>
        <li><strong>Связка</strong> — фигура не может уйти, не подставив ту, что за ней</li>
        <li><strong>Сквозной удар</strong> — фигура уходит и открывает удар по той, что стояла позади</li>
        <li><strong>Мат по последней горизонтали</strong></li>
        <li><strong>Вскрытое нападение</strong> и <strong>висячая фигура</strong></li>
      </ul>

      <h2>Если позиция не удержалась в голове</h2>
      <p>
        <code>«Повтори задачу»</code> читает позицию заново — столько раз, сколько нужно, без штрафа.
        <code>«Подскажи»</code> даёт подсказку по ступеням, <code>«покажи решение»</code> продиктует
        оставшиеся ходы решения и закроет задачу, а <code>«следующая задача»</code> берёт новую.
      </p>

      <h2>Серия решений</h2>
      <p>
        Вопрос <code>«какая у меня серия»</code> называет счёт задач, решённых подряд. Серия сохраняется
        между сессиями Алисы, поэтому её можно набирать понемногу каждый день.
      </p>

      <h2>Зачем решать задачи на слух</h2>
      <p>
        Решение с доски опирается на зрение: варианты проверяются взглядом. На слух так не выйдет —
        позицию приходится строить в голове и держать её, пока считаешь. Это тот же механизм, что
        и в <a href="{BLINDFOLD_PATH}">игре вслепую</a>, но короткими подходами и с точным ответом
        в конце. Незрячим игрокам задачи доступны наравне со всем остальным — см.
        <a href="{ACCESSIBILITY_PATH}">игру без экрана</a>.
      </p>

      <h2>Что дальше</h2>
      <p>
        Разбирать собственные ошибки помогает <a href="{COACH_PATH}">режим тренера</a>,
        а полный список фраз собран в <a href="{COMMANDS_PATH}">голосовых командах</a>.
      </p>
    </section>
""",
)

_BLINDFOLD_TITLE = "Шахматы вслепую с Алисой"
_BLINDFOLD_CRUMB_SCHEMA = _breadcrumb_schema(_BLINDFOLD_TITLE, BLINDFOLD_PATH)

BLINDFOLD_PAGE_HTML = _document(
    title="Шахматы вслепую — играть и тренироваться голосом с Алисой",
    description=(
        "Как научиться играть в шахматы вслепую: партия голосом против Stockfish, позиция и история "
        "ходов на слух, задачи без доски. Тренировка памяти и счёта вариантов с Алисой."
    ),
    path=BLINDFOLD_PATH,
    structured_data=[_page_schema(_BLINDFOLD_TITLE, BLINDFOLD_PATH), _BLINDFOLD_CRUMB_SCHEMA],
    body=f"""    <header>
      <a class="piece home" href="{LANDING_PATH}" aria-label="На главную «Шахматы с Юрой»">♘</a>
      <h1>{_BLINDFOLD_TITLE}</h1>
      <p class="lead">
        Игра вслепую всегда упиралась в партнёра: кто-то должен вести доску и называть ходы.
        Алиса делает это бесконечно терпеливо — и не показывает подсказок, пока их не попросят.
      </p>
    </header>

    <section class="article">
      <h2>Почему голосовой навык — удобный тренажёр вслепую</h2>
      <p>
        Обычный шахматный сайт с выключенной доской всё равно оставляет соблазн подсмотреть.
        На колонке без экрана смотреть просто не на что: партия существует только в звуке.
        На устройстве с экраном доска всё же показывается — если она мешает тренировке,
        отверните экран или возьмите колонку. Соперник — Stockfish с двадцатью уровнями силы,
        поэтому нагрузку можно поднимать по мере роста.
      </p>

      <h2>Как начать</h2>
      <ol class="steps">
        <li>
          <strong>Запустите навык</strong>
          «Алиса, запусти навык Шахматы с Юрой».
        </li>
        <li>
          <strong>Возьмите посильный уровень</strong>
          «Новая игра белыми уровень три». Вслепую даже слабый уровень ощущается сложнее обычного:
          силы уходят на удержание позиции, а не на счёт.
        </li>
        <li>
          <strong>Играйте, не спрашивая позицию</strong>
          Цель тренировки — дойти как можно дальше без команды «какая позиция».
        </li>
      </ol>

      <h2>Страховка, когда позиция поплыла</h2>
      <p>
        Тренировка не должна заканчиваться тупиком. В любой момент доступны
        <code>«какая позиция»</code> — полное чтение доски по горизонталям,
        <code>«что на е четыре»</code> — одна клетка, <code>«где белые слоны»</code> — одна фигура,
        <code>«какой был последний ход»</code> и <code>«что делали чёрные четыре хода назад»</code> —
        история. Пользоваться ими не стыдно: полезнее восстановить картину и доиграть, чем бросить партию.
      </p>

      <h2>Как наращивать сложность</h2>
      <ul>
        <li>Считайте, сколько ходов удаётся продержаться без единого вопроса о позиции</li>
        <li>Начните с уточнений по одной клетке, а полное чтение доски оставьте на крайний случай</li>
        <li>Поднимайте уровень Stockfish командой <code>«сыграем сложнее»</code></li>
        <li>Между партиями решайте <a href="{PUZZLES_PATH}">задачи на слух</a> — короткие подходы
            тренируют то же удержание позиции</li>
      </ul>

      <h2>Разбор после партии</h2>
      <p>
        Вслепую ошибки чаще всего рождаются там, где картинка в голове разошлась с доской.
        <a href="{COACH_PATH}">Разбор партии</a> показывает перелом и главную ошибку, а
        <code>«сыграть эту позицию заново»</code> позволяет вернуться к критическому моменту
        и попробовать иначе.
      </p>

      <h2>Не только тренировка</h2>
      <p>
        Для незрячих и слабовидящих игроков это не упражнение, а обычный способ играть.
        Навык изначально проектировался под них — подробности на странице
        <a href="{ACCESSIBILITY_PATH}">«Шахматы для незрячих голосом»</a>.
      </p>
    </section>
""",
)


def render_landing_page(dashboard: str) -> str:
    return LANDING_PAGE_HTML.replace("{{ dashboard }}", dashboard)
