"""Public website shown to players, moderators and Yandex Webmaster."""

from __future__ import annotations

from yura_chess.presentation.dashboard import DASHBOARD_CSS

WEBMASTER_VERIFICATION_PATH = "/yandex_67cb474818f8d2b2.html"
ROBOTS_PATH = "/robots.txt"
SITEMAP_PATH = "/sitemap.xml"
PUBLIC_SITE_URL = "https://chess.waxim.ru/"
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
Disallow: /health/
Clean-param: source&period /
Sitemap: {PUBLIC_SITE_URL}sitemap.xml
"""
SITEMAP_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{PUBLIC_SITE_URL}</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""

LANDING_PAGE_HTML = (
    """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description"
        content="Голосовые шахматы с Алисой против Stockfish: тренер, разбор партий,
                 шахматные задачи, объяснения и сохранение игры.">
  <meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">
  <meta name="theme-color" content="#171613">
  <link rel="canonical" href="https://chess.waxim.ru/">
  <link rel="alternate" hreflang="ru-RU" href="https://chess.waxim.ru/">
  <link rel="alternate" hreflang="x-default" href="https://chess.waxim.ru/">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <title>Шахматы с Юрой — голосовые шахматы с Алисой</title>
  <meta property="og:type" content="website">
  <meta property="og:locale" content="ru_RU">
  <meta property="og:site_name" content="Шахматы с Юрой">
  <meta property="og:title" content="Шахматы с Юрой — голосовые шахматы с Алисой">
  <meta property="og:description"
        content="Играйте голосом против Stockfish, тренируйтесь, разбирайте партии и решайте шахматные задачи.">
  <meta property="og:url" content="https://chess.waxim.ru/">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Шахматы с Юрой — голосовые шахматы с Алисой">
  <meta name="twitter:description" content="Играйте, тренируйтесь и решайте шахматные задачи голосом прямо в Алисе.">
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": "https://chess.waxim.ru/#website",
        "url": "https://chess.waxim.ru/",
        "name": "Шахматы с Юрой",
        "description": "Голосовые шахматы с Алисой против Stockfish",
        "inLanguage": "ru-RU"
      },
      {
        "@type": "SoftwareApplication",
        "@id": "https://chess.waxim.ru/#skill",
        "url": "https://chess.waxim.ru/",
        "name": "Шахматы с Юрой",
        "description": "Голосовой навык Алисы для партий против Stockfish, тренировок, разбора игр и шахматных задач.",
        "applicationCategory": "GameApplication",
        "operatingSystem": "Яндекс Алиса",
        "inLanguage": "ru-RU",
        "isAccessibleForFree": true,
        "featureList": [
          "Голосовые шахматные партии против Stockfish",
          "Режим тренера с оценкой, вариантами и подсказками",
          "Разбор завершённых партий и PGN",
          "Голосовые шахматные задачи и серии решений"
        ],
        "offers": {
          "@type": "Offer",
          "price": "0",
          "priceCurrency": "RUB"
        }
      },
      {
        "@type": "FAQPage",
        "mainEntity": [
          {
            "@type": "Question",
            "name": "Как запустить навык?",
            "acceptedAnswer": {
              "@type": "Answer",
              "text": "Скажите: Алиса, запусти навык Шахматы с Юрой."
            }
          },
          {
            "@type": "Question",
            "name": "Есть ли режим тренера?",
            "acceptedAnswer": {
              "@type": "Answer",
              "text": "Да. Тренер даёт оценку позиции, варианты ходов, объяснения и ступенчатые подсказки."
            }
          },
          {
            "@type": "Question",
            "name": "Можно ли разобрать партию?",
            "acceptedAnswer": {
              "@type": "Answer",
              "text": "Да. Навык найдёт перелом и главную ошибку, продиктует ходы или покажет PGN."
            }
          },
          {
            "@type": "Question",
            "name": "Есть ли шахматные задачи?",
            "acceptedAnswer": {
              "@type": "Answer",
              "text": "Да. Доступны мат в один и два хода, вилки, связки, сквозные удары, подсказки и серии решений."
            }
          },
          {
            "@type": "Question",
            "name": "Можно ли настроить речь и доску?",
            "acceptedAnswer": {
              "@type": "Answer",
              "text": "Да. Можно менять подробность и темп речи, стиль произнесения ходов и ориентацию экранной доски."
            }
          },
          {
            "@type": "Question",
            "name": "Как узнать все команды?",
            "acceptedAnswer": {
              "@type": "Answer",
              "text": "Скажите «что ты умеешь?» для короткого меню или «все команды» для полного каталога."
            }
          }
        ]
      }
    ]
  }
  </script>
  <style>
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
    body {
      margin: 0;
      background:
        radial-gradient(circle at 20% 0%, #3a2f1f 0, transparent 36rem),
        var(--bg);
      color: var(--text);
      font: 17px/1.6 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main { width: min(1080px, calc(100% - 32px)); margin: 0 auto; }
    header { padding: 72px 0 52px; text-align: center; }
    .piece { color: var(--gold); font-size: clamp(72px, 12vw, 126px); line-height: 1; }
    h1 { margin: 18px 0 12px; font-size: clamp(38px, 7vw, 72px); line-height: 1.05; }
    h2 { margin: 0 0 18px; font-size: clamp(26px, 4vw, 38px); }
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
      display: inline-block;
      padding: 14px 22px;
      border: 1px solid #765f36;
      border-radius: 999px;
      background: #2d271c;
      color: var(--gold);
      font-weight: 700;
    }
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
    .faq { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 0; }
    .faq div { padding: 22px; border-radius: 18px; background: #1d1c19; }
    .faq dt { color: var(--gold); font-weight: 700; }
    .faq dd { margin: 8px 0 0; color: var(--muted); }
    ul { margin: 0; padding-left: 22px; }
    li + li { margin-top: 8px; }
    .section-intro { margin: -6px 0 20px; color: var(--muted); }
    .command-list { columns: 2; column-gap: 48px; }
    .command-list li { break-inside: avoid; }
    code { color: var(--gold); font: inherit; }
    a { color: var(--gold); }
    .support { text-align: center; }
    .support p { max-width: 760px; margin: 0 auto 20px; color: var(--muted); }
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
    .support-action:hover {
      background: #f3ca78;
      transform: translateY(-3px) scale(1.025);
      box-shadow: 0 14px 34px #e8b8542e;
    }
    .support .support-note { margin-top: 16px; margin-bottom: 0; font-size: 14px; }
    footer { padding: 24px 0 52px; color: var(--muted); text-align: center; }
    :root {
      --spring: linear(
        0, .009, .035 2.1%, .141 4.4%, .723 12.9%, .938 16.7%, 1.017 20.4%,
        1.051 24.7%, 1.019 30.1%, .995 36%, 1.002 43%, 1
      );
    }
    .feature, .faq > div, section {
      transition: transform 520ms var(--spring), border-color 220ms ease, box-shadow 220ms ease;
    }
    .has-motion .motion-item { opacity: 0; filter: blur(4px); transform: translateY(28px) scale(.985); }
    .has-motion .motion-item.is-visible {
      animation: page-reveal 760ms var(--spring) both;
      animation-delay: var(--motion-delay, 0ms);
    }
    @keyframes page-reveal { to { opacity: 1; filter: none; transform: none; } }
    @media (hover: hover) {
      .feature:hover, .faq > div:hover {
        border-color: #e8b85466;
        transform: translateY(-4px);
        box-shadow: 0 18px 42px #0000002e;
      }
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
    }
    @media (max-width: 760px) {
      header { padding-top: 46px; }
      section { padding: 24px; }
      .grid { grid-template-columns: 1fr; }
      .faq { grid-template-columns: 1fr; }
      .command-list { columns: 1; }
    }
"""
    + DASHBOARD_CSS
    + """
  </style>
</head>
<body>
  <main>
    <header>
      <div class="piece" aria-hidden="true">♞</div>
      <h1>Шахматы с Юрой</h1>
      <p class="lead">
        Полноценная партия голосом против сильного движка Stockfish — с&nbsp;естественными командами,
        понятными объяснениями и сохранением игры.
      </p>
      <div class="hero-actions">
        <div class="launch">Скажите: «Алиса, запусти навык Шахматы с Юрой»</div>
        <a
          class="support-action hero-support"
          href="https://pay.cloudtips.ru/p/f604e20f"
          target="_blank"
          rel="noopener noreferrer nofollow"
        >Поддержать проект</a>
      </div>
    </header>

    <section>
      <h2>Настоящие шахматы в Алисе</h2>
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
          <strong>Смотрите позицию</strong>
          На устройствах с экраном показывается доска с координатами и последним ходом. Экран не обязателен для игры.
        </div>
      </div>
    </section>

    <section>
      <h2>Что можно сказать</h2>
      <p class="section-intro">
        Это только примеры. Скажите «что ты умеешь?» или «все команды», чтобы услышать полный каталог.
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
      <dl class="faq">
        <div>
          <dt>Как запустить навык?</dt>
          <dd>Скажите: «Алиса, запусти навык Шахматы с Юрой».</dd>
        </div>
        <div>
          <dt>Есть ли режим тренера?</dt>
          <dd>Да. Тренер даёт оценку позиции, варианты ходов, объяснения и ступенчатые подсказки.</dd>
        </div>
        <div>
          <dt>Можно ли разобрать партию?</dt>
          <dd>Да. Навык найдёт перелом и главную ошибку, продиктует ходы или покажет PGN.</dd>
        </div>
        <div>
          <dt>Есть ли шахматные задачи?</dt>
          <dd>Да. Доступны мат в один и два хода, вилки, связки, сквозные удары, подсказки и серии решений.</dd>
        </div>
        <div>
          <dt>Можно ли настроить речь и доску?</dt>
          <dd>Да. Можно менять подробность и темп речи, стиль произнесения ходов и ориентацию экранной доски.</dd>
        </div>
        <div>
          <dt>Как узнать все команды?</dt>
          <dd>Скажите «что ты умеешь?» для короткого меню или «все команды» для полного каталога.</dd>
        </div>
      </dl>
    </section>

    {{ dashboard }}

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

    <footer>Шахматы с Юрой · Голосовая игра с Алисой</footer>
  </main>
  <script>
    (() => {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const showLatest = (root = document) => {
        const chart = root.querySelector(".stats-chart");
        if (chart) chart.scrollLeft = chart.scrollWidth - chart.clientWidth;
      };
      requestAnimationFrame(() => showLatest());
      window.addEventListener("load", () => showLatest(), { once: true });

      const formatter = new Intl.NumberFormat("ru-RU");
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
        requestAnimationFrame(() => showLatest(section));
        if (!reveal) return;
        section.querySelectorAll(".stats-cards, .stats-chart").forEach((element) => reveal.observe(element));
      };
      const loadStatistics = async (url, updateHistory = true) => {
        const current = document.querySelector("#statistics");
        if (!current) return;
        current.setAttribute("aria-busy", "true");
        try {
          const response = await fetch(url, { headers: { "X-Requested-With": "statistics" } });
          if (!response.ok) throw new Error(`Statistics request failed: ${response.status}`);
          const page = new DOMParser().parseFromString(await response.text(), "text/html");
          const replacement = page.querySelector("#statistics");
          if (!replacement) throw new Error("Statistics section is missing");
          const scrollPosition = window.scrollY;
          current.replaceWith(replacement);
          if (updateHistory) history.pushState({ statistics: true }, "", url);
          window.scrollTo({ top: scrollPosition });
          prepareStatistics(replacement);
        } catch (error) {
          window.location.assign(url);
        }
      };
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
            entry.target.querySelectorAll("[data-count]").forEach(animateCounter);
          }
          observer.unobserve(entry.target);
        });
      }, { threshold: 0.14, rootMargin: "0px 0px -5%" });

      document.querySelectorAll("main > header, main > section, .feature, .faq > div").forEach((element, index) => {
        element.classList.add("motion-item");
        element.style.setProperty("--motion-delay", `${(index % 4) * 45}ms`);
        reveal.observe(element);
      });
      prepareStatistics(document.querySelector("#statistics"));
    })();
  </script>
</body>
</html>"""
)


def render_landing_page(dashboard: str) -> str:
    return LANDING_PAGE_HTML.replace("{{ dashboard }}", dashboard)
