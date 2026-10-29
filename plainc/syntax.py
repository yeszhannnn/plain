"""From source text to canonical tokens: sentences, tokens, normalization, synonyms."""

import json
import os
from dataclasses import dataclass

from .errors import EngError

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Lexicon:
    def __init__(self, path=os.path.join(HERE, "lexicon.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.edition = data["edition"]
        self.articles = set(data["articles"])
        self.filler = set(data["filler"])
        self.label_words = set(data["label_words"])
        self.contractions = data["contractions"]
        self.numbers = data["numbers"]
        self.repeat_words = data["repeat_words"]
        self.symbols = data["symbols"]
        self.phrases = {}  # tuple of words -> canonical keyword
        for kw, forms in data["words"].items():
            for form in forms:
                self.phrases[tuple(form.split())] = kw
        self.max_phrase = max(len(p) for p in self.phrases)
        self.phrase_starts = {p[:2] for p in self.phrases if len(p) > 1}
        self.print_forms = {p[0] for p, kw in self.phrases.items() if kw in ("PRINT", "REPLY") and len(p) == 1}
        self.single_forms = sorted(p[0] for p in self.phrases if len(p) == 1)
        self.protected = set()   # field names: never dropped as label words ("the value of a node")


@dataclass
class Tok:
    kind: str           # WORD NUM DEC STR COMMA LP RP or a canonical keyword (PRINT, IS, ...)
    value: object       # lowercase word / number / string / keyword source text
    text: str = ""      # original spelling, used for unquoted text and messages
    sym: bool = False   # operator written as a symbol (+ * >) rather than a word

    def __repr__(self):
        return f"{self.kind}({self.value!r})" if self.kind in ("WORD", "NUM", "DEC", "STR") else self.kind


@dataclass
class Sentence:
    text: str
    line: int
    file: str
    header_colon: bool = False  # sentence ended with ':' and the body follows on the same line


def normalize_quotes(src):
    return (src.replace("“", '"').replace("”", '"')
               .replace("‘", "'").replace("’", "'"))


def is_apostrophe(s, i):
    return 0 < i < len(s) - 1 and s[i - 1].isalnum() and s[i + 1].isalnum()


def is_comment(line):
    s = line.strip().lower()
    return s.startswith("note:") or s.startswith("#")


def split_sentences(src, file=""):
    """Split source into sentences on '.', ':' and newlines, respecting quotes.

    Empty lines and comment lines ("Note: ..." or "# ...") are skipped.
    """
    out = []
    for lineno, raw in enumerate(normalize_quotes(src).split("\n"), 1):
        if not raw.strip() or is_comment(raw):
            continue
        buf, quote, i = "", None, 0
        while i < len(raw):
            ch = raw[i]
            if quote:
                buf += ch
                if ch == quote and not (quote == "'" and is_apostrophe(raw, i)):
                    quote = None
            elif ch == '"' or (ch == "'" and not (i > 0 and raw[i - 1].isalnum())):
                quote = ch
                buf += ch
            elif ch == "." and not (0 < i < len(raw) - 1 and raw[i - 1].isdigit() and raw[i + 1].isdigit()):
                if buf.strip():
                    out.append(Sentence(buf.strip(), lineno, file))
                buf = ""
            elif ch == ":":
                rest = raw[i + 1:].strip().strip(".").strip()
                if buf.strip():
                    out.append(Sentence(buf.strip(), lineno, file, header_colon=bool(rest)))
                buf = ""
            else:
                buf += ch
            i += 1
        if quote:
            raise EngError("A quote is opened but never closed.", lineno, file)
        if buf.strip():
            out.append(Sentence(buf.strip(), lineno, file))
    return out


def tokenize(s, line, file=""):
    toks, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch.isspace():
            i += 1
        elif ch == '"' or (ch == "'" and not (i > 0 and s[i - 1].isalnum())):
            j = i + 1
            while j < len(s) and not (s[j] == ch and not (ch == "'" and is_apostrophe(s, j))):
                j += 1
            toks.append(Tok("STR", s[i + 1:j], s[i:j + 1]))
            i = j + 1
        elif ch.isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            if j + 1 < len(s) and s[j] == "." and s[j + 1].isdigit():
                j += 1
                while j < len(s) and s[j].isdigit():
                    j += 1
                toks.append(Tok("DEC", float(s[i:j]), s[i:j]))
            else:
                toks.append(Tok("NUM", int(s[i:j]), s[i:j]))
            i = j
        elif ch.isalpha() or ch == "_":
            j = i
            while j < len(s) and (s[j].isalnum() or s[j] == "_" or
                                  (s[j] in "'-" and j + 1 < len(s) and s[j + 1].isalpha())):
                j += 1
            toks.append(Tok("WORD", s[i:j].lower(), s[i:j]))
            i = j
        elif s[i:i + 2] in (">=", "<=", "!="):
            toks.append(Tok("SYM", s[i:i + 2], s[i:i + 2]))
            i += 2
        elif ch in "+-*×/<>=%^":
            toks.append(Tok("SYM", ch, ch))
            i += 1
        elif ch == ",":
            toks.append(Tok("COMMA", ",", ","))
            i += 1
        elif ch in "()":
            toks.append(Tok("LP" if ch == "(" else "RP", ch, ch))
            i += 1
        else:
            raise EngError(f'I don\'t understand the symbol "{ch}". Put it inside quotes if it is part of a text.',
                           line, file)
    return toks


def normalize(toks, lx):
    """Contractions, number words, possessives, articles and filler. Pure and deterministic."""
    out = []
    for t in toks:
        if t.kind == "WORD" and t.value in lx.contractions:
            out += [Tok("WORD", w, w) for w in lx.contractions[t.value].split()]
        else:
            out.append(t)
    toks = out

    # number words: "twenty-one" -> 21, "twice" -> 2 times
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "WORD" and t.value in lx.repeat_words:
            out += [Tok("NUM", lx.repeat_words[t.value], t.text), Tok("WORD", "times", "times")]
            i += 1
            continue
        parts = []
        j = i
        while j < len(toks) and toks[j].kind == "WORD":
            pieces = toks[j].value.split("-")
            if pieces == ["one"] and ((j + 1 < len(toks) and toks[j + 1].value == "of") or
                                      (j > 0 and toks[j - 1].value in ("next", "this", "that", "each", "any"))):
                break  # "one of ..." picks a random value; "the next one" is not a number
            if all(p in lx.numbers for p in pieces):
                parts += pieces
                j += 1
            elif toks[j].value == "and" and parts and j + 1 < len(toks) and toks[j + 1].value in lx.numbers \
                    and parts[-1] in ("hundred", "thousand"):
                j += 1  # "one hundred and five"
            else:
                break
        if parts:
            total, cur = 0, 0
            for p in parts:
                n = lx.numbers[p]
                if n == 100:
                    cur = (cur or 1) * 100
                elif n == 1000:
                    total += (cur or 1) * 1000
                    cur = 0
                else:
                    cur += n
            out.append(Tok("NUM", total + cur, " ".join(x.text for x in toks[i:j])))
            i = j
        else:
            out.append(t)
            i += 1
    toks = out

    # possessives: "the user's score" -> one name, user.score
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "WORD" and t.value.endswith("'s") and i + 1 < len(toks) and toks[i + 1].kind == "WORD":
            base, fld = t.value[:-2], toks[i + 1]
            out.append(Tok("WORD", f"{base}.{fld.value}", f"{t.text} {fld.text}"))
            i += 2
        else:
            out.append(t)
            i += 1
    toks = out

    toks = [t for t in toks if not (t.kind == "WORD" and (t.value in lx.articles or t.value in lx.filler))]

    # label words: "print the word 'Hello'", "say the number 5"
    out = []
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        prev = toks[i - 1] if i > 0 else None
        if t.kind == "WORD" and t.value in lx.label_words and t.value not in lx.protected and nxt is not None and (
                nxt.kind in ("STR", "NUM", "DEC") or
                (prev is not None and prev.kind == "WORD" and prev.value in lx.print_forms and nxt.kind == "WORD"
                 and (t.value, nxt.value) not in lx.phrase_starts)):
            continue
        out.append(t)
    return out


USER = "their."   # prefix of per-user variables: "their score" -> their.score


def apply_lexicon(toks, lx):
    """Map surface words and symbols to canonical keywords, longest phrase first."""
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "SYM":
            out.append(Tok(lx.symbols[t.value], t.value, t.text, sym=True))
            i += 1
            continue
        if t.kind == "WORD":
            matched = False
            for n in range(min(lx.max_phrase, len(toks) - i), 0, -1):
                window = toks[i:i + n]
                if all(w.kind == "WORD" for w in window):
                    key = tuple(w.value for w in window)
                    if key in lx.phrases and not (n > 1 and key[0] in lx.protected and key[1] == "of"):
                        out.append(Tok(lx.phrases[key], " ".join(key), " ".join(w.text for w in window)))
                        i += n
                        matched = True
                        break
            if matched:
                continue
        out.append(t)
        i += 1

    # "their score": a variable that each Telegram user has separately
    merged, i = [], 0
    while i < len(out):
        t = out[i]
        if t.kind == "WORD" and t.value == "their" and i + 1 < len(out) and out[i + 1].kind == "WORD":
            merged.append(Tok("WORD", USER + out[i + 1].value, f"{t.text} {out[i + 1].text}"))
            i += 2
        else:
            merged.append(t)
            i += 1
    return merged


def canonical(text, line, file, lx):
    return apply_lexicon(normalize(tokenize(text, line, file), lx), lx)


def show(toks):
    return " ".join(t.text or str(t.value) for t in toks)
