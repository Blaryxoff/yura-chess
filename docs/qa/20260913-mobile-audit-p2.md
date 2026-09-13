# P2: мобильная проверка критичных страниц — статус

Ссылка на карточку: `docs/plans/product/20260912-seo-improvement.md`, приоритет P2.
Проверяемые страницы: `/`, `/how-to-play`, `/commands`, `/coach`, `/blindfold`.

## Что сделано

### Инструмент для замера

Добавлен `scripts/mobile_audit.py` — прогоняет мобильный PageSpeed Insights
(`strategy=mobile`, категории `performance`, `accessibility`, `seo`,
`best-practices`) по всем пяти страницам и сохраняет сырые отчёты и сводку в
`docs/qa/mobile-audits/<дата>-<label>/`:

```bash
uv run python scripts/mobile_audit.py --label pre-release
uv run python scripts/mobile_audit.py --label post-release
```

Список страниц берётся из констант `yura_chess.presentation.website`, а не
захардкожен — как и в `scripts/submit_indexnow.py`, страница, отсутствующая в
исходнике сайта, не может незаметно выпасть из аудита.

### Структурная проверка (без Lighthouse)

Проверено чтением `src/yura_chess/presentation/website.py` (общий `SITE_CSS` и
разметка всех пяти страниц):

- **Команда запуска и польза в первом экране** — `header` каждой страницы
  открывается фразой-действием (`«Запусти навык Шахматы с Юрой»` на главной,
  аналогично на остальных) и одним abzацем `lead` до какой-либо другой секции;
  ничего обязательного не вынесено ниже.
- **Навигация читаема** — `.site-nav` на `max-width: 760px` (`website.py:411-421`)
  разворачивается в полноширинный флекс-ряд с `min-height: 40px` на пункт.
- **Горизонтального скролла нет** — `main` ограничен `min(1080px, calc(100% - 32px))`
  (`calc(100% - 20px)` на `max-width: 520px`), контент страниц — только текст,
  списки и `code`, без таблиц или элементов фиксированной ширины шире экрана.
- **`prefers-reduced-motion` поддержан** — `@media (prefers-reduced-motion: reduce)`
  (`website.py:386-400`) отключает transition/animation и принудительно
  показывает `.motion-item` и hover-трансформации без сдвига.

Ни один из этих пунктов не потребовал правки — текущая вёрстка уже это
обеспечивает (см. также `9d4d724 fix(site): fit the statistics cards on a
phone`).

## Заблокировано в этой среде

Реальный мобильный Lighthouse/PageSpeed прогон **не выполнен** — окружение
задачи не даёт ни одного рабочего канала:

1. **Headless-браузер (chrome-devtools MCP)** — `new_page`/`lighthouse_audit`
   падают с `Missing X server to start the headful browser`. MCP-сервер уже
   запущен без `DISPLAY`; установка и запуск `Xvfb` в этой сессии не помогает,
   потому что переменные окружения уже стартовавшего процесса не меняются задним
   числом — нужен перезапуск MCP-сервера с headless-режимом или рабочим
   `DISPLAY`, которого у этой сессии нет.
2. **Анонимный PageSpeed Insights API** — прокси песочницы отвечает `429
   RESOURCE_EXHAUSTED` с `quota_limit_value: 0` для проекта по умолчанию, то
   есть без `YURA_CHESS_PAGESPEED_API_KEY` (реального Google API-ключа) канал
   недоступен вообще, не только заквотирован.

`scripts/mobile_audit.py` проверен запуском против продакшна:
логика запроса и сохранения корректна, отчёт падает именно на 429 из пункта 2 —
других ошибок нет.

## Что нужно, чтобы закрыть карточку

Один из:

- сессия с рабочим `DISPLAY`/headless Chrome для `chrome-devtools` MCP, или
- `YURA_CHESS_PAGESPEED_API_KEY` (Google API-ключ с включённым PageSpeed
  Insights API) в среде, у которой есть исходящий доступ к
  `www.googleapis.com` без нулевой квоты по умолчанию.

С любым из них: прогнать `scripts/mobile_audit.py --label pre-release` на
сборке с изменениями карточек P0/P1 до публикации и `--label post-release`
после неё, сохранить оба `docs/qa/mobile-audits/*/summary.md`. Правки
производительности вносятся только по конкретному узкому месту, которое
покажет один из этих прогонов — сейчас такого измерения нет, поэтому
производительность кода не менялась.
