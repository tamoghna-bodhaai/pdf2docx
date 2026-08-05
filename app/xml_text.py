"""The characters XML refuses to carry, removed before they can reach lxml."""

from __future__ import annotations

import re

# XML 1.0 admits tab, newline, carriage return and #x20 upward — nothing else
# from the C0 block, and no surrogates. lxml enforces that when the string is
# assigned, so one stray character anywhere aborts the whole document rather
# than spoiling one word. Publishing software leaves plenty of them behind in
# a PDF's text layer (InDesign writes U+0007 for "indent to here"), and a model
# can emit one too, so every string from either source passes through here on
# its way in.
_ILLEGAL = re.compile(
    "[^"
    "\t\n\r"  # the only control characters XML allows
    " -퟿-�"  # the printable range, minus the surrogates
    "\U00010000-\U0010FFFF"  # and the astral planes
    "]"
)


def xml_safe(text: str) -> str:
    """Replace characters XML forbids with spaces.

    Spaces rather than nothing: the offenders are overwhelmingly typesetting
    spacers that occupy real width — the U+0007 in the PDFs that prompted this
    measures a quarter em, exactly a space — so dropping them would run words
    together. Where one genuinely has no width, the run's width compensation
    absorbs the difference.
    """
    return _ILLEGAL.sub(" ", text) if text else text
