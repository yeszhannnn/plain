#!/usr/bin/env python3
"""engc: prototype compiler for Plain, a programming language written as plain English.

Pipeline:
    .eng text -> sentences -> tokens -> normalize -> lexicon (synonyms) ->
    all-parses parser -> ambiguity check -> Core IR -> type inference ->
    backend (interpreter | C | LLVM IR -> clang -> native binary)

Usage:
    engc.py run   FILE            execute directly
    engc.py build FILE [-o OUT]   compile to a native executable via LLVM IR + clang
    engc.py ir    FILE            print LLVM IR        (alias: --emit-llvm)
    engc.py c     FILE            print C source       (alias: --emit-c)
    engc.py core  FILE            print Core IR        (alias: --dump-core)
"""

import difflib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))


class EngError(Exception):
    def __init__(self, message, line=None):
        super().__init__(message)
        self.message = message
        self.line = line


class AmbiguityError(EngError):
    pass


# ───────────────────────────── Lexicon ─────────────────────────────

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
        self.print_forms = {p[0] for p, kw in self.phrases.items() if kw == "PRINT" and len(p) == 1}
        self.single_forms = sorted(p[0] for p in self.phrases if len(p) == 1)


# ───────────────────────────── Tokens ─────────────────────────────

@dataclass
class Tok:
    kind: str           # WORD NUM STR COMMA LP RP or a canonical keyword (PRINT, IS, ...)
    value: object       # lowercase word / int / string / keyword source text
    text: str = ""      # original spelling, used for unquoted text and messages
    sym: bool = False   # operator written as a symbol (+ * >) rather than a word

    def __repr__(self):
        return f"{self.kind}({self.value!r})" if self.kind in ("WORD", "NUM", "STR") else self.kind


BINOPS = {"PLUS": ("+", 1), "MINUS": ("-", 1), "MUL": ("*", 2), "TIMES": ("*", 2), "DIV": ("/", 2)}
CMPOPS = {"GT": ">", "LT": "<", "GE": ">=", "LE": "<=", "NE": "!=", "IS": "=="}
OP_WORDS = {"+": "plus", "-": "minus", "*": "times", "/": "divided by"}


@dataclass
class Sentence:
    text: str
    line: int
    header_colon: bool = False  # sentence ended with ':' and the body follows on the same line


@dataclass
class Blank:
    line: int


def normalize_quotes(src):
    return (src.replace("“", '"').replace("”", '"')
               .replace("‘", "'").replace("’", "'"))


def is_apostrophe(s, i):
    return 0 < i < len(s) - 1 and s[i - 1].isalnum() and s[i + 1].isalnum()


def split_sentences(src):
    """Split source into sentences on '.', ':' and newlines, respecting quotes."""
    out = []
    for lineno, raw in enumerate(normalize_quotes(src).split("\n"), 1):
        if not raw.strip():
            out.append(Blank(lineno))
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
                    out.append(Sentence(buf.strip(), lineno))
                buf = ""
            elif ch == ":":
                rest = raw[i + 1:].strip().strip(".").strip()
                if buf.strip():
                    out.append(Sentence(buf.strip(), lineno, header_colon=bool(rest)))
                buf = ""
            else:
                buf += ch
            i += 1
        if quote:
            raise EngError("A quote is opened but never closed.", lineno)
        if buf.strip():
            out.append(Sentence(buf.strip(), lineno))
    return out


def tokenize(s, line):
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
        elif ch in "+-*×/<>=":
            toks.append(Tok("SYM", ch, ch))
            i += 1
        elif ch == ",":
            toks.append(Tok("COMMA", ",", ","))
            i += 1
        elif ch in "()":
            toks.append(Tok("LP" if ch == "(" else "RP", ch, ch))
            i += 1
        else:
            raise EngError(f'I don\'t understand the symbol "{ch}".', line)
    return toks


def normalize(toks, lx):
    """Contractions, number words, possessives, articles and filler. Pure and deterministic."""
    # contractions: that's -> that is
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

    # articles and filler
    toks = [t for t in toks if not (t.kind == "WORD" and (t.value in lx.articles or t.value in lx.filler))]

    # label words: "print the word 'Hello'", "say the number 5"
    out = []
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        prev = toks[i - 1] if i > 0 else None
        if t.kind == "WORD" and t.value in lx.label_words and nxt is not None and (
                nxt.kind in ("STR", "NUM") or
                (prev is not None and prev.kind == "WORD" and prev.value in lx.print_forms and nxt.kind == "WORD")):
            continue
        out.append(t)
    return out


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
                    if key in lx.phrases:
                        out.append(Tok(lx.phrases[key], " ".join(key), " ".join(w.text for w in window)))
                        i += n
                        matched = True
                        break
            if matched:
                continue
        out.append(t)
        i += 1
    return out


