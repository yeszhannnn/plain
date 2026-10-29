"""Rules shared by the interpreter and runtime/*.c, so both print exactly the same thing."""

import re

NO_SPACE_BEFORE = ".,!?;:)"

# Upper -> lower case pairs: Latin, Latin-1, Cyrillic and the extra Kazakh letters.
# runtime/text.c has the same table.
_PAIRS = [(c, c + 32) for c in range(0x41, 0x5B)]
_PAIRS += [(c, c + 32) for c in range(0xC0, 0xDF) if c != 0xD7]
_PAIRS += [(c, c + 32) for c in range(0x410, 0x430)]
_PAIRS += [(c, c + 80) for c in range(0x400, 0x410)]
_PAIRS += [(0x492, 0x493), (0x49A, 0x49B), (0x4A2, 0x4A3), (0x4AE, 0x4AF), (0x4B0, 0x4B1), (0x4BA, 0x4BB),
           (0x4D8, 0x4D9), (0x4E8, 0x4E9)]
LOWER = {chr(u): chr(l) for u, l in _PAIRS}
UPPER = {chr(l): chr(u) for u, l in _PAIRS}

INT_RE = re.compile(r"[ \t\r\n]*([+-]?[0-9]{1,18})[ \t\r\n]*")
DEC_RE = re.compile(r"[ \t\r\n]*([+-]?[0-9]+(\.[0-9]+)?)[ \t\r\n]*")
SPACE = " \t\r\n"


def lower(s):
    return "".join(LOWER.get(c, c) for c in s)


def upper(s):
    return "".join(UPPER.get(c, c) for c in s)


def text_same(a, b):
    return lower(a.strip(SPACE)) == lower(b.strip(SPACE))


def join_parts(pieces):
    out = ""
    for s in pieces:
        if not s:
            continue
        if out and out[-1] not in SPACE and s[0] not in SPACE and s[0] not in NO_SPACE_BEFORE:
            out += " "
        out += s
    return out


def fmt_float(x):
    s = f"{x:.12g}"
    if not any(c in s for c in ".eni"):
        s += ".0"
    return s


def wrap(n):
    """64-bit wrap-around, like the machine code."""
    return ((n + 2 ** 63) % 2 ** 64) - 2 ** 63


def english_list(items):
    if not items:
        return "nothing"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def fmt(v, t):
    """Text of a value for printing; t is the value's type (types.Con)."""
    name = t.name
    if name == "Bool":
        return "true" if v else "false"
    if name == "Float":
        return fmt_float(float(v))
    if name == "Int":
        return str(v)
    if name == "Text":
        return v
    if name == "List":
        return english_list([fmt(x, t.args[0]) for x in v])
    if not v:
        return "nothing"
    return ", ".join(f"{fmt(k, t.args[0])}: {fmt(x, t.args[1])}" for k, x in v.items())


def words_of(s):
    return [w for w in re.split(r"[ \t\r\n]+", s) if w]


def lines_of(s):
    lines = s.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [l[:-1] if l.endswith("\r") else l for l in lines]
