"""The image a link to the site unfurls into, drawn once at import.

Messengers and social networks never execute the page, so a shared link without
`og:image` gets whatever the platform invents — usually nothing. The card is
rendered here rather than committed as a binary so the wording stays in the same
review as the pages it advertises.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# 1200x630 is what every platform crops against; anything else gets letterboxed.
CARD_WIDTH = 1200
CARD_HEIGHT = 630
SOCIAL_CARD_PATH = "/og.png"
SOCIAL_CARD_ALT = "Шахматы с Юрой — голосовые шахматы с Алисой"

_BACKGROUND = (23, 22, 19)
_GOLD = (232, 189, 102)
_TEXT = (246, 240, 227)
_MUTED = (201, 192, 174)

# Same fallback chain as the board card: the deployment image ships DejaVu, a
# developer Mac has Arial Unicode, and Pillow's bundled font is the last resort.
_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)

_Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


def _font(size: int) -> _Font:
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _render() -> bytes:
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), _BACKGROUND)
    canvas = ImageDraw.Draw(card)

    canvas.text((80, 118), "♞", font=_font(132), fill=_GOLD)
    canvas.text((80, 286), "Шахматы с Юрой", font=_font(76), fill=_TEXT)
    canvas.text((80, 392), "Голосовые шахматы с Алисой", font=_font(44), fill=_GOLD)
    canvas.text(
        (80, 470),
        "Партия против Stockfish, тренер, разбор и задачи —\nполностью без экрана",
        font=_font(32),
        fill=_MUTED,
        spacing=12,
    )
    canvas.line(((80, 250), (240, 250)), fill=_GOLD, width=5)

    buffer = BytesIO()
    card.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


SOCIAL_CARD_PNG = _render()