# ───────────────────────────── Core IR ─────────────────────────────

@dataclass
class Num:
    value: int
    def __str__(self): return f"Int({self.value})"

@dataclass
class Text:
    value: str
    def __str__(self): return f"Text({json.dumps(self.value, ensure_ascii=False)})"

@dataclass
class Var:
    name: str
    display: str = ""
    def __str__(self): return f"Var({self.name})"

@dataclass
class Bin:
    op: str
    left: object
    right: object
    def __str__(self): return f"Bin({self.op}, {self.left}, {self.right})"

@dataclass
class Cmp:
    op: str
    left: object
    right: object
    def __str__(self): return f"Cmp({self.op}, {self.left}, {self.right})"

@dataclass
class Logic:
    op: str             # and / or / not
    args: list
    def __str__(self): return f"{self.op.capitalize()}({', '.join(map(str, self.args))})"

@dataclass
class Assign:
    name: str
    expr: object
    line: int = 0
    def dump(self, ind): return [f"{ind}Assign({self.name}, {self.expr})"]

@dataclass
class Update:
    name: str
    op: str
    expr: object
    line: int = 0
    def dump(self, ind): return [f"{ind}Update({self.name}, {self.op}, {self.expr})"]

@dataclass
class Print:
    parts: list
    line: int = 0
    def dump(self, ind): return [f"{ind}Print({', '.join(map(str, self.parts))})"]

@dataclass
class Repeat:
    count: object
    body: list = field(default_factory=list)
    line: int = 0
    def dump(self, ind): return [f"{ind}Repeat({self.count})"] + dump_body(self.body, ind)

@dataclass
class While:
    cond: object
    body: list = field(default_factory=list)
    line: int = 0
    def dump(self, ind): return [f"{ind}While({self.cond})"] + dump_body(self.body, ind)

@dataclass
class If:
    cond: object
    then: list = field(default_factory=list)
    else_: list = None
    line: int = 0
    def dump(self, ind):
        lines = [f"{ind}If({self.cond})"] + dump_body(self.then, ind)
        if self.else_ is not None:
            lines += [f"{ind}Else"] + dump_body(self.else_, ind)
        return lines


def dump_body(body, ind):
    return [l for s in body for l in s.dump(ind + "  ")]


def dump_program(prog):
    return "\n".join(l for s in prog for l in s.dump(""))


# Markers for block headers produced by the sentence parser.
@dataclass
class End:
    pass

@dataclass
class ElseMark:
    stmt: object = None   # inline statement, or None when a block follows


# ───────────────────────────── Parser ─────────────────────────────
#
# Every parse function returns a list of all successful parses (list-of-successes).
# This gives the same answer as an Earley parser over the ambiguous grammar: if a
# sentence has two different readings, both come back, and the compiler refuses
# to guess.

