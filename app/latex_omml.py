"""Convert a useful subset of LaTeX math into OMML (Office Math Markup Language).

OMML is what Word's native equation editor stores, so equations produced here stay
editable in Word rather than becoming images or plain text. Coverage is aimed at
what appears in real documents: fractions, scripts, radicals, big operators with
limits, delimiters, accents, matrices, cases, and the usual symbol vocabulary.

Anything unrecognised degrades gracefully to an upright text run, so no content is
ever silently dropped.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"

# --------------------------------------------------------------------------- #
# Symbol tables
# --------------------------------------------------------------------------- #

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
    "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω",
}

SYMBOLS = {
    # relations
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅",
    "propto": "∝", "ll": "≪", "gg": "≫", "subset": "⊂", "supset": "⊃",
    "subseteq": "⊆", "supseteq": "⊇", "in": "∈", "notin": "∉", "ni": "∋",
    "perp": "⊥", "parallel": "∥", "mid": "∣", "models": "⊨",
    # operators
    "times": "×", "div": "÷", "pm": "±", "mp": "∓", "cdot": "⋅", "ast": "∗",
    "star": "⋆", "circ": "∘", "bullet": "∙", "oplus": "⊕", "ominus": "⊖",
    "otimes": "⊗", "oslash": "⊘", "odot": "⊙", "cup": "∪", "cap": "∩",
    "setminus": "∖", "wedge": "∧", "vee": "∨", "neg": "¬", "lnot": "¬",
    "sqrtsign": "√",
    # arrows
    "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←",
    "leftrightarrow": "↔", "Rightarrow": "⇒", "Leftarrow": "⇐",
    "Leftrightarrow": "⇔", "mapsto": "↦", "longrightarrow": "⟶",
    "longleftarrow": "⟵", "uparrow": "↑", "downarrow": "↓",
    # misc
    "infty": "∞", "partial": "∂", "nabla": "∇", "forall": "∀", "exists": "∃",
    "nexists": "∄", "emptyset": "∅", "varnothing": "∅", "angle": "∠",
    "triangle": "△", "square": "□", "therefore": "∴", "because": "∵",
    "ldots": "…", "dots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱",
    "prime": "′", "degree": "°", "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ",
    "Im": "ℑ", "aleph": "ℵ", "wp": "℘", "surd": "√", "checkmark": "✓",
    "langle": "⟨", "rangle": "⟩", "lceil": "⌈", "rceil": "⌉",
    "lfloor": "⌊", "rfloor": "⌋", "vert": "|", "Vert": "‖",
    "backslash": "\\",
    "%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "{": "{", "}": "}",
}

# Big operators rendered as n-ary structures.
NARY = {
    "sum": ("∑", "undOvr"), "prod": ("∏", "undOvr"), "coprod": ("∐", "undOvr"),
    "int": ("∫", "subSup"), "iint": ("∬", "subSup"), "iiint": ("∭", "subSup"),
    "oint": ("∮", "subSup"),
    "bigcup": ("⋃", "undOvr"), "bigcap": ("⋂", "undOvr"),
    "bigoplus": ("⨁", "undOvr"), "bigotimes": ("⨂", "undOvr"),
    "bigvee": ("⋁", "undOvr"), "bigwedge": ("⋀", "undOvr"),
}

# Upright function names.
FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "coth", "log", "ln", "lg", "exp", "det", "dim",
    "ker", "deg", "gcd", "arg", "Pr", "hom", "mod", "bmod",
}

# Functions whose script sits underneath (lim_{x \to 0}).
UNDER_FUNCTIONS = {"lim", "limsup", "liminf", "max", "min", "sup", "inf", "argmax", "argmin"}

ACCENTS = {
    "hat": "̂", "widehat": "̂", "tilde": "̃", "widetilde": "̃",
    "dot": "̇", "ddot": "̈", "vec": "⃗", "acute": "́",
    "grave": "̀", "check": "̌", "breve": "̆",
}

SPACES = {"quad": " ", "qquad": "  ", ",": " ", ":": " ",
          ";": " ", "!": "", " ": " "}

MATRIX_DELIMS = {
    "matrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"),
    "Bmatrix": ("{", "}"), "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"),
    "cases": ("{", ""), "array": ("", ""), "aligned": ("", ""),
    "align": ("", ""), "align*": ("", ""), "gathered": ("", ""),
    "smallmatrix": ("", ""), "substack": ("", ""),
}

TEXT_COMMANDS = {"text", "textrm", "textnormal", "mathrm", "operatorname", "mbox", "textit"}
BOLD_COMMANDS = {"mathbf", "textbf", "boldsymbol", "bm"}

# Alphabet switches: the face is not reproduced, but the argument is still
# content and must survive. `\mathbb R` has no braces, so the argument has to be
# read as an atom when there is no group — reading only groups dropped the R.
FONT_COMMANDS = {"mathbb", "mathcal", "mathfrak", "mathsf", "mathtt", "mathit"}

# Pure modifiers: they take no argument at all, so they are consumed and ignored.
STYLE_MODIFIERS = {"displaystyle", "textstyle", "scriptstyle", "limits", "nolimits"}

TOKEN_RE = re.compile(
    r"""(?P<cmd>\\[A-Za-z]+\*?)      # \command  (\align* included)
      | (?P<esc>\\.)                 # \{  \}  \\  \,  \;
      | (?P<num>[0-9]+(?:\.[0-9]+)?) # numbers stay together
      | (?P<ws>[ \t\n]+)
      | (?P<char>.)
    """,
    re.X | re.S,
)


class Token(str):
    """A LaTeX token; `kind` distinguishes commands from literal characters."""

    kind: str

    def __new__(cls, value: str, kind: str) -> "Token":
        obj = super().__new__(cls, value)
        obj.kind = kind
        return obj


def tokenize(latex: str) -> list[Token]:
    tokens: list[Token] = []
    for match in TOKEN_RE.finditer(latex):
        kind = match.lastgroup
        value = match.group()
        if kind == "ws":
            # Kept, not dropped: `\text{...}` is not mathematics, and the spaces
            # inside it are content — "radius of path", "20 T". The maths parser
            # steps over these at the cursor, exactly as LaTeX ignores them.
            tokens.append(Token(value, "ws"))
            continue
        if kind == "cmd":
            tokens.append(Token(value[1:], "cmd"))
        elif kind == "esc":
            body = value[1:]
            if body == "\\":
                tokens.append(Token("\\\\", "rowsep"))
            else:
                tokens.append(Token(body, "cmd"))
        else:
            tokens.append(Token(value, "char"))
    return tokens


# --------------------------------------------------------------------------- #
# OMML element builders
# --------------------------------------------------------------------------- #


def _text_run(text: str, upright: bool = False, bold: bool = False) -> str:
    if not upright:
        # A hyphen-minus in mathematics is a minus sign, and saying so is not
        # only correct typography. LibreOffice reads a run holding the ASCII
        # character as a binary operator and demands two operands for it, so an
        # `e^-` — an electron, an ion, half the notation in a chemistry book —
        # arrives with the inverted question marks it prints for the operands
        # that never came. With U+2212 it is a character, and reads as written.
        text = text.replace("-", "−")
    props = ""
    if upright or bold:
        inner = "<m:nor/>" if upright else ""
        if bold:
            inner += '<m:sty m:val="b"/>'
        props = f"<m:rPr>{inner}</m:rPr>"
    return f'<m:r>{props}<m:t xml:space="preserve">{escape(text)}</m:t></m:r>'


# The characters LibreOffice cannot carry through an `<m:nor/>` run. It imports
# such a run as StarMath literal text, escapes the characters StarMath itself
# gives meaning to, and then prints the escape instead of consuming it — so the
# `\text{(a) arg}` that labels a multiple-choice option reaches the page as
# `\(a\) arg`, and a double quote is swallowed outright. Word has no such
# trouble, but the .docx has to read the same wherever it is opened.
_STARMATH_LITERAL_RE = re.compile(r'[(){}"]')


def _run(text: str, upright: bool = False, bold: bool = False) -> str:
    """One OMML run, or the few it takes to say the same thing safely.

    Upright text is split so that the characters above are carried by ordinary
    mathematical runs, which every reader renders as themselves, and only the
    words in between are marked as literal text. Brackets and quotes are upright
    in mathematics anyway, so nothing about the expression looks different for
    it — in Word the split is invisible.
    """
    if not upright or not _STARMATH_LITERAL_RE.search(text):
        return _text_run(text, upright=upright, bold=bold)

    runs: list[str] = []
    cursor = 0
    for match in _STARMATH_LITERAL_RE.finditer(text):
        if match.start() > cursor:
            runs.append(_text_run(text[cursor : match.start()], upright=True, bold=bold))
        runs.append(_text_run(match.group(), upright=False, bold=bold))
        cursor = match.end()
    if cursor < len(text):
        runs.append(_text_run(text[cursor:], upright=True, bold=bold))
    return "".join(runs)


def _literal(char: str) -> str:
    """A character that has to be drawn rather than interpreted.

    A delimiter standing on its own is not always a delimiter. A transcription
    that ends `\\leq |z_1| + |z_2|]` has a bracket in it that opens nothing, and
    a bar that means "such that" is not the side of a modulus. LibreOffice reads
    both structurally regardless: the lone bracket becomes one whose operand
    never came, drawn as an inverted question mark, and the lone bar becomes the
    logical `or`, drawn as `∨`. Marked as literal text they are drawn as
    themselves, which is what was written.

    Not for `(`, `)`, `{` or `}` — those survive an ordinary run intact, and it
    is being marked literal that breaks them. See `_run`.
    """
    return _text_run(char, upright=True)


def _frac(num: str, den: str, bar: bool = True) -> str:
    kind = "" if bar else '<m:fPr><m:type m:val="noBar"/></m:fPr>'
    return f"<m:f>{kind}<m:num>{num}</m:num><m:den>{den}</m:den></m:f>"


def _attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _delim(begin: str, end: str, body: str) -> str:
    # CT_DPr is a strict sequence — begChr, sepChr, endChr, grow, shp, ctrlPr —
    # and Word validates it. Written out of that order the whole property set is
    # discarded and the delimiter falls back to its default, which is a round
    # bracket: every [ ] { } ⟨ ⟩ in the document silently became ( ). sepChr is
    # omitted rather than emitted empty; with a single m:e there is nothing for a
    # separator to sit between, and leaving it out is what Word itself writes.
    props = (
        f'<m:dPr><m:begChr m:val="{_attr(begin)}"/>'
        f'<m:endChr m:val="{_attr(end)}"/></m:dPr>'
    )
    return f"<m:d>{props}<m:e>{body}</m:e></m:d>"


def _rad(degree: str | None, body: str) -> str:
    if degree:
        return (
            f'<m:rad><m:radPr><m:degHide m:val="0"/></m:radPr>'
            f"<m:deg>{degree}</m:deg><m:e>{body}</m:e></m:rad>"
        )
    return (
        f'<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr>'
        f"<m:deg/><m:e>{body}</m:e></m:rad>"
    )


def _scripts(base: str, sub: str | None, sup: str | None) -> str:
    if sub is not None and sup is not None:
        return f"<m:sSubSup><m:e>{base}</m:e><m:sub>{sub}</m:sub><m:sup>{sup}</m:sup></m:sSubSup>"
    if sub is not None:
        return f"<m:sSub><m:e>{base}</m:e><m:sub>{sub}</m:sub></m:sSub>"
    if sup is not None:
        return f"<m:sSup><m:e>{base}</m:e><m:sup>{sup}</m:sup></m:sSup>"
    return base


def _nary(char: str, lim_loc: str, sub: str | None, sup: str | None, body: str) -> str:
    props = (
        f'<m:naryPr><m:chr m:val="{char}"/><m:limLoc m:val="{lim_loc}"/>'
        f'<m:subHide m:val="{0 if sub else 1}"/><m:supHide m:val="{0 if sup else 1}"/>'
        f"</m:naryPr>"
    )
    return (
        f"<m:nary>{props}<m:sub>{sub or ''}</m:sub>"
        f"<m:sup>{sup or ''}</m:sup><m:e>{body}</m:e></m:nary>"
    )


# A relation opening an expression, as the first thing in the emitted body.
_LEADS_WITH_RELATION_RE = re.compile(
    r'^<m:r><m:t xml:space="preserve">(?:[=≠≈≡≤≥∝≃≅∼]|&lt;|&gt;)'
)


def _guard_leading_relation(body: str) -> str:
    """Let an expression begin with `=` without losing it.

    A worked solution is set as a column of continuation lines — `= 2\\pi r`,
    `= 3.14` — each of which opens with a relation whose left operand is the
    line above. LibreOffice reads that as an operator with a missing operand,
    draws the inverted question mark it uses for one, and drops the relation
    itself: `= -3.2 \\times 10^{-14}` is displayed as `¿-3.2 × 10⁻¹⁴`. Marking
    the relation as literal text keeps it — it is upright either way, so nothing
    about the equation looks different for it.
    """
    if not _LEADS_WITH_RELATION_RE.match(body):
        return body
    return body.replace("<m:r>", "<m:r><m:rPr><m:nor/></m:rPr>", 1)


def _matrix(rows: list[list[str]], align: str = "center") -> str:
    columns = max((len(row) for row in rows), default=1)
    props = (
        f"<m:mPr><m:mcs><m:mc><m:mcPr><m:count m:val=\"{columns}\"/>"
        f'<m:mcJc m:val="{align}"/></m:mcPr></m:mc></m:mcs></m:mPr>'
    )
    # Every cell is guarded, not just the first thing in the equation. An
    # `aligned` environment puts the alignment marker exactly where the relation
    # is — `x &= 1` is a cell holding `x` beside a cell holding `= 1` — so in a
    # column of worked steps it is the cells, not the equation, that open with a
    # relation whose left operand is elsewhere.
    #
    # Short rows are padded out to the full count. `aligned` lets a line carry
    # fewer alignment markers than the lines around it — a step written without
    # a relation in it is one cell where its neighbours are two — but `m:m` does
    # not: every `m:mr` owes `m:count` cells, and LibreOffice answers a row that
    # hands over fewer by dropping every cell it did hand over and drawing an
    # inverted question mark in each place.
    #
    # The padding is a space rather than the empty `<m:e/>` the schema allows,
    # because an empty one is drawn as a missing operand and comes out as the
    # same question mark. A cell can also be empty because the line began with
    # its alignment marker — `\\&= 2\\pi r`, the continuation of the line above —
    # and it is filled the same way, for the same reason.
    padded = [
        [cell if cell else _run(" ", upright=True) for cell in row]
        + [_run(" ", upright=True)] * (columns - len(row))
        for row in rows
    ]
    body = "".join(
        "<m:mr>"
        + "".join(f"<m:e>{_guard_leading_relation(cell)}</m:e>" for cell in row)
        + "</m:mr>"
        for row in padded
    )
    return f"<m:m>{props}{body}</m:m>"


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

OPEN_DELIMS = {"(": ")", "[": "]"}


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    # -- token helpers ----------------------------------------------------- #

    def _significant(self, start: int) -> int:
        """The next index the maths parser can see, stepping over whitespace."""
        index = start
        while index < len(self.tokens) and self.tokens[index].kind == "ws":
            index += 1
        return index

    def peek(self) -> Token | None:
        index = self._significant(self.pos)
        return self.tokens[index] if index < len(self.tokens) else None

    def next(self) -> Token | None:
        index = self._significant(self.pos)
        if index >= len(self.tokens):
            self.pos = index
            return None
        self.pos = index + 1
        return self.tokens[index]

    def next_raw(self) -> Token | None:
        """The next token including whitespace, for text that is not mathematics."""
        token = self.tokens[self.pos] if self.pos < len(self.tokens) else None
        if token is not None:
            self.pos += 1
        return token

    def at(self, value: str, kind: str = "char") -> bool:
        token = self.peek()
        return token is not None and token.kind == kind and str(token) == value

    def has_closing(self, value: str) -> bool:
        """Is there a matching `value` still to come at this brace depth?"""
        depth = 0
        for token in self.tokens[self.pos :]:
            if token.kind != "char":
                continue
            char = str(token)
            if char == "{":
                depth += 1
            elif char == "}":
                if depth == 0:
                    return False
                depth -= 1
            elif char == value and depth == 0:
                return True
        return False

    # -- entry points ------------------------------------------------------ #

    def parse_sequence(self, stop: set[tuple[str, str]] | None = None) -> str:
        stop = stop or set()
        parts: list[str] = []
        while True:
            token = self.peek()
            if token is None or (str(token), token.kind) in stop:
                break
            parts.append(self.parse_element())
        return "".join(parts)

    def parse_element(self) -> str:
        base = self.parse_atom()
        return self.attach_scripts(base)

    def attach_scripts(self, base: str, under: bool = False) -> str:
        sub = sup = None
        while True:
            token = self.peek()
            if token is None or token.kind != "char" or str(token) not in ("_", "^"):
                break
            self.next()
            argument = self.parse_script_argument()
            if str(token) == "_":
                sub = argument
            else:
                sup = argument
        if under and sub is not None and sup is None:
            return f"<m:limLow><m:e>{base}</m:e><m:lim>{sub}</m:lim></m:limLow>"
        if under and sub is not None and sup is not None:
            low = f"<m:limLow><m:e>{base}</m:e><m:lim>{sub}</m:lim></m:limLow>"
            return f"<m:limUpp><m:e>{low}</m:e><m:lim>{sup}</m:lim></m:limUpp>"
        return _scripts(base, sub, sup)

    def parse_script_argument(self) -> str:
        if self.at("{"):
            self.next()
            body = self.parse_sequence({("}", "char")})
            if self.at("}"):
                self.next()
            return body
        return self.parse_atom()

    def parse_required_group(self) -> str:
        while self.peek() is not None and self.peek().kind == "char" and str(self.peek()) == " ":
            self.next()
        if self.at("{"):
            self.next()
            body = self.parse_sequence({("}", "char")})
            if self.at("}"):
                self.next()
            return body
        return self.parse_atom()

    def parse_matrix_group(self, align: str = "center") -> str:
        """Consume a braced group whose ``\\`` separators form stacked rows."""
        if not self.at("{"):
            return self.parse_required_group()
        self.next()
        rows: list[list[str]] = [[]]
        while True:
            token = self.peek()
            if token is None or (token.kind == "char" and str(token) == "}"):
                break
            if token.kind == "rowsep":
                self.next()
                rows.append([])
                continue
            if token.kind == "char" and str(token) == "&":
                self.next()
                rows[-1].append("")
                continue
            cell = self.parse_element()
            if rows[-1]:
                rows[-1][-1] += cell
            else:
                rows[-1].append(cell)
        if self.at("}"):
            self.next()
        rows = [row for row in rows if any(cell.strip() for cell in row)] or [[""]]
        return _matrix(rows, align=align)

    def read_raw_group(self) -> str:
        """Consume `{...}` and return its literal text (for \\text{...})."""
        if not self.at("{"):
            token = self.next()
            return str(token) if token else ""
        self.next()
        depth = 1
        chunks: list[str] = []
        while True:
            token = self.next_raw()
            if token is None:
                break
            if token.kind == "char" and str(token) == "{":
                depth += 1
            elif token.kind == "char" and str(token) == "}":
                depth -= 1
                if depth == 0:
                    break
            if token.kind == "cmd":
                chunks.append(SYMBOLS.get(str(token), GREEK.get(str(token), " ")))
            elif token.kind in ("rowsep", "ws"):
                # A run of spaces, a tab or a newline is one space, as in LaTeX.
                chunks.append(" ")
            else:
                chunks.append(str(token))
        return "".join(chunks)

    # -- atoms ------------------------------------------------------------- #

    def parse_atom(self) -> str:
        token = self.next()
        if token is None:
            return ""
        if token.kind == "cmd":
            return self.parse_command(str(token))
        if token.kind == "rowsep":
            return _run(" ")

        char = str(token)
        if char == "{":
            body = self.parse_sequence({("}", "char")})
            if self.at("}"):
                self.next()
            return body
        if char == "}":
            return ""
        if char in OPEN_DELIMS:
            closing = OPEN_DELIMS[char]
            body = self.parse_sequence({(closing, "char"), ("right", "cmd")})
            if self.at(closing):
                self.next()
                return _delim(char, closing, body)
            # Nothing closed it. An equation crop can end mid-expression, and
            # closing the pair anyway prints a bracket the source never had.
            return _delim(char, "", body)
        if char in (")", "]"):
            return _literal(char) if char == "]" else _run(char)
        if char == "|" and self.has_closing("|"):
            # A matched pair of bars is a modulus, and saying so structurally is
            # what makes it one object. Left as two loose characters the bars are
            # read as operators and the expression between them is taken for an
            # operand that never arrives. An unmatched bar is left alone — there
            # it means "such that", and pairing it would invent a delimiter.
            body = self.parse_sequence({("|", "char")})
            if self.at("|"):
                self.next()
            return _delim("|", "|", body)
        if char == "|":
            return _literal(char)
        if char in ("^", "_"):
            # Stray script marker with no base — attach it to an empty base.
            argument = self.parse_script_argument()
            return _scripts("", argument if char == "_" else None,
                            argument if char == "^" else None)
        return _run(char)

    def parse_command(self, name: str) -> str:  # noqa: C901 - a dispatch table by nature
        if name == "frac" or name == "dfrac" or name == "tfrac":
            return _frac(self.parse_required_group(), self.parse_required_group())
        if name == "binom" or name == "dbinom":
            top = self.parse_required_group()
            bottom = self.parse_required_group()
            return _delim("(", ")", _frac(top, bottom, bar=False))
        if name == "sqrt":
            degree = None
            if self.at("["):
                self.next()
                degree = self.parse_sequence({("]", "char")})
                if self.at("]"):
                    self.next()
            return _rad(degree, self.parse_required_group())
        if name in NARY:
            char, lim_loc = NARY[name]
            sub = sup = None
            while True:
                token = self.peek()
                if token is not None and token.kind == "cmd" and str(token) in ("limits", "nolimits"):
                    # `\sum\limits_{i=1}^{n}`: the modifier sits between the
                    # operator and its limits. Stepping over it here is what keeps
                    # them on the operator — parsed as an ordinary atom it ends the
                    # script loop, and the limits land on an empty base instead.
                    lim_loc = "undOvr" if str(token) == "limits" else "subSup"
                    self.next()
                    continue
                if token is None or token.kind != "char" or str(token) not in ("_", "^"):
                    break
                self.next()
                argument = self.parse_script_argument()
                if str(token) == "_":
                    sub = argument
                else:
                    sup = argument
            body = self.parse_nary_body()
            return _nary(char, lim_loc, sub, sup, body)
        if name in UNDER_FUNCTIONS:
            return self.attach_scripts(_run(name, upright=True), under=True)
        if name in FUNCTIONS:
            return _run(name, upright=True)
        if name in ACCENTS:
            body = self.parse_required_group()
            return (
                f'<m:acc><m:accPr><m:chr m:val="{ACCENTS[name]}"/></m:accPr>'
                f"<m:e>{body}</m:e></m:acc>"
            )
        if name in ("overline", "bar"):
            body = self.parse_required_group()
            return f'<m:bar><m:barPr><m:pos m:val="top"/></m:barPr><m:e>{body}</m:e></m:bar>'
        if name == "underline":
            body = self.parse_required_group()
            return f'<m:bar><m:barPr><m:pos m:val="bot"/></m:barPr><m:e>{body}</m:e></m:bar>'
        if name in ("overbrace", "underbrace"):
            position = "top" if name == "overbrace" else "bot"
            body = self.parse_required_group()
            return (
                f'<m:groupChr><m:groupChrPr><m:chr m:val="{"⏞" if position == "top" else "⏟"}"/>'
                f'<m:pos m:val="{position}"/></m:groupChrPr><m:e>{body}</m:e></m:groupChr>'
            )
        if name in ("overset", "stackrel"):
            upper = self.parse_required_group()
            body = self.parse_required_group()
            return f"<m:limUpp><m:e>{body}</m:e><m:lim>{upper}</m:lim></m:limUpp>"
        if name == "underset":
            lower = self.parse_required_group()
            body = self.parse_required_group()
            return f"<m:limLow><m:e>{body}</m:e><m:lim>{lower}</m:lim></m:limLow>"
        if name == "substack":
            return self.parse_matrix_group()
        if name in TEXT_COMMANDS:
            return _run(self.read_raw_group(), upright=True)
        if name in BOLD_COMMANDS:
            return _run(self.read_raw_group(), bold=True)
        if name in FONT_COMMANDS:
            return self.parse_required_group()
        if name in STYLE_MODIFIERS:
            return ""
        if name == "left":
            return self.parse_left_right()
        if name == "right":
            # Unmatched \right — consume its delimiter and move on.
            self.next()
            return ""
        if name == "begin":
            return self.parse_environment()
        if name == "end":
            self.read_raw_group()
            return ""
        if name in SPACES:
            return _run(SPACES[name]) if SPACES[name] else ""
        if name in GREEK:
            return _run(GREEK[name])
        if name in SYMBOLS:
            return _run(SYMBOLS[name])
        if name == "not":
            return _run("¬")
        # Unknown command: keep it visible rather than dropping content.
        return _run(name, upright=True)

    def parse_nary_body(self) -> str:
        """Read the operand of a big operator, stopping at the next relation."""
        parts: list[str] = []
        while True:
            token = self.peek()
            if token is None:
                break
            value, kind = str(token), token.kind
            if kind == "rowsep":
                break
            if kind == "char" and value in ("}", "&", ")", "]"):
                break
            if kind == "cmd" and value in ("right", "end", "\\"):
                break
            if kind == "char" and value in ("=", "+", "<", ">") and parts:
                break
            if kind == "cmd" and value in NARY and parts:
                break
            parts.append(self.parse_element())
        return "".join(parts)

    def parse_left_right(self) -> str:
        begin = self.read_delimiter()
        body = self.parse_sequence({("right", "cmd")})
        end = ""
        if self.at("right", "cmd"):
            self.next()
            end = self.read_delimiter()
        return _delim(begin, end, body)

    def read_delimiter(self) -> str:
        token = self.next()
        if token is None:
            return ""
        value = str(token)
        if token.kind == "cmd":
            if value == ".":
                return ""
            return SYMBOLS.get(value, value)
        return "" if value == "." else value

    def parse_environment(self) -> str:
        name = self.read_raw_group().strip()
        if name == "array":
            # Skip the column specification, e.g. {ccc}
            if self.at("{"):
                self.read_raw_group()
        rows: list[list[str]] = [[]]
        stop = {("end", "cmd")}
        while True:
            token = self.peek()
            if token is None or (str(token), token.kind) in stop:
                break
            if token.kind == "rowsep":
                self.next()
                rows.append([])
                continue
            if token.kind == "char" and str(token) == "&":
                self.next()
                rows[-1].append("")
                continue
            cell = self.parse_element()
            if rows[-1]:
                rows[-1][-1] += cell
            else:
                rows[-1].append(cell)
        if self.at("end", "cmd"):
            self.next()
            self.read_raw_group()

        rows = [row for row in rows if any(cell.strip() for cell in row)] or [[""]]
        align = "left" if name in ("cases", "aligned", "align", "align*", "array") else "center"
        matrix = _matrix(rows, align=align)
        begin, end = MATRIX_DELIMS.get(name, ("", ""))
        if begin or end:
            return _delim(begin, end, matrix)
        return matrix


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


# A transcription is judged on what is left once its prose is taken out: the
# words inside \text{...}, and the command names themselves, are not evidence of
# mathematics — `\text{The velocity of the particle}` is a sentence.
_TEXT_GROUP_RE = re.compile(
    r"\\(?:text|textrm|textnormal|textit|textbf|mathrm|mbox|operatorname)\s*\{[^{}]*\}"
)
_COMMAND_RE = re.compile(r"\\[A-Za-z]+\*?")
_OPERATOR_RE = re.compile(r"[=+<>^_/±×÷≤≥≠∑∫√∞−·]|\\\\")
_PROSE_WORD_RE = re.compile(r"[A-Za-z]{4,}")

# Three ordinary words is a sentence, not an equation with a label in it.
_MAX_PROSE_WORDS = 3


def is_math_latex(latex: str) -> bool:
    """Is this transcription really an equation?

    The crop detector works from the fonts a typesetter used, and those are only
    a hint: a heading, a caption or a whole line of prose can be handed to the
    model as if it were an expression, and the model dutifully returns it as
    LaTeX. Writing that into an `<m:oMath>` turns a paragraph of the document
    into an equation object — it still reads correctly, but it is no longer text,
    and Word will not let it be edited as text. Anything that fails this test is
    given back to the ordinary positioned-text path, which still has it.
    """
    body = (latex or "").strip()
    if not body:
        return False

    outside = _TEXT_GROUP_RE.sub(" ", body)
    if not outside.strip():
        return False  # nothing but \text{...}: prose the model wrapped for us

    has_structure = bool(_COMMAND_RE.search(outside) or _OPERATOR_RE.search(outside))
    # Command names are not words; strip them before counting prose.
    prose = _PROSE_WORD_RE.findall(_COMMAND_RE.sub(" ", outside))
    return has_structure and len(prose) < _MAX_PROSE_WORDS


# An expression that stops at an operator has an operand missing: whatever the
# crop cut off never reached the transcription.
_DANGLING_RE = re.compile(
    r"(?:[=+\-*/<>^_±×÷≤≥≠−·]"
    r"|\\(?:frac|sqrt|left|right|pm|mp|times|cdot|div|leq|geq|approx|to|rightarrow"
    r"|neq|equiv|sim|propto|over|hat|vec|bar|text|mathrm))\s*$"
)


def looks_incomplete(latex: str) -> bool:
    """Does this transcription end in mid-expression?

    Word draws a missing operand as an empty slot and LibreOffice draws it as an
    inverted question mark, so `a = R sin θ =` — an equation whose right-hand
    side was cut off the crop — arrives in the document as `a = R sin θ = ¿`.
    The picture of the crop is never wrong in that way, so it is used instead.
    """
    body = (latex or "").strip()
    if not body:
        return True
    if body.count("{") != body.count("}"):
        return True
    if body.count(r"\left") != body.count(r"\right"):
        return True
    return bool(_DANGLING_RE.search(body))


def latex_to_omml_body(latex: str) -> str:
    """Convert LaTeX to the inner OMML of an `<m:oMath>` element."""
    parser = Parser(tokenize(latex))
    body = parser.parse_sequence()
    return _guard_leading_relation(body) if body else _run(latex.strip(), upright=True)


def inline_math_xml(latex: str) -> str:
    """A standalone `<m:oMath>` element, for math inside a paragraph."""
    return f'<m:oMath xmlns:m="{MATH_NS}">{latex_to_omml_body(latex)}</m:oMath>'


def display_math_xml(latex: str, align: str = "center") -> str:
    """An `<m:oMathPara>` element, for a display equation on its own line."""
    body = latex_to_omml_body(latex)
    return (
        f'<m:oMathPara xmlns:m="{MATH_NS}">'
        f'<m:oMathParaPr><m:jc m:val="{align}"/></m:oMathParaPr>'
        f"<m:oMath>{body}</m:oMath></m:oMathPara>"
    )
