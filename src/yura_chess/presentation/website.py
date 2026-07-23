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
        content="Голосовые шахматы с Алисой против Stockfish: уровни сложности, объяснения ходов и сохранение партии.">
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
        content="Полноценная партия голосом против Stockfish с уровнями сложности и объяснениями ходов.">
  <meta property="og:url" content="https://chess.waxim.ru/">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="Шахматы с Юрой — голосовые шахматы с Алисой">
  <meta name="twitter:description" content="Играйте в шахматы голосом против Stockfish прямо в Алисе.">
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
        "description": "Голосовой навык Алисы для полноценных шахматных партий против Stockfish.",
        "applicationCategory": "GameApplication",
        "operatingSystem": "Яндекс Алиса",
        "inLanguage": "ru-RU",
        "isAccessibleForFree": true,
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
            "name": "Как запустить голосовые шахматы с Алисой?",
            "acceptedAnswer": {
              "@type": "Answer",
              "text": "Скажите: Алиса, запусти навык Шахматы с Юрой."
            }
          },
          {
            "@type": "Question",
            "name": "Нужен ли экран для игры?",
            "acceptedAnswer": {
              "@type": "Answer",
              "text": "Нет. Вся игра доступна голосом; экран только показывает текущую позицию."
            }
          },
          {
            "@type": "Question",
            "name": "Сохраняется ли незаконченная партия?",
            "acceptedAnswer": {
              "@type": "Answer",
              "text": "Да. Навык сохраняет партию и предлагает продолжить её при следующем запуске."
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
    .lead { max-width: 760px; margin: 0 auto; color: var(--muted); font-size: clamp(19px, 3vw, 24px); }
    .launch {
      display: inline-block;
      margin-top: 30px;
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
    }
    .support-action:hover { background: #f3ca78; }
    .support .support-note { margin-top: 16px; margin-bottom: 0; font-size: 14px; }
    footer { padding: 24px 0 52px; color: var(--muted); text-align: center; }
    @media (max-width: 760px) {
      header { padding-top: 46px; }
      section { padding: 24px; }
      .grid { grid-template-columns: 1fr; }
      .faq { grid-template-columns: 1fr; }
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
        Полноценная партия голосом против сильного движка Stockfish — с естественными командами,
        понятными объяснениями и сохранением игры.
      </p>
      <div class="launch">Скажите: «Алиса, запусти навык Шахматы с Юрой»</div>
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
          На устройствах с экраном показывается доска с координатами и последним ходом.
          Экран не обязателен для игры.
        </div>
      </div>
    </section>

    <section>
      <h2>Что можно сказать</h2>
      <ul>
        <li><code>«Пешка е два е четыре»</code></li>
        <li><code>«Что на эф три?»</code></li>
        <li><code>«Где белые слоны?»</code></li>
        <li><code>«Какой уровень сложности?»</code></li>
        <li><code>«Что сделали чёрные четыре хода назад?»</code></li>
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
          <dt>Нужен ли экран?</dt>
          <dd>Нет. Вся партия доступна голосом, а экран только дополняет игру изображением доски.</dd>
        </div>
        <div>
          <dt>Сохраняется ли партия?</dt>
          <dd>Да. Незаконченная партия сохраняется, и при следующем запуске её можно продолжить.</dd>
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
</body>
</html>"""
)


def render_landing_page(dashboard: str) -> str:
    return LANDING_PAGE_HTML.replace("{{ dashboard }}", dashboard)