class Parser:
    def __init__(self, lx):
        self.lx = lx
        self.known = {}       # variable name -> display name
        self.last_var = None  # target of "it"
        self.line = 0

    def err(self, msg):
        raise EngError(msg, self.line)

    # ── expressions ──

    def atoms(self, toks, i):
        if i >= len(toks):
            return []
        t = toks[i]
        if t.kind == "NUM":
            return [(Num(t.value), i + 1)]
        if t.kind == "MINUS" and t.sym and i + 1 < len(toks) and toks[i + 1].kind == "NUM":
            return [(Num(-toks[i + 1].value), i + 2)]
        if t.kind == "STR":
            return [(Text(t.value), i + 1)]
        if t.kind == "IT":
            if self.last_var is None:
                self.err('I don\'t know what "it" means here. Nothing has been set yet.')
            return [(Var(self.last_var, self.known[self.last_var]), i + 1)]
        if t.kind == "WORD":
            if t.value in self.known:
                return [(Var(t.value, self.known[t.value]), i + 1)]
            if "." in t.value:
                self.err(f'I don\'t know "{t.text}" yet. Set it first, for example: set {t.text} to 0.')
            return [(Text(t.text), i + 1)]  # an unknown single word is just text
        if t.kind == "LP":
            depth = 0
            for j in range(i, len(toks)):
                depth += {"LP": 1, "RP": -1}.get(toks[j].kind, 0)
                if depth == 0:
                    return [(e, j + 1) for e in self.expr_full(toks[i + 1:j])]
            self.err("A bracket is opened but never closed.")
        return []

    def seqs(self, toks, i):
        """All operand/operator chains starting at i: (operands, operators, end)."""
        res = []
        for a, j in self.atoms(toks, i):
            res.append(([a], [], j))
            if j < len(toks) and toks[j].kind in BINOPS:
                op, prec = BINOPS[toks[j].kind]
                for more, ops, k in self.seqs(toks, j + 1):
                    res.append(([a] + more, [(op, prec, toks[j].sym)] + ops, k))
        return res

    @staticmethod
    def trees(operands, ops):
        """All bracketings allowed by the precedence rules.

        Symbols (+ * /) follow school maths. Word operators of the same strength read
        left to right. Spoken English has no precedence between "plus" and "times",
        so "1 plus 2 times 3" keeps both bracketings and becomes an ambiguity error.
        """
        memo = {}

        def constrained(a, b):
            return a[1] or b[2] or a[0] == b[1]

        def build(i, j):
            if (i, j) in memo:
                return memo[(i, j)]
            if i == j:
                return [(operands[i], None)]
            res = []
            for k in range(i, j):
                op = ops[k]
                for l, lt in build(i, k):
                    if lt and constrained(lt, op) and lt[0] < op[1]:
                        continue
                    for r, rt in build(k + 1, j):
                        if rt and constrained(rt, op) and rt[0] <= op[1]:
                            continue
                        res.append((Bin(op[0], l, r), (op[1], op[2])))
            memo[(i, j)] = res
            return res

        return [t for t, _ in build(0, len(operands) - 1)]

    def exprs(self, toks, i):
        return [(t, j) for operands, ops, j in self.seqs(toks, i) for t in self.trees(operands, ops)]

    def expr_full(self, toks):
        return [e for e, j in self.exprs(toks, 0) if j == len(toks)]

    def one_expr(self, toks, what="this"):
        cands = unique(self.expr_full(toks))
        if not cands:
            self.err(f'I couldn\'t understand {what}: "{show(toks)}".')
        if len(cands) > 1:
            raise AmbiguityError(ambiguity_text([english_expr(c) for c in cands]), self.line)
        return cands[0]

    # ── conditions ──

    def conds(self, toks):
        for kind, ctor in (("OR", "or"), ("AND", "and")):
            parts = split_top(toks, kind)
            if len(parts) > 1:
                options = [[]]
                for p in parts:
                    options = [o + [c] for o in options for c in self.conds(p)]
                return [Logic(ctor, o) for o in options]
        if toks and toks[0].kind == "NOT":
            return [Logic("not", [c]) for c in self.conds(toks[1:])]
        idx = [i for i, t in enumerate(toks) if t.kind in CMPOPS]
        if len(idx) != 1:
            return []
        k = idx[0]
        return [Cmp(CMPOPS[toks[k].kind], l, r)
                for l in self.expr_full(toks[:k]) for r in self.expr_full(toks[k + 1:])]

    def one_cond(self, toks):
        cands = unique(self.conds(toks))
        if not cands:
            self.err(f'I couldn\'t understand the condition "{show(toks)}". '
                     'Try something like: if the score is more than 50.')
        if len(cands) > 1:
            raise AmbiguityError(ambiguity_text([english_cond(c) for c in cands]), self.line)
        return cands[0]

    # ── statements ──

    def target(self, tok):
        if tok.kind == "IT" and self.last_var is not None:
            return Tok("WORD", self.last_var, self.known[self.last_var])
        if tok.kind != "WORD":
            self.err(f'"{tok.text}" can\'t be used as a name.')
        return tok

    def define(self, tok):
        self.known.setdefault(tok.value, tok.text)
        self.last_var = tok.value

    def sentence(self, toks):
        """Parse one sentence. Returns a statement, a block header, End or ElseMark."""
        if not toks:
            return None
        k = toks[0].kind
        if k == "END" and len(toks) == 1:
            return End()
        if k == "OTHERWISE":
            rest = strip_commas(toks[1:])
            return ElseMark(self.sentence(rest) if rest else None)
        if k == "IF":
            cut = next((i for i, t in enumerate(toks) if t.kind in ("COMMA", "THEN")), None)
            if cut is None:
                return If(self.one_cond(toks[1:]), line=self.line)          # block follows
            cond = self.one_cond(toks[1:cut])
            return If(cond, [self.sentence(strip_commas(toks[cut + 1:]))], line=self.line)
        guard = next((i for i, t in enumerate(toks) if t.kind == "IF" and i > 0), None)
        if guard is not None:
            body = self.sentence(strip_commas(toks[:guard]))
            return If(self.one_cond(toks[guard + 1:]), [body], line=self.line)
        if k == "REPEAT":
            t = next((i for i, t in enumerate(toks) if t.kind == "TIMES"), None)
            if t is None:
                self.err('Say how many times, for example: repeat 3 times.')
            count = self.one_expr(toks[1:t], "how many times to repeat")
            rest = strip_commas(toks[t + 1:])
            return Repeat(count, [self.sentence(rest)] if rest else [], line=self.line)
        if k == "KEEP":
            stop = next((i for i, t in enumerate(toks) if t.kind in ("UNTIL", "WHILE")), None)
            if stop is None:
                self.err('Say when to stop, for example: keep doubling x until it is more than 100.')
            body = self.simple(strip_commas(toks[1:stop]))
            cond = self.one_cond(toks[stop + 1:])
            if toks[stop].kind == "UNTIL":
                cond = Logic("not", [cond])
            return While(cond, [body], line=self.line)
        return self.simple(toks)

    def simple(self, toks):
        k = toks[0].kind
        n = len(toks)
        if k == "PRINT":
            return self.print_stmt(toks)
        if k == "SET":
            to = next((i for i, t in enumerate(toks) if t.kind == "TO"), None)
            if to != 2:
                self.err('Write it like: set age to 18.')
            tgt = self.target(toks[1])
            e = self.one_expr(toks[3:], "the value")
            self.define(tgt)
            return Assign(tgt.value, e, self.line)
        if k == "WORD" and n > 2 and toks[1].kind == "IS":
            tgt = toks[0]
            e = self.one_expr(toks[2:], "the value")
            self.define(tgt)
            return Assign(tgt.value, e, self.line)
        if k in ("ADD", "SUB") and n >= 4 and toks[-2].kind == ("TO" if k == "ADD" else "FROM"):
            e = self.one_expr(toks[1:-2], "the amount")
            tgt = self.target(toks[-1])
            self.define(tgt)
            return Update(tgt.value, "+" if k == "ADD" else "-", e, self.line)
        if k in ("INC", "DEC") and n >= 2:
            tgt = self.target(toks[1])
            e = Num(1)
            if n > 2:
                if toks[2].kind != "BY":
                    self.err(f'Write it like: {toks[0].value} {toks[1].text} by 5.')
                e = self.one_expr(toks[3:], "the amount")
            self.define(tgt)
            return Update(tgt.value, "+" if k == "INC" else "-", e, self.line)
        if k in ("DOUBLE", "HALVE") and n == 2:
            tgt = self.target(toks[1])
            self.define(tgt)
            return Update(tgt.value, "*" if k == "DOUBLE" else "/", Num(2), self.line)
        if k == "WORD":
            close = difflib.get_close_matches(toks[0].value, self.lx.single_forms, n=1, cutoff=0.7)
            hint = f' Did you mean "{close[0]}"?' if close else ""
            self.err(f'I don\'t know the word "{toks[0].text}".{hint}')
        self.err(f'I couldn\'t understand this sentence: "{show(toks)}".')

    def print_args(self, toks, i):
        res = []
        for e, j in self.exprs(toks, i):
            res.append(([e], j))
            if j < len(toks) and toks[j].kind == "CONCAT":
                res += [([e] + more, k) for more, k in self.print_args(toks, j + 1)]
        return res

    def print_stmt(self, toks):
        n = len(toks)
        cands = []
        for parts, j in self.print_args(toks, 1):
            if j == n:
                cands.append(Print(parts, self.line))
            else:
                for cnt, k in self.exprs(toks, j):
                    if k == n - 1 and toks[k].kind == "TIMES":
                        cands.append(Repeat(cnt, [Print(parts, self.line)], self.line))
        cands = unique(cands, key=lambda s: dump_program([s]))
        if not cands:
            self.err(f'I couldn\'t understand what to {toks[0].value}: "{show(toks)}". '
                     'Put text with several words in quotes.')
        if len(cands) > 1:
            raise AmbiguityError(ambiguity_text([english_stmt(c, toks[0].value) for c in cands]), self.line)
        return cands[0]


