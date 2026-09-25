"""How the HTML digest looks.

Presentation only, no words. The wording still comes from
`config/digest_copy.py`, so the plain text and HTML versions can never say
different things.

Two constraints shape everything here. Email clients strip <style> blocks
and external stylesheets, so every rule is inline on the element. And the
PRD's second-highest risk is the digest silently landing in Promotions, so
there are no images, no web fonts, no tracking pixels, and no marketing
markup — the things that actually trigger it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DigestStyle:
    """Fonts and colours for the HTML digest.

    font is a full CSS font-family stack. The first entry is what you get
    when the client has it; the rest are fallbacks, and every stack must
    end in a generic family so something sensible renders regardless.

    accent and accent_bg are used only for the cleaner-week note and prep
    chores, which are the exception in any given week and the thing most
    worth not missing.
    """

    font: str
    page_bg: str
    card_bg: str
    border: str
    text: str
    muted: str
    accent: str
    accent_bg: str
    width_px: int


DEFAULT_DIGEST_STYLE = DigestStyle(
    font="'Times New Roman', Times, Georgia, serif",
    page_bg="#faf9f7",
    card_bg="#ffffff",
    border="#e6e1d8",
    text="#1c1917",
    muted="#7d7467",
    accent="#9a6b1f",
    accent_bg="#fdf7ea",
    width_px=540,
)
