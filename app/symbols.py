"""The Symbol font's private-use codes, mapped back to real Unicode.

A PDF that sets its mathematics with the Adobe Symbol font — which is most
material produced through Word's older equation editor, and a great deal of
textbook typesetting — encodes those glyphs at 0xF000 + the font's own byte, deep
in the Basic Multilingual Plane's private use area. There is no ToUnicode table
to undo it, so the text layer hands out U+F071 where the page shows θ.

Left alone those characters are worse than wrong. U+F071 is category Co, so
`str.isprintable()` calls it unprintable and a span made only of symbols is
discarded as a control-code run; a symbol that survives inside a mixed span is
mapped to a text font that has no glyph at that code point and draws nothing at
all. Either way the reader loses every π, θ, ≥, √ and bracket on the page while
the words around them come through perfectly.

The table below is the Symbol font's encoding as published in its AFM, so the
mapping is exact rather than a guess.
"""

from __future__ import annotations

# Where the private-use block for an 8-bit symbol font starts.
_PUA_BASE = 0xF000
_PUA_END = 0xF0FF

# Adobe Symbol encoding: byte -> the Unicode character the glyph actually is.
_SYMBOL: dict[int, str] = {
    0x20: " ",   0x21: "!",   0x22: "∀",  0x23: "#",  0x24: "∃",  0x25: "%",
    0x26: "&",   0x27: "∋",   0x28: "(",  0x29: ")",  0x2A: "∗",  0x2B: "+",
    0x2C: ",",   0x2D: "−",   0x2E: ".",  0x2F: "/",
    0x30: "0",   0x31: "1",   0x32: "2",  0x33: "3",  0x34: "4",  0x35: "5",
    0x36: "6",   0x37: "7",   0x38: "8",  0x39: "9",
    0x3A: ":",   0x3B: ";",   0x3C: "<",  0x3D: "=",  0x3E: ">",  0x3F: "?",
    # Upper case: Greek capitals, with a few relations mixed in.
    0x40: "≅",   0x41: "Α",   0x42: "Β",  0x43: "Χ",  0x44: "Δ",  0x45: "Ε",
    0x46: "Φ",   0x47: "Γ",   0x48: "Η",  0x49: "Ι",  0x4A: "ϑ",  0x4B: "Κ",
    0x4C: "Λ",   0x4D: "Μ",   0x4E: "Ν",  0x4F: "Ο",  0x50: "Π",  0x51: "Θ",
    0x52: "Ρ",   0x53: "Σ",   0x54: "Τ",  0x55: "Υ",  0x56: "ς",  0x57: "Ω",
    0x58: "Ξ",   0x59: "Ψ",   0x5A: "Ζ",
    0x5B: "[",   0x5C: "∴",   0x5D: "]",  0x5E: "⊥",  0x5F: "_",  0x60: "‾",
    # Lower case: Greek minuscules.
    0x61: "α",   0x62: "β",   0x63: "χ",  0x64: "δ",  0x65: "ε",  0x66: "φ",
    0x67: "γ",   0x68: "η",   0x69: "ι",  0x6A: "ϕ",  0x6B: "κ",  0x6C: "λ",
    0x6D: "μ",   0x6E: "ν",   0x6F: "ο",  0x70: "π",  0x71: "θ",  0x72: "ρ",
    0x73: "σ",   0x74: "τ",   0x75: "υ",  0x76: "ϖ",  0x77: "ω",  0x78: "ξ",
    0x79: "ψ",   0x7A: "ζ",
    0x7B: "{",   0x7C: "|",   0x7D: "}",  0x7E: "∼",
    # High half: relations, operators, arrows, set theory.
    0xA0: "€",   0xA1: "ϒ",   0xA2: "′",  0xA3: "≤",  0xA4: "⁄",  0xA5: "∞",
    0xA6: "ƒ",   0xA7: "♣",   0xA8: "♦",  0xA9: "♥",  0xAA: "♠",  0xAB: "↔",
    0xAC: "←",   0xAD: "↑",   0xAE: "→",  0xAF: "↓",
    0xB0: "°",   0xB1: "±",   0xB2: "″",  0xB3: "≥",  0xB4: "×",  0xB5: "∝",
    0xB6: "∂",   0xB7: "•",   0xB8: "÷",  0xB9: "≠",  0xBA: "≡",  0xBB: "≈",
    0xBC: "…",   0xBD: "⏐",   0xBE: "⎯",  0xBF: "↵",
    0xC0: "ℵ",   0xC1: "ℑ",   0xC2: "ℜ",  0xC3: "℘",  0xC4: "⊗",  0xC5: "⊕",
    0xC6: "∅",   0xC7: "∩",   0xC8: "∪",  0xC9: "⊃",  0xCA: "⊇",  0xCB: "⊄",
    0xCC: "⊂",   0xCD: "⊆",   0xCE: "∈",  0xCF: "∉",
    0xD0: "∠",   0xD1: "∇",   0xD2: "®",  0xD3: "©",  0xD4: "™",  0xD5: "∏",
    0xD6: "√",   0xD7: "⋅",   0xD8: "¬",  0xD9: "∧",  0xDA: "∨",  0xDB: "⇔",
    0xDC: "⇐",   0xDD: "⇑",   0xDE: "⇒",  0xDF: "⇓",
    0xE0: "◊",   0xE1: "⟨",   0xE2: "®",  0xE3: "©",  0xE4: "™",  0xE5: "∑",
    # The pieces a typesetter stacks to build a tall bracket around a fraction.
    0xE6: "⎛",   0xE7: "⎜",   0xE8: "⎝",  0xE9: "⎡",  0xEA: "⎢",  0xEB: "⎣",
    0xEC: "⎧",   0xED: "⎨",   0xEE: "⎩",  0xEF: "⎪",
    0xF1: "⟩",   0xF2: "∫",   0xF3: "⌠",  0xF4: "⎮",  0xF5: "⌡",
    0xF6: "⎞",   0xF7: "⎟",   0xF8: "⎠",  0xF9: "⎤",  0xFA: "⎥",  0xFB: "⎦",
    0xFC: "⎫",   0xFD: "⎬",   0xFE: "⎭",
}

# Word ships Cambria Math with Office, and unlike the text faces it covers the
# whole of this table — including the bracket-piece range at U+239B..U+23AD,
# which Times New Roman does not have. Symbol spans are drawn in it so that the
# character that was recovered is a character the reader can actually see.
SYMBOL_FONT = "Cambria Math"


def decode_symbol_pua(text: str) -> str:
    """Rewrite private-use Symbol codes as the characters they stand for.

    Codes with no entry in the table are left as they are rather than dropped;
    an unmapped glyph is a question for the font, not licence to lose content.
    """
    if not text:
        return text
    if not any(_PUA_BASE <= ord(character) <= _PUA_END for character in text):
        return text
    return "".join(
        _SYMBOL.get(ord(character) - _PUA_BASE, character)
        if _PUA_BASE <= ord(character) <= _PUA_END
        else character
        for character in text
    )