def split_top(toks, kind):
    parts, cur, depth = [], [], 0
    for t in toks:
        depth += {"LP": 1, "RP": -1}.get(t.kind, 0)
        if t.kind == kind and depth == 0:
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    return parts + [cur]


def strip_commas(toks):
    while toks and toks[0].kind == "COMMA":
        toks = toks[1:]
    while toks and toks[-1].kind == "COMMA":
        toks = toks[:-1]
    return toks


def unique(items, key=str):
    seen, out = set(), []
    for it in items:
        k = key(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def show(toks):
    return " ".join(t.text or str(t.value) for t in toks)


# ── back to English, for error messages ──

def english_expr(e, top=True):
    if isinstance(e, Num):
        return str(e.value)
    if isinstance(e, Text):
        return json.dumps(e.value, ensure_ascii=False)
    if isinstance(e, Var):
        return e.display or e.name
    if isinstance(e, Bin):
        s = f"{english_expr(e.left, False)} {OP_WORDS[e.op]} {english_expr(e.right, False)}"
        return s if top else f"({s})"
    return str(e)


def english_cond(c):
    if isinstance(c, Cmp):
        words = {">": "is more than", "<": "is less than", ">=": "is at least",
                 "<=": "is at most", "!=": "is not", "==": "is"}
        return f"{english_expr(c.left)} {words[c.op]} {english_expr(c.right)}"
    if c.op == "not":
        return f"not ({english_cond(c.args[0])})"
    return f" {c.op} ".join(f"({english_cond(a)})" for a in c.args)


def english_stmt(s, verb):
    if isinstance(s, Repeat):
        return f"{english_stmt(s.body[0], verb)}, {english_expr(s.count, False)} times"
    return f"{verb} " + " and then ".join(english_expr(p) for p in s.parts)


def ambiguity_text(readings):
    lines = [f"This sentence can mean {len(readings)} different things:"]
    lines += [f"  {i}) {r}" for i, r in enumerate(readings, 1)]
    lines.append("Please add brackets or rewrite it so that only one reading is possible.")
    return "\n".join(lines)


# ── blocks ──

def open_if(body):
    """The If that a following "otherwise" belongs to, following else-if chains."""
    node = body[-1] if body else None
    while isinstance(node, If) and node.else_ is not None and len(node.else_) == 1 \
            and isinstance(node.else_[0], If):
        node = node.else_[0]
    return node if isinstance(node, If) and node.else_ is None else None


def parse_program(src, lx=None):
    lx = lx or Lexicon()
    p = Parser(lx)
    root = []
    stack = []  # frames: [kind, list, single_line, node]

    def current():
        return stack[-1][1] if stack else root

    def add(stmt):
        current().append(stmt)
        while stack and stack[-1][2] and stack[-1][1]:
            stack.pop()   # a same-line body ("repeat 5 times: say hi") holds one sentence

    pending_inline = False
    for item in split_sentences(src):
        if isinstance(item, Blank):
            if stack and stack[-1][1]:
                stack.pop()
            continue
        p.line = item.line
        toks = apply_lexicon(normalize(tokenize(item.text, item.line), lx), lx)
        node = p.sentence(toks)
        if node is None:
            continue
        if isinstance(node, End):
            if not stack:
                raise EngError('"That\'s all" closes a group, but no group is open here.', item.line)
            stack.pop()
            continue
        if isinstance(node, ElseMark):
            prev = open_if(current())
            if prev is None and stack and stack[-1][0] == "then":
                stack.pop()   # "otherwise" ends the block of a multi-line "if"
                prev = open_if(current())
            if prev is None:
                raise EngError('"Otherwise" needs an "if" sentence right before it.', item.line)
            prev.else_ = []
            if node.stmt is not None:
                prev.else_.append(node.stmt)
            else:
                stack.append(["else", prev.else_, item.header_colon, prev])
            continue
        is_header = (isinstance(node, Repeat) and not node.body) or (isinstance(node, If) and not node.then)
        add(node)
        if is_header:
            body = node.body if isinstance(node, Repeat) else node.then
            stack.append(["then" if isinstance(node, If) else "repeat", body, item.header_colon, node])
    return root


# ───────────────────────────── Types ─────────────────────────────

INT, TEXT, BOOL = "Int", "Text", "Bool"
TYPE_WORDS = {INT: "a number", TEXT: "text", BOOL: "yes/no"}


class Types:
    def __init__(self):
        self.vars = {}    # name -> type
        self.where = {}   # name -> line of first assignment

    def expr(self, e, line):
        if isinstance(e, Num):
            return INT
        if isinstance(e, Text):
            return TEXT
        if isinstance(e, Var):
            if e.name not in self.vars:
                raise EngError(f'"{e.display or e.name}" is used before it has a value.', line)
            return self.vars[e.name]
        if isinstance(e, Bin):
            l, r = self.expr(e.left, line), self.expr(e.right, line)
            if l != INT or r != INT:
                raise EngError(f'I can only do arithmetic with numbers, but got {TYPE_WORDS[l]} '
                               f'and {TYPE_WORDS[r]} in "{english_expr(e)}".', line)
            return INT
        if isinstance(e, Cmp):
            l, r = self.expr(e.left, line), self.expr(e.right, line)
            if l != r:
                raise EngError(f'I can\'t compare {TYPE_WORDS[l]} with {TYPE_WORDS[r]} in '
                               f'"{english_cond(e)}".', line)
            if e.op not in ("==", "!=") and l != INT:
                raise EngError(f'Only numbers can be bigger or smaller, in "{english_cond(e)}".', line)
            return BOOL
        if isinstance(e, Logic):
            for a in e.args:
                self.expr(a, line)
            return BOOL
        raise EngError(f"internal: unknown expression {e}", line)

    def bind(self, name, t, line):
        old = self.vars.get(name)
        if old is None:
            self.vars[name], self.where[name] = t, line
        elif old != t:
            raise EngError(f'"{name}" was {TYPE_WORDS[old]} on line {self.where[name]}, '
                           f'and here it would become {TYPE_WORDS[t]}. A name keeps one kind of value.', line)

    def block(self, body):
        for s in body:
            if isinstance(s, Assign):
                self.bind(s.name, self.expr(s.expr, s.line), s.line)
            elif isinstance(s, Update):
                self.bind(s.name, INT, s.line)   # counters start at zero
                self.expr(Bin(s.op, Var(s.name), s.expr), s.line)
            elif isinstance(s, Print):
                for part in s.parts:
                    self.expr(part, s.line)
            elif isinstance(s, Repeat):
                if self.expr(s.count, s.line) != INT:
                    raise EngError("The number of times must be a number.", s.line)
                self.block(s.body)
            elif isinstance(s, While):
                self.expr(s.cond, s.line)
                self.block(s.body)
            elif isinstance(s, If):
                self.expr(s.cond, s.line)
                self.block(s.then)
                self.block(s.else_ or [])


def check(prog):
    t = Types()
    t.block(prog)
    return t


def compile_front(src):
    prog = parse_program(src)
    return prog, check(prog)


# ───────────────────────────── Backend: interpreter ─────────────────────────────

ZERO = {INT: 0, TEXT: "", BOOL: False}


def idiv(a, b):
    if b == 0:
        raise EngError("Division by zero.")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def fmt_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def run(prog, types, out=sys.stdout):
    env = {name: ZERO[t] for name, t in types.vars.items()}

    def ev(e):
        if isinstance(e, Num):
            return e.value
        if isinstance(e, Text):
            return e.value
        if isinstance(e, Var):
            return env[e.name]
        if isinstance(e, Bin):
            a, b = ev(e.left), ev(e.right)
            return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: idiv(a, b)}[e.op]()
        if isinstance(e, Cmp):
            a, b = ev(e.left), ev(e.right)
            return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b, "==": a == b, "!=": a != b}[e.op]
        if isinstance(e, Logic):
            if e.op == "not":
                return not ev(e.args[0])
            vals = [ev(a) for a in e.args]
            return all(vals) if e.op == "and" else any(vals)

    def ex(body):
        for s in body:
            if isinstance(s, Assign):
                env[s.name] = ev(s.expr)
            elif isinstance(s, Update):
                env[s.name] = ev(Bin(s.op, Var(s.name), s.expr))
            elif isinstance(s, Print):
                out.write(" ".join(fmt_value(ev(p)) for p in s.parts) + "\n")
            elif isinstance(s, Repeat):
                for _ in range(ev(s.count)):
                    ex(s.body)
            elif isinstance(s, While):
                while ev(s.cond):
                    ex(s.body)
            elif isinstance(s, If):
                ex(s.then if ev(s.cond) else (s.else_ or []))

    ex(prog)


