# Участие в проекте

## Лицензия вклада

Отправляя pull request, вы соглашаетесь с двумя условиями:

1. Ваш вклад распространяется на условиях GPL-3.0-or-later, как и весь проект.
2. Вы даёте автору проекта (Blaryx) бессрочное, безотзывное, неисключительное
   право использовать ваш вклад и лицензировать его на любых других условиях,
   включая проприетарные, без дополнительного согласования и без выплат.

Второй пункт нужен для того, чтобы проект мог сменить лицензию в будущем без
поиска всех участников. Он не отбирает у вас никаких прав: вы остаётесь
правообладателем своего кода и вправе использовать его где угодно.

*By submitting a pull request you agree that your contribution is licensed under
GPL-3.0-or-later, and you grant Blaryx a perpetual, irrevocable, non-exclusive
right to use your contribution and to relicense it under any other terms,
including proprietary ones, without further approval or compensation. You retain
copyright in your contribution.*

## Перед pull request

Прочитайте [`AGENTS.md`](AGENTS.md): продуктовые инварианты там не пожелания, а
условия работы навыка на устройстве без экрана.

Прогоните тот же набор проверок, что и CI:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Голосовые формулировки берите из реальных расшифровок Алисы, а не придумывайте
синонимы. Каждая новая фраза, переход состояния и диагностика недопустимого хода
должны быть покрыты тестом.
