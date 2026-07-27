"""Small Russian-language inflection helpers shared by presentation modules."""

from __future__ import annotations


def plural_form(value: int, forms: tuple[str, str, str]) -> str:
    """Choose nominative singular, genitive singular or genitive plural."""
    if value % 100 in range(11, 15):
        return forms[2]
    if value % 10 == 1:
        return forms[0]
    if value % 10 in (2, 3, 4):
        return forms[1]
    return forms[2]