# ───────────────────────────── Backend: C ─────────────────────────────

def c_name(name):
    return "v_" + "".join(ch if ch.isalnum() else "_" for ch in name)


def emit_c(prog, types):
    ctype = {INT: "long long", TEXT: "const char *", BOOL: "int"}
    czero = {INT: "0", TEXT: '""', BOOL: "0"}
    lines = ["#include <stdio.h>", "#include <string.h>", "", "int main(void) {"]
    for name, t in types.vars.items():
        lines.append(f"    {ctype[t]} {c_name(name)} = {czero[t]};")

    def ty(e):
        return types.expr(e, 0)

    def ce(e):
        if isinstance(e, Num):
            return f"{e.value}LL"
        if isinstance(e, Text):
            return json.dumps(e.value)
        if isinstance(e, Var):
            return c_name(e.name)
        if isinstance(e, Bin):
            return f"({ce(e.left)} {e.op} {ce(e.right)})"
        if isinstance(e, Cmp):
            if ty(e.left) == TEXT:
                return f"(strcmp({ce(e.left)}, {ce(e.right)}) {e.op} 0)"
            return f"({ce(e.left)} {e.op} {ce(e.right)})"
        if isinstance(e, Logic):
            if e.op == "not":
                return f"(!{ce(e.args[0])})"
            return "(" + (" && " if e.op == "and" else " || ").join(ce(a) for a in e.args) + ")"

    counter = [0]

    def cs(body, ind):
        for s in body:
            if isinstance(s, Assign):
                lines.append(f"{ind}{c_name(s.name)} = {ce(s.expr)};")
            elif isinstance(s, Update):
                lines.append(f"{ind}{c_name(s.name)} = {ce(Bin(s.op, Var(s.name), s.expr))};")
            elif isinstance(s, Print):
                fmt, args = [], []
                for p in s.parts:
                    t = ty(p)
                    fmt.append("%lld" if t == INT else "%s")
                    args.append(f'({ce(p)} ? "true" : "false")' if t == BOOL else ce(p))
                lines.append(f'{ind}printf({json.dumps(" ".join(fmt) + chr(10))}, {", ".join(args)});')
            elif isinstance(s, Repeat):
                counter[0] += 1
                i = f"i{counter[0]}"
                lines.append(f"{ind}for (long long {i} = 0, n{i} = {ce(s.count)}; {i} < n{i}; {i}++) {{")
                cs(s.body, ind + "    ")
                lines.append(f"{ind}}}")
            elif isinstance(s, While):
                lines.append(f"{ind}while ({ce(s.cond)}) {{")
                cs(s.body, ind + "    ")
                lines.append(f"{ind}}}")
            elif isinstance(s, If):
                lines.append(f"{ind}if ({ce(s.cond)}) {{")
                cs(s.then, ind + "    ")
                if s.else_ is not None:
                    lines.append(f"{ind}}} else {{")
                    cs(s.else_, ind + "    ")
                lines.append(f"{ind}}}")

    cs(prog, "    ")
    lines += ["    return 0;", "}", ""]
    return "\n".join(lines)


# ───────────────────────────── Backend: LLVM IR ─────────────────────────────

LLTYPE = {INT: "i64", TEXT: "ptr", BOOL: "i1"}


class LLVMGen:
    def __init__(self, types):
        self.types = types
        self.globals = {}   # string -> global name
        self.body = []
        self.allocas = []
        self.tmp = 0
        self.lbl = 0

    def t(self):
        self.tmp += 1
        return f"%t{self.tmp}"

    def label(self, hint):
        self.lbl += 1
        return f"{hint}{self.lbl}"

    def emit(self, s):
        self.body.append("  " + s)

    def start(self, label):
        self.body.append(f"{label}:")

    def string(self, s):
        if s not in self.globals:
            self.globals[s] = f"@.s{len(self.globals)}"
        return self.globals[s]

    @staticmethod
    def var(name):
        return "%v." + "".join(ch if ch.isalnum() or ch in "._" else "_" for ch in name)

    def ty(self, e):
        return self.types.expr(e, 0)

    def expr(self, e):
        if isinstance(e, Num):
            return str(e.value)
        if isinstance(e, Text):
            return self.string(e.value)
        if isinstance(e, Var):
            r = self.t()
            self.emit(f"{r} = load {LLTYPE[self.types.vars[e.name]]}, ptr {self.var(e.name)}")
            return r
        if isinstance(e, Bin):
            a, b = self.expr(e.left), self.expr(e.right)
            r = self.t()
            op = {"+": "add", "-": "sub", "*": "mul", "/": "sdiv"}[e.op]
            self.emit(f"{r} = {op} i64 {a}, {b}")
            return r
        if isinstance(e, Cmp):
            lt = self.ty(e.left)
            a, b = self.expr(e.left), self.expr(e.right)
            pred = {">": "sgt", "<": "slt", ">=": "sge", "<=": "sle", "==": "eq", "!=": "ne"}[e.op]
            r = self.t()
            if lt == TEXT:
                c = self.t()
                self.emit(f"{c} = call i32 @strcmp(ptr {a}, ptr {b})")
                self.emit(f"{r} = icmp {pred} i32 {c}, 0")
            else:
                self.emit(f"{r} = icmp {pred} {LLTYPE[lt]} {a}, {b}")
            return r
        if isinstance(e, Logic):
            vals = [self.expr(a) for a in e.args]
            if e.op == "not":
                r = self.t()
                self.emit(f"{r} = xor i1 {vals[0]}, true")
                return r
            acc = vals[0]
            for v in vals[1:]:
                r = self.t()
                self.emit(f"{r} = {e.op} i1 {acc}, {v}")
                acc = r
            return acc

    def block(self, body):
        for s in body:
            if isinstance(s, Assign):
                v = self.expr(s.expr)
                self.emit(f"store {LLTYPE[self.types.vars[s.name]]} {v}, ptr {self.var(s.name)}")
            elif isinstance(s, Update):
                v = self.expr(Bin(s.op, Var(s.name), s.expr))
                self.emit(f"store i64 {v}, ptr {self.var(s.name)}")
            elif isinstance(s, Print):
                fmt, args = [], []
                for p in s.parts:
                    t = self.ty(p)
                    v = self.expr(p)
                    if t == INT:
                        fmt.append("%lld")
                        args.append(f"i64 {v}")
                    else:
                        if t == BOOL:
                            r = self.t()
                            self.emit(f"{r} = select i1 {v}, ptr {self.string('true')}, ptr {self.string('false')}")
                            v = r
                        fmt.append("%s")
                        args.append(f"ptr {v}")
                f = self.string(" ".join(fmt) + "\n")
                r = self.t()
                self.emit(f"{r} = call i32 (ptr, ...) @printf(ptr {f}, {', '.join(args)})")
            elif isinstance(s, Repeat):
                n = self.expr(s.count)
                ctr = f"%rep{self.lbl + 1}"
                self.allocas.append(f"{ctr} = alloca i64")
                self.emit(f"store i64 0, ptr {ctr}")
                cond, loop, done = self.label("repeat.cond"), self.label("repeat.body"), self.label("repeat.end")
                self.emit(f"br label %{cond}")
                self.start(cond)
                i = self.t()
                c = self.t()
                self.emit(f"{i} = load i64, ptr {ctr}")
                self.emit(f"{c} = icmp slt i64 {i}, {n}")
                self.emit(f"br i1 {c}, label %{loop}, label %{done}")
                self.start(loop)
                self.block(s.body)
                i2, nx = self.t(), self.t()
                self.emit(f"{i2} = load i64, ptr {ctr}")
                self.emit(f"{nx} = add i64 {i2}, 1")
                self.emit(f"store i64 {nx}, ptr {ctr}")
                self.emit(f"br label %{cond}")
                self.start(done)
            elif isinstance(s, While):
                cond, loop, done = self.label("while.cond"), self.label("while.body"), self.label("while.end")
                self.emit(f"br label %{cond}")
                self.start(cond)
                c = self.expr(s.cond)
                self.emit(f"br i1 {c}, label %{loop}, label %{done}")
                self.start(loop)
                self.block(s.body)
                self.emit(f"br label %{cond}")
                self.start(done)
            elif isinstance(s, If):
                c = self.expr(s.cond)
                yes, no, done = self.label("if.then"), self.label("if.else"), self.label("if.end")
                self.emit(f"br i1 {c}, label %{yes}, label %{no}")
                self.start(yes)
                self.block(s.then)
                self.emit(f"br label %{done}")
                self.start(no)
                self.block(s.else_ or [])
                self.emit(f"br label %{done}")
                self.start(done)

    def module(self, prog):
        empty = self.string("")
        self.block(prog)
        init = []
        for name, t in self.types.vars.items():
            self.allocas.insert(0, f"{self.var(name)} = alloca {LLTYPE[t]}")
            init.append(f"store {LLTYPE[t]} {dict([(INT, '0'), (TEXT, empty), (BOOL, 'false')])[t]}, "
                        f"ptr {self.var(name)}")
        out = ["; generated by engc from Plain source", ""]
        for s, g in self.globals.items():
            data = s.encode("utf-8") + b"\0"
            enc = "".join(chr(b) if 32 <= b < 127 and chr(b) not in '"\\' else f"\\{b:02X}" for b in data)
            out.append(f'{g} = private unnamed_addr constant [{len(data)} x i8] c"{enc}"')
        out += ["", "declare i32 @printf(ptr, ...)", "declare i32 @strcmp(ptr, ptr)", "",
                "define i32 @main() {", "entry:"]
        out += ["  " + a for a in self.allocas] + ["  " + s for s in init]
        out += self.body
        out += ["  ret i32 0", "}", ""]
        return "\n".join(out)


def emit_llvm(prog, types):
    return LLVMGen(types).module(prog)


def build(prog, types, output):
    ir = emit_llvm(prog, types)
    with tempfile.TemporaryDirectory() as d:
        ll = os.path.join(d, "out.ll")
        with open(ll, "w") as f:
            f.write(ir)
        subprocess.run(["clang", "-O2", "-Wno-override-module", ll, "-o", output], check=True)


# ───────────────────────────── CLI ─────────────────────────────

ALIASES = {"--emit-llvm": "ir", "--emit-c": "c", "--dump-core": "core"}


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    cmd, path = ALIASES.get(argv[0], argv[0]), argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        prog, types = compile_front(src)
        if cmd == "run":
            run(prog, types)
        elif cmd == "core":
            print(dump_program(prog))
        elif cmd == "c":
            print(emit_c(prog, types), end="")
        elif cmd == "ir":
            print(emit_llvm(prog, types), end="")
        elif cmd == "build":
            out = argv[argv.index("-o") + 1] if "-o" in argv else os.path.splitext(path)[0]
            build(prog, types, out)
            print(f"built {out}")
        else:
            print(f"unknown command {cmd}\n\n{__doc__.strip()}")
            return 2
    except EngError as e:
        where = f"{os.path.basename(path)}, line {e.line}" if e.line else os.path.basename(path)
        print(f"{where}:\n{e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
