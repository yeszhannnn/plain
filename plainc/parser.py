"""Sentences -> Core IR.

Every parse function returns all successful parses (list-of-successes). This
gives the same answer as an Earley parser over the ambiguous grammar: if a
sentence has two different readings, both come back, and the compiler refuses
to guess.
"""

import difflib
import os

from .core import (AddTo, Ask, Assign, Bin, Break, Builtin, Call, CallStmt, Cmp, Concat, Continue, Dec, DeleteFile,
                   ElseMark, EmptyMap, End, Exit, ForEach, ForRange, FuncDef, FuncSig, Handler, If, Index, IsNum,
                   ListLit, Logic, Lookup, Msg, MsgNum, Name, Num, OneOf, Persist, Print, Random, RandomItem,
                   RemoveAt, RemoveKey, RemoveValue, Repeat, Reply, Return, Reverse, Seq, Slice, Sort, Text, Truth,
                   Var, While, WriteFile, english)
from .errors import AmbiguityError, EngError
from .syntax import USER, Lexicon, Tok, apply_lexicon, canonical, normalize, show, split_sentences, tokenize

BINOPS = {"PLUS": ("+", 1), "MINUS": ("-", 1), "MUL": ("*", 2), "TIMES": ("*", 2), "DIV": ("/", 2),
          "MOD": ("%", 2), "POW": ("^", 3)}
CMPOPS = {"GT": ">", "LT": "<", "GE": ">=", "LE": "<=", "NE": "!=", "IS": "==",
          "CONTAINS": "contains", "STARTS": "starts"}
PREFIX_BUILTINS = {"LENGTH": "length", "SUM": "sum", "AVERAGE": "average", "LARGEST": "largest",
                   "SMALLEST": "smallest", "SQRT": "sqrt", "ABS": "abs", "WORDSOF": "words", "LINESOF": "lines",
                   "LETTERSOF": "letters", "CONTENTS": None}
POSTFIX_BUILTINS = {"SQUARED": "squared", "ROUNDED": "round", "ROUNDUP": "round_up", "ROUNDDOWN": "round_down",
                    "UPPER": "upper", "LOWER": "lower", "TRIMMED": "trim", "ASNUMBER": "to_int",
                    "ASDECIMAL": "to_float", "ASTEXT": "to_text"}
INDEX_WORDS = {"item", "letter", "character"}
SLICE_WORDS = {"items", "letters", "characters"}
VALUE_MARKERS = {"find", "calculate", "compute", "get"}
PARAM_INTRO = {"a", "an", "some"}
PARAM_SELF = {"someone", "somebody", "something"}
PLACEHOLDER = "zzparam"


def unique(items, key=str):
    seen, out = set(), []
    for it in items:
        k = key(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def top_positions(toks, kinds):
    """Indexes of tokens of the given kinds that are not inside brackets."""
    out, depth = [], 0
    for i, t in enumerate(toks):
        if t.kind == "LP":
            depth += 1
        elif t.kind == "RP":
            depth -= 1
        elif depth == 0 and t.kind in kinds:
            out.append(i)
    return out


def split_top(toks, kinds):
    parts, start = [], 0
    for k in top_positions(toks, kinds):
        parts.append(toks[start:k])
        start = k + 1
    return parts + [toks[start:]]


def strip_commas(toks):
    while toks and toks[0].kind == "COMMA":
        toks = toks[1:]
    while toks and toks[-1].kind == "COMMA":
        toks = toks[:-1]
    return toks


def is_word(tok, *values):
    return tok is not None and tok.kind == "WORD" and tok.value in values


def stmt_key(s):
    return "\n".join(s.dump("")) if hasattr(s, "dump") else str(s)


def english_stmt(s):
    if isinstance(s, Assign):
        return f"set {english(s.target)} to {english(s.expr)}"
    if isinstance(s, AddTo):
        return f"add {english(s.expr)} to {english(s.target)}"
    if isinstance(s, Print):
        return "say " + " and then ".join(english(p) for p in s.parts)
    if isinstance(s, Repeat) and len(s.body) == 1:
        return f"{english_stmt(s.body[0])}, {english(s.count, False)} times"
    if isinstance(s, CallStmt):
        return english(s.call)
    return stmt_key(s)


def ambiguity_text(readings):
    lines = [f"This sentence can mean {len(readings)} different things:"]
    lines += [f"  {i}) {r}" for i, r in enumerate(readings, 1)]
    lines.append("Please add brackets or rewrite it so that only one reading is possible.")
    return "\n".join(lines)


def describe(target):
    """A settable target in English: a name, item N of a list, or an entry of a table."""
    if target[0] == "var":
        return target[1].text
    if target[0] == "index":
        return english(target[1])
    return f"{target[1].text} of {english(target[2])}"


def lit_match(el, tok):
    _, kind, value = el
    if tok.kind != kind:
        return False
    return tok.value == value if kind in ("WORD", "NUM") else True


# ───────────── function headers ─────────────

def header_split(text, line, file):
    """Raw tokens of a "to ..." sentence: header words and the inline body (after the first comma)."""
    raw = tokenize(text, line, file)
    cut = next((i for i, t in enumerate(raw) if t.kind == "COMMA"), None)
    return raw[1:cut] if cut is not None else raw[1:], (raw[cut + 1:] if cut is not None else None)


def parse_header(sentence, lx, number):
    words, _ = header_split(sentence.text, sentence.line, sentence.file)

    def fail(msg):
        raise EngError(msg, sentence.line, sentence.file)

    params, parts, k = [], [], 0
    while k < len(words):
        w = words[k]
        if w.kind == "WORD" and w.value in PARAM_INTRO and k + 1 < len(words) and words[k + 1].kind == "WORD":
            name = words[k + 1]
        elif w.kind == "WORD" and w.value in PARAM_SELF:
            name = w
            k -= 1
        else:
            parts.append(w)
            k += 1
            continue
        if apply_lexicon([Tok("WORD", name.value, name.text)], lx)[0].kind != "WORD":
            fail(f'"{name.text}" is a word the language already uses. Pick another name for this value.')
        if name.value in params:
            fail(f'"{name.text}" is used twice in this definition. Give each value its own name.')
        parts.append(Tok("WORD", f"{PLACEHOLDER}{len(params)}", name.text))
        params.append(name.value)
        k += 2
    toks = apply_lexicon(normalize(parts, lx), lx)
    returns = False
    if toks and is_word(toks[0], *VALUE_MARKERS):
        toks, returns = toks[1:], True
    elif len(toks) > 1 and is_word(toks[0], "work") and is_word(toks[1], "out"):
        toks, returns = toks[2:], True
    pattern = []
    for t in toks:
        if t.kind == "WORD" and t.value.startswith(PLACEHOLDER):
            pattern.append(("param", params[int(t.value[len(PLACEHOLDER):])]))
        else:
            pattern.append(("lit", t.kind, t.value if t.kind in ("WORD", "NUM") else t.text))
    lits = [el for el in pattern if el[0] == "lit"]
    if not lits:
        fail("A definition needs at least one word besides its values, for example: to greet someone.")
    lead = pattern[0] if pattern[0][0] == "lit" else (pattern[1] if len(pattern) > 1 else None)
    if lead is None or lead[0] != "lit" or lead[1] != "WORD":
        what = lead[2] if lead and lead[0] == "lit" else "?"
        fail(f'This definition starts with "{what}", which is already part of the language. '
             'Start it with a word of your own, for example: to find the area of a width and a height.')
    if pattern[0][0] == "param" and not returns:
        fail("A command has to start with a word, for example: to greet someone.")
    name = "_".join(str(el[2]) for el in lits)
    name = "".join(ch if ch.isalnum() else "_" for ch in name) + f"_{number}"
    display = " ".join(w.text for w in words)
    return FuncSig(name, display, pattern, params, returns, sentence.line, sentence.file)


class Funcs:
    def __init__(self):
        self.sigs = []
        self.by_header = {}   # (file, line) -> sig

    def add(self, sig):
        self.sigs.append(sig)
        self.by_header[(sig.file, sig.line)] = sig

    def prefix(self, tok):
        return [s for s in self.sigs if s.returns and s.pattern[0][0] == "lit" and lit_match(s.pattern[0], tok)]

    def postfix(self, tok):
        return [s for s in self.sigs if s.returns and s.pattern[0][0] == "param" and lit_match(s.pattern[1], tok)]

    def commands(self, tok):
        return [s for s in self.sigs if not s.returns and lit_match(s.pattern[0], tok)]

    def first_words(self):
        return {s.pattern[0][2] for s in self.sigs if s.pattern[0][0] == "lit"}


# ───────────── the parser ─────────────

class Parser:
    def __init__(self, lx, funcs):
        self.lx = lx
        self.funcs = funcs
        self.globals = {}       # name -> display
        self.user_vars = {}     # their.x -> display
        self.func = None        # FuncSig while parsing a body
        self.locals = {}
        self.visible = set()    # globals a function body can see
        self.last_var = None    # target of "it"
        self.line = 0
        self.file = ""
        self.memo = {}

    def err(self, msg):
        raise EngError(msg, self.line, self.file)

    def at(self, node):
        node.line, node.file = self.line, self.file
        return node

    # ── names ──

    def scope_of(self, name):
        if name.startswith(USER):
            return "user" if name in self.user_vars else None
        if self.func is not None:
            if name in self.locals:
                return "local"
            return "global" if name in self.visible else None
        return "global" if name in self.globals else None

    def make_var(self, name):
        scope = self.scope_of(name)
        display = {"user": self.user_vars, "local": self.locals, "global": self.globals}[scope][name]
        return Var(name, display, scope, self.func.name if scope == "local" else "")

    def define(self, tok):
        name = tok.value
        if self.scope_of(name) is None:
            if name.startswith(USER):
                self.user_vars[name] = tok.text
            elif self.func is not None:
                self.locals[name] = tok.text
            else:
                self.globals[name] = tok.text
        self.last_var = name
        return self.make_var(name)

    def enter_function(self, sig):
        self.func = sig
        self.locals = {p: p for p in sig.params}
        self.visible = set(self.globals)
        self.saved_last = self.last_var
        self.last_var = None

    def exit_function(self):
        if self.func is not None:
            self.func = None
            self.locals = {}
            self.last_var = self.saved_last

    # ── expressions ──

    def item_list(self, toks, i):
        """Values separated by commas, "and" or "or": one of "a", "b" and "c"."""
        items = []
        while True:
            got = self.primaries(toks, i)
            if not got:
                break
            e, i = max(got, key=lambda c: c[1])
            items.append(e)
            if i < len(toks) and toks[i].kind in ("COMMA", "AND", "OR"):
                i += 1
            else:
                break
        return items, i

    def atoms(self, toks, i):
        if i >= len(toks):
            return []
        t = toks[i]
        k = t.kind
        if k == "NUM":
            return [(Num(t.value), i + 1)]
        if k == "DEC":
            return [(Dec(t.value), i + 1)]
        if k == "MINUS" and t.sym and i + 1 < len(toks) and toks[i + 1].kind in ("NUM", "DEC"):
            n = toks[i + 1]
            return [((Num if n.kind == "NUM" else Dec)(-n.value), i + 2)]
        if k == "STR":
            return [(Text(t.value), i + 1)]
        if k == "NEWLINE":
            return [(Text("\n"), i + 1)]
        if k == "MSG":
            return [(Msg(), i + 1)]
        if k == "NAME":
            return [(Name(), i + 1)]
        if k == "MSGNUM":
            return [(MsgNum(), i + 1)]
        if k == "ARGS":
            return [(Builtin("arguments", []), i + 1)]
        if k == "EMPTYLIST":
            return [(ListLit([]), i + 1)]
        if k == "EMPTYMAP":
            return [(EmptyMap(), i + 1)]
        if k == "LISTOF":
            items, j = self.item_list(toks, i + 1)
            return [(ListLit(items), j)] if items else []
        if k == "ONEOF":
            items, j = self.item_list(toks, i + 1)
            if not items:
                self.err('Write it like: one of "yes", "no" and "maybe", or: one of the guests.')
            return [(RandomItem(items[0]) if len(items) == 1 else OneOf(items), j)]
        if k == "RANDOM":
            ok = i + 1 < len(toks) and toks[i + 1].kind == "FROM"
            lo = self.atoms(toks, i + 2) if ok else []
            if lo and lo[0][1] < len(toks) and toks[lo[0][1]].kind in ("TO", "AND"):
                hi = self.atoms(toks, lo[0][1] + 1)
                if hi:
                    return [(Random(lo[0][0], hi[0][0]), hi[0][1])]
            self.err('Write it like: a random number from 1 to 6.')
        if k == "REM":
            res = []
            for a, j in self.exprs(toks, i + 1):
                if j < len(toks) and toks[j].kind == "DIV":
                    res += [(Bin("%", a, b), m) for b, m in self.tight(toks, j + 1)]
            if not res:
                self.err('Write it like: the remainder of n divided by 3.')
            return res
        if k in PREFIX_BUILTINS:
            name = PREFIX_BUILTINS[k]
            return [(x if name is None else Builtin(name, [x]), j) for x, j in self.tight(toks, i + 1)]
        if k == "IT":
            if self.last_var is None:
                self.err('I don\'t know what "it" means here. Nothing has been set yet.')
            return [(self.make_var(self.last_var), i + 1)]
        if k == "LP":
            depth = 0
            for j in range(i, len(toks)):
                depth += {"LP": 1, "RP": -1}.get(toks[j].kind, 0)
                if depth == 0:
                    inner = toks[i + 1:j]
                    res = [(e, j + 1) for e in self.expr_full(inner)]
                    items = split_top(inner, ("COMMA", "AND"))
                    if len(items) > 1 and all(items):
                        parsed = [unique(self.expr_full(it)) for it in items]
                        if all(len(p) == 1 for p in parsed):
                            res.append((ListLit([p[0] for p in parsed]), j + 1))
                    return res
            self.err("A bracket is opened but never closed.")
        if k != "WORD":
            return []

        res = [(Call(sig, args), j) for sig in self.funcs.prefix(t) for args, j in self.match(sig.pattern, toks, i)]
        if t.value.startswith(USER):
            self.user_vars.setdefault(t.value, t.text)   # every user starts with zero / empty
        known = self.scope_of(t.value) is not None
        if known:
            res.append((self.make_var(t.value), i + 1))
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t.value in INDEX_WORDS:
            for n, j in self.exprs(toks, i + 1):
                if is_word(toks[j] if j < len(toks) else None, "of"):
                    res += [(Index(c, n), m) for c, m in self.tight(toks, j + 1)]
        elif t.value in SLICE_WORDS:
            for a, j in self.exprs(toks, i + 1):
                if j < len(toks) and toks[j].kind == "TO":
                    for b, m in self.exprs(toks, j + 1):
                        if is_word(toks[m] if m < len(toks) else None, "of"):
                            res += [(Slice(c, a, b), p) for c, p in self.tight(toks, m + 1)]
        elif t.value in ("first", "last") and nxt is not None and (is_word(nxt, *INDEX_WORDS, "one")) and \
                is_word(toks[i + 2] if i + 2 < len(toks) else None, "of"):
            idx = Num(1) if t.value == "first" else None
            res += [(Index(c, idx), m) for c, m in self.tight(toks, i + 3)]
        elif t.value == "file" and nxt is not None:
            res += [(Builtin("file_text", [p]), j) for p, j in self.tight(toks, i + 1)]
        if known:
            return res
        if "." in t.value and not res:
            self.err(f'I don\'t know "{t.text}" yet. Set it first, for example: set {t.text} to 0.')
        return res + [(Text(t.text), i + 1)]   # an unknown single word is just text

    def postfixes(self, a, toks, j):
        if j >= len(toks):
            return []
        t = toks[j]
        k = t.kind
        if k in POSTFIX_BUILTINS:
            return [(Builtin(POSTFIX_BUILTINS[k], [a]), j + 1)]
        if k == "JOINED_TOGETHER":
            return [(Builtin("join", [a, Text("")]), j + 1)]
        if k == "JOINED":
            return [(Builtin("join", [a, s]), m) for s, m in self.primaries(toks, j + 1)]
        if k == "SPLIT":
            return [(Builtin("split", [a, s]), m) for s, m in self.primaries(toks, j + 1)]
        res = []
        if is_word(t, "with"):
            for x, m in self.exprs(toks, j + 1):
                if m < len(toks) and toks[m].kind == "REPLACED":
                    res += [(Builtin("replace", [a, x, y]), p) for y, p in self.primaries(toks, m + 1)]
        for sig in self.funcs.postfix(t):
            res += [(Call(sig, args), m) for args, m in self.match(sig.pattern[1:], toks, j, [a])]
        return res

    def tight(self, toks, i):
        """The value right after "of": an atom, plus table lookups. Endings like "rounded" or
        "joined with" belong to the whole phrase, not to this value."""
        key = ("t", id(toks), i)
        hit = self.memo.get(key)
        if hit is not None and hit[0] is toks:
            return hit[1]
        res = []
        todo = list(self.atoms(toks, i))
        while todo:
            a, j = todo.pop(0)
            res.append((a, j))
            if isinstance(a, Var) and j < len(toks) and is_word(toks[j], "of"):
                todo += [(Lookup(a, key_), m) for key_, m in self.tight(toks, j + 1)]
        self.memo[key] = (toks, res)
        return res

    def primaries(self, toks, i):
        key = ("p", id(toks), i)
        hit = self.memo.get(key)
        if hit is not None and hit[0] is toks:
            return hit[1]
        res = []
        todo = list(self.tight(toks, i))
        while todo:
            a, j = todo.pop(0)
            res.append((a, j))
            todo += self.postfixes(a, toks, j)
        self.memo[key] = (toks, res)
        return res

    def match(self, pattern, toks, i, args=None, tight_last=True):
        args = args or []
        if not pattern:
            return [(args, i)]
        el = pattern[0]
        if el[0] == "lit":
            if i < len(toks) and lit_match(el, toks[i]):
                return self.match(pattern[1:], toks, i + 1, args, tight_last)
            return []
        cands = self.tight(toks, i) if len(pattern) == 1 and tight_last else self.exprs(toks, i)
        res = []
        for e, j in cands:
            res += self.match(pattern[1:], toks, j, args + [e], tight_last)
        return res

    def seqs(self, toks, i):
        """All operand/operator chains starting at i: (operands, operators, end)."""
        res = []
        for a, j in self.primaries(toks, i):
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
        left to right ("to the power of" reads right to left). Spoken English has no
        precedence between "plus" and "times", so "1 plus 2 times 3" keeps both
        bracketings and becomes an ambiguity error.
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
                right_assoc = op[1] == 3
                for l, lt in build(i, k):
                    if lt and constrained(lt, op) and (lt[0] <= op[1] if right_assoc else lt[0] < op[1]):
                        continue
                    for r, rt in build(k + 1, j):
                        if rt and constrained(rt, op) and (rt[0] < op[1] if right_assoc else rt[0] <= op[1]):
                            continue
                        res.append((Bin(op[0], l, r), (op[1], op[2])))
            memo[(i, j)] = res
            return res

        return [t for t, _ in build(0, len(operands) - 1)]

    def exprs(self, toks, i):
        key = ("e", id(toks), i)
        hit = self.memo.get(key)
        if hit is not None and hit[0] is toks:
            return hit[1]
        res = [(t, j) for operands, ops, j in self.seqs(toks, i) for t in self.trees(operands, ops)]
        self.memo[key] = (toks, res)
        return res

    def expr_full(self, toks):
        return [e for e, j in self.exprs(toks, 0) if j == len(toks)]

    def choose(self, cands, what, toks, render=english, key=str):
        cands = unique(cands, key)
        if not cands:
            self.err(f'I couldn\'t understand {what}: "{show(toks)}".')
        if len(cands) > 1:
            raise AmbiguityError(ambiguity_text([render(c) for c in cands]), self.line, self.file)
        return cands[0]

    def one_expr(self, toks, what="this"):
        return self.choose(self.expr_full(toks), what, toks)

    # ── conditions ──

    def conds(self, toks):
        return unique(self.cond_or(toks))

    def cond_or(self, toks):
        res = list(self.cond_and(toks))
        for k in top_positions(toks, ("OR",)):
            for l in self.cond_and(toks[:k]):
                for r in self.cond_or(toks[k + 1:]):
                    res.append(Logic("or", [l] + (r.args if isinstance(r, Logic) and r.op == "or" else [r])))
        return res

    def cond_and(self, toks):
        res = list(self.cond_atom(toks))
        for k in top_positions(toks, ("AND",)):
            for l in self.cond_atom(toks[:k]):
                for r in self.cond_and(toks[k + 1:]):
                    res.append(Logic("and", [l] + (r.args if isinstance(r, Logic) and r.op == "and" else [r])))
        return res

    def cond_atom(self, toks):
        if not toks:
            return []
        if toks[0].kind == "NOT":
            return [Logic("not", [c]) for c in self.cond_atom(toks[1:])]
        res = []
        last = toks[-1].kind
        if last in ("KNOWN", "UNKNOWN"):
            for e in self.expr_full(toks[:-1]):
                if isinstance(e, Lookup):
                    known = Builtin("known", [e.table, e.key])
                    res.append(known if last == "KNOWN" else Logic("not", [known]))
        if last in ("ISNUMBER", "ISNOTNUMBER"):
            for e in self.expr_full(toks[:-1]):
                test = Builtin("is_number", [e])
                res.append(test if last == "ISNUMBER" else Logic("not", [test]))
        if last == "EXISTS" and is_word(toks[0], "file"):
            res += [Builtin("file_exists", [p]) for p in self.expr_full(toks[1:-1])]
        cmps = top_positions(toks, CMPOPS)
        for k in cmps:
            res += [Cmp(CMPOPS[toks[k].kind], l, r)
                    for l in self.expr_full(toks[:k]) for r in self.expr_full(toks[k + 1:])]
        if not cmps and last not in ("KNOWN", "UNKNOWN", "ISNUMBER", "ISNOTNUMBER", "EXISTS"):
            res += self.expr_full(toks)    # a yes/no value on its own: "if ready"
        return res

    def one_cond(self, toks):
        cands = self.conds(toks)
        if not cands:
            self.err(f'I couldn\'t understand the condition "{show(toks)}". '
                     'Try something like: if the score is more than 50.')
        if len(cands) > 1:
            raise AmbiguityError(ambiguity_text([english(c) for c in cands]), self.line, self.file)
        return cands[0]

    def message_cond(self, toks):
        """What a "when someone sends ..." rule waits for."""
        kinds = [t.kind for t in toks]
        if kinds in (["ANYTHING"], ["MSG"]):
            return Truth()
        if kinds == ["WORD"] and toks[0].value == "number":
            return IsNum()
        if toks and toks[0].kind in ("ANYTHING", "MSG"):
            toks = toks[1:]
        if toks and toks[0].kind in ("CONTAINS", "STARTS"):
            op = CMPOPS[toks[0].kind]
            texts, j = self.item_list(toks, 1)
            if j == len(toks) and texts and all(isinstance(x, Text) for x in texts):
                tests = [Cmp(op, Msg(), x) for x in texts]
                return tests[0] if len(tests) == 1 else Logic("or", tests)
        elif toks and toks[0].kind == "STR":
            texts, j = self.item_list(toks, 0)
            if j == len(toks) and all(isinstance(x, Text) for x in texts):
                tests = [Cmp("~=", Msg(), x) for x in texts]
                return tests[0] if len(tests) == 1 else Logic("or", tests)
        self.err('I don\'t understand which messages this rule is for. For example: '
                 'when someone sends "/start", when someone sends a number, '
                 'when someone sends a message that contains "price", when someone sends anything else.')

    # ── values and targets ──

    def print_args(self, toks, i):
        res = []
        for e, j in self.exprs(toks, i):
            res.append(([e], j))
            if j < len(toks) and toks[j].kind == "CONCAT":
                res += [([e] + more, k) for more, k in self.print_args(toks, j + 1)]
        return res

    def value_cands(self, toks):
        cands = [parts[0] if len(parts) == 1 else Concat(parts)
                 for parts, j in self.print_args(toks, 0) if j == len(toks)]
        items = split_top(toks, ("COMMA", "AND"))
        if len(items) > 1 and all(items):
            parsed = [unique(self.expr_full(it)) for it in items]
            if all(len(p) == 1 for p in parsed):
                cands.append(ListLit([p[0] for p in parsed]))
        if top_positions(toks, CMPOPS) or toks[-1].kind in ("KNOWN", "UNKNOWN", "ISNUMBER", "ISNOTNUMBER",
                                                           "EXISTS"):
            cands += [c for c in self.conds(toks) if not isinstance(c, (Num, Dec, Text, Var))]
        return cands

    def value(self, toks):
        if not toks:
            self.err("Something is missing after the name: what should it be?")
        return self.choose(self.value_cands(toks), "the value", toks)

    def targets(self, toks):
        """What can be set: a name, item N of a list, or an entry of a table."""
        res = []
        if len(toks) == 1 and toks[0].kind in ("WORD", "IT"):
            t = toks[0]
            if t.kind == "IT":
                if self.last_var is None:
                    self.err('I don\'t know what "it" means here. Nothing has been set yet.')
                t = Tok("WORD", self.last_var, self.make_var(self.last_var).display)
            res.append(("var", t))
        if toks and toks[0].kind == "WORD" and len(toks) > 2 and is_word(toks[1], "of") and \
                toks[0].value not in INDEX_WORDS | {"first", "last", "file"}:
            res += [("key", toks[0], key) for key in self.expr_full(toks[2:])]
        if toks and is_word(toks[0], *INDEX_WORDS, "first", "last"):
            res += [("index", e) for e in self.expr_full(toks) if isinstance(e, Index)]
        return res

    def make_target(self, target, value_stmt):
        """Turn a chosen target into statements; a new table is created on first use."""
        if target[0] == "var":
            return value_stmt(self.define(target[1]))
        if target[0] == "index":
            return value_stmt(target[1])
        tok, key = target[1], target[2]
        if self.scope_of(tok.value) is None:
            # a new table: every name starts out empty, so the first entry just goes in
            return value_stmt(Lookup(self.define(tok), key))
        return value_stmt(Lookup(self.make_var(tok.value), key))

    # ── statements ──

    def sentence(self, toks):
        """Parse one sentence. Returns a statement, a block header, End or ElseMark."""
        self.memo = {}
        if not toks:
            return None
        k = toks[0].kind
        if k == "END" and len(toks) == 1:
            return End()
        if k == "ON_MESSAGE":
            cut = next((i for i, t in enumerate(toks)
                        if t.kind == "COMMA" and not (i + 1 < len(toks) and toks[i + 1].kind == "STR")), None)
            cond = self.message_cond(toks[1:cut])
            if cut is None:
                return self.at(Handler(cond))
            return self.at(Handler(cond, [self.sentence(strip_commas(toks[cut + 1:]))]))
        if k == "OTHERWISE":
            rest = strip_commas(toks[1:])
            return ElseMark(self.sentence(rest) if rest else None)
        if k in ("IF", "WHILE"):
            cut = next(iter(top_positions(toks, ("COMMA", "THEN"))), None)
            node = If if k == "IF" else While
            if cut is None:
                return self.at(node(self.one_cond(toks[1:])))
            cond = self.one_cond(toks[1:cut])
            return self.at(node(cond, [self.sentence(strip_commas(toks[cut + 1:]))]))
        if k == "FOREACH":
            return self.for_each(toks)
        guard = next((i for i, t in enumerate(toks) if t.kind == "IF" and i > 0), None)
        if guard is not None:
            body = self.sentence(strip_commas(toks[:guard]))
            return self.at(If(self.one_cond(toks[guard + 1:]), [body]))
        if k == "REPEAT":
            return self.repeat(toks)
        if k == "KEEP":
            stop = next((i for i, t in enumerate(toks) if t.kind in ("UNTIL", "WHILE")), None)
            if stop is None:
                self.err('Say when to stop, for example: keep doubling x until it is more than 100.')
            body = self.simple(strip_commas(toks[1:stop]))
            cond = self.one_cond(toks[stop + 1:])
            if toks[stop].kind == "UNTIL":
                cond = Logic("not", [cond])
            return self.at(While(cond, [body]))
        if k in ("BREAK", "CONTINUE", "EXIT") and len(toks) == 1:
            return self.at({"BREAK": Break, "CONTINUE": Continue, "EXIT": Exit}[k]())
        if k == "RETURN":
            return self.at(Return(self.value(toks[1:]) if len(toks) > 1 else None))
        return self.simple(toks)

    def repeat(self, toks):
        stop = next((i for i, t in enumerate(toks) if t.kind in ("UNTIL", "WHILE")), None)
        if stop is not None and stop == 1:
            cut = next(iter(top_positions(toks, ("COMMA",))), None)
            cond = self.one_cond(toks[2:cut])
            if toks[1].kind == "UNTIL":
                cond = Logic("not", [cond])
            rest = strip_commas(toks[cut + 1:]) if cut is not None else []
            return self.at(While(cond, [self.sentence(rest)] if rest else []))
        t = next((i for i, t in enumerate(toks) if t.kind == "TIMES"), None)
        if t is None:
            self.err('Say how many times, for example: repeat 3 times, or: do the following until x is 10.')
        count = self.one_expr(toks[1:t], "how many times to repeat")
        rest = strip_commas(toks[t + 1:])
        return self.at(Repeat(count, [self.sentence(rest)] if rest else []))

    def for_each(self, toks):
        cut = next(iter(top_positions(toks, ("COMMA",))), None)
        head = toks[:cut] if cut is not None else toks
        if len(head) < 4 or head[1].kind != "WORD":
            self.err('Write it like: for every guest in the guests, or: for every n from 1 to 10.')
        if is_word(head[2], "in"):
            items = self.one_expr(head[3:], "what to go through")
            node = ForEach(None, items)
        elif head[2].kind == "FROM":
            to = top_positions(head, ("TO",))
            if not to:
                self.err('Write it like: for every n from 1 to 10.')
            start = self.one_expr(head[3:to[0]], "where to start")
            end = self.one_expr(head[to[0] + 1:], "where to stop")
            node = ForRange(None, start, end)
        else:
            self.err('Write it like: for every guest in the guests, or: for every n from 1 to 10.')
        node.var = self.define(head[1])
        self.at(node)
        if cut is not None:
            node.body = [self.sentence(strip_commas(toks[cut + 1:]))]
        return node

    def simple(self, toks):
        k = toks[0].kind
        n = len(toks)
        if k == "PRINT":
            return self.print_stmt(toks)
        if k == "REPLY":
            return self.reply_stmt(toks)
        if k == "SET":
            return self.set_stmt(toks[1:], "TO")
        if k == "ADD":
            return self.add_stmt(toks)
        if k == "SUB" and n >= 4:
            cands = []
            for pos in top_positions(toks, ("FROM",)):
                for target in self.targets(toks[pos + 1:]):
                    for e in self.expr_full(toks[1:pos]):
                        cands.append((target, e))
            target, e = self.choose(cands, "what to subtract from what", toks,
                                    key=lambda c: f"{describe(c[0])}|{c[1]}",
                                    render=lambda c: f"subtract {english(c[1])} from {describe(c[0])}")
            return self.numeric_update(target, "-", e)
        if k in ("INC", "DEC") and n >= 2:
            by = top_positions(toks, ("BY",))
            target_toks = toks[1:by[-1]] if by else toks[1:]
            e = self.one_expr(toks[by[-1] + 1:], "the amount") if by else Num(1)
            target = self.choose(self.targets(target_toks), "what to change", target_toks, key=describe,
                                 render=describe)
            return self.numeric_update(target, "+" if k == "INC" else "-", e)
        if k in ("DOUBLE", "HALVE") and n >= 2:
            target = self.choose(self.targets(toks[1:]), "what to change", toks[1:], key=describe, render=describe)
            return self.numeric_update(target, "*" if k == "DOUBLE" else "/", Num(2))
        if k == "REMOVE":
            return self.remove_stmt(toks)
        if k in ("SORT", "REVERSE"):
            items = self.one_expr(toks[1:], "the list")
            return self.at((Sort if k == "SORT" else Reverse)(items))
        if k == "ASK":
            pos = top_positions(toks, ("ANSWER_AS",))
            if not pos or pos[-1] != n - 2 or toks[-1].kind != "WORD":
                self.err('Write it like: ask "How old are you?" and remember the answer as age.')
            prompt = self.choose([p[0] if len(p) == 1 else Concat(p) for p, j in self.print_args(toks, 1)
                                  if j == pos[-1]], "the question", toks[1:pos[-1]])
            return self.at(Ask(prompt, self.define(toks[-1])))
        if k == "READ":
            if n < 4 or not is_word(toks[-2], "into", "as") or toks[-1].kind != "WORD":
                self.err('Write it like: read the file "notes.txt" into notes.')
            e = self.one_expr(toks[1:-2], "what to read")
            return self.at(Assign(self.define(toks[-1]), e))
        if k == "PERSIST":
            return self.at(Persist(self.one_expr(toks[1:], "the file name")))
        if k == "USE":
            self.err('"use" only works at the start of a line, outside of any group.')
        if k in ("WORD", "IT") and n > 2:
            stmt = self.try_is(toks)
            if stmt is not None:
                return stmt
        calls = []
        if k == "WORD":
            for sig in self.funcs.commands(toks[0]):
                calls += [CallStmt(Call(sig, args)) for args, j in self.match(sig.pattern, toks, 0, None, False)
                          if j == n]
        if calls:
            return self.at(self.choose(calls, "this command", toks, render=english_stmt, key=stmt_key))
        if k == "WORD":
            pool = self.lx.single_forms + sorted(self.funcs.first_words())
            close = difflib.get_close_matches(toks[0].value, pool, n=1, cutoff=0.7)
            hint = f' Did you mean "{close[0]}"?' if close else ""
            self.err(f'I don\'t know the word "{toks[0].text}".{hint}')
        self.err(f'I couldn\'t understand this sentence: "{show(toks)}".')

    def set_stmt(self, toks, sep):
        cands = []
        for pos in top_positions(toks, (sep,)):
            for target in self.targets(toks[:pos]):
                if pos + 1 < len(toks):
                    cands += [(target, v) for v in unique(self.value_cands(toks[pos + 1:]))]
        if not cands:
            if sep == "TO":
                self.err('Write it like: set age to 18.')
            return None
        target, v = self.choose(cands, "this", toks, key=lambda c: f"{describe(c[0])}|{c[1]}",
                                render=lambda c: f"set {describe(c[0])} to {english(c[1])}")
        return self.make_target(target, lambda t: self.at(Assign(t, v)))

    def try_is(self, toks):
        if not top_positions(toks, ("IS",)):
            return None
        return self.set_stmt(toks, "IS")

    def numeric_update(self, target, op, e):
        """increase / decrease / double / halve: names and table entries start at zero."""
        def build(t):
            current = Lookup(t.table, t.key, or_zero=True) if isinstance(t, Lookup) else t
            return self.at(Assign(t, Bin(op, current, e)))
        if target[0] == "var":
            var = self.define(target[1])
            return self.at(Assign(var, Bin(op, self.make_var(var.name), e)))
        return self.make_target(target, build)

    def add_stmt(self, toks):
        n = len(toks)
        cands = []
        for pos in top_positions(toks, ("TO",)):
            values = self.expr_full(toks[1:pos])
            rest = toks[pos + 1:]
            if rest and is_word(rest[0], "file"):
                for p in self.expr_full(rest[1:]):
                    cands += [("file", p, v) for v in values]
            for target in self.targets(rest):
                cands += [("add", target, v) for v in values]
        if not cands:
            self.err('Write it like: add 5 to the score, or: add "Eve" to the guests.')
        kind, a, v = self.choose(cands, "what to add to what", toks,
                                 key=lambda c: f"{c[0]}|{describe(c[1]) if c[0] == 'add' else c[1]}|{c[2]}",
                                 render=lambda c: f"add {english(c[2])} to "
                                                  f"{describe(c[1]) if c[0] == 'add' else 'the file ' + english(c[1])}")
        if kind == "file":
            return self.at(WriteFile(a, [v], True))
        if a[0] == "var":
            create = self.scope_of(a[1].value) is None
            return self.at(AddTo(self.define(a[1]), v, create))
        return self.make_target(a, lambda t: self.at(AddTo(t, v)))

    def remove_stmt(self, toks):
        rest = toks[1:]
        if rest and is_word(rest[0], "file"):
            return self.at(DeleteFile(self.one_expr(rest[1:], "the file name")))
        cands = []
        for pos in top_positions(rest, ("FROM",)):
            what, src = rest[:pos], rest[pos + 1:]
            for items in self.expr_full(src):
                if what and is_word(what[0], *INDEX_WORDS):
                    cands += [RemoveAt(items, i) for i in self.expr_full(what[1:])]
                cands += [RemoveValue(items, v) for v in self.expr_full(what)]
        cands += [RemoveKey(e.table, e.key) for e in self.expr_full(rest) if isinstance(e, Lookup)]
        stmt = self.choose(cands, "what to remove", toks, key=stmt_key, render=stmt_key)
        return self.at(stmt)

    def print_stmt(self, toks):
        n = len(toks)
        to_file = [p for p in top_positions(toks, ("TO",)) if p + 1 < n and is_word(toks[p + 1], "file")]
        if to_file:
            p = to_file[-1]
            parts = self.choose([ps for ps, j in self.print_args(toks, 1) if j == p], "what to write", toks[1:p],
                                key=lambda ps: ", ".join(map(str, ps)))
            return self.at(WriteFile(self.one_expr(toks[p + 2:], "the file name"), parts, False))
        cands = []
        for parts, j in self.print_args(toks, 1):
            if j == n:
                cands.append(self.at(Print(parts)))
            else:
                for cnt, k in self.exprs(toks, j):
                    if k == n - 1 and toks[k].kind == "TIMES":
                        cands.append(self.at(Repeat(cnt, [self.at(Print(parts))])))
        if not cands:
            self.err(f'I couldn\'t understand what to {toks[0].value}: "{show(toks)}". '
                     'Put text with several words in quotes.')
        return self.choose(cands, "this", toks, render=english_stmt, key=stmt_key)

    def reply_stmt(self, toks):
        b = next((i for i, t in enumerate(toks) if t.kind == "BUTTONS"), len(toks))
        buttons = []
        if b < len(toks):
            buttons, j = self.item_list(toks, b + 1)
            if not buttons or j != len(toks):
                self.err('Write buttons like this: reply "Pick one" with buttons "yes", "no" and "maybe".')
        parts = self.choose([ps for ps, j in self.print_args(toks[:b], 1) if j == b], "what to reply", toks[:b],
                            key=lambda ps: ", ".join(map(str, ps)),
                            render=lambda ps: "reply " + " and then ".join(english(p) for p in ps))
        return self.at(Reply(parts, buttons))


# ───────────── blocks and files ─────────────

def open_if(body):
    """The If that a following "otherwise" belongs to, following else-if chains."""
    node = body[-1] if body else None
    while isinstance(node, If) and node.else_ is not None and len(node.else_) == 1 \
            and isinstance(node.else_[0], If):
        node = node.else_[0]
    return node if isinstance(node, If) and node.else_ is None else None


def is_header(node):
    """A statement whose body follows on the next lines."""
    if isinstance(node, If):
        return not node.then
    return isinstance(node, (Repeat, While, ForEach, ForRange, Handler)) and not node.body


def header_frame(node, single_line):
    body = node.then if isinstance(node, If) else node.body
    return ["then" if isinstance(node, If) else "block", body, single_line, node]


def starts_with_word(text, *words):
    first = text.strip().split(None, 1)
    return bool(first) and first[0].lower() in words


def load_sentences(src, file, seen, lx):
    """All sentences of a file, with "use" lines replaced by the used file's sentences."""
    out = []
    for s in split_sentences(src, file):
        if not starts_with_word(s.text, "use"):
            out.append(s)
            continue
        toks = canonical(s.text, s.line, s.file, lx)
        if len(toks) != 2 or toks[0].kind != "USE" or toks[1].kind != "STR":
            raise EngError('Write it like: use "helpers.eng".', s.line, s.file)
        path = os.path.join(os.path.dirname(file), toks[1].value) if file else toks[1].value
        key = os.path.abspath(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            with open(path, encoding="utf-8") as f:
                used = f.read()
        except OSError:
            raise EngError(f'I can\'t find the file "{toks[1].value}".', s.line, s.file)
        out += load_sentences(used, path, seen, lx)
    return out


def parse_program(src, file="", lx=None):
    lx = lx or Lexicon()
    sentences = load_sentences(src, file, {os.path.abspath(file)} if file else set(), lx)
    funcs = Funcs()
    for s in sentences:
        if starts_with_word(s.text, "to"):
            funcs.add(parse_header(s, lx, len(funcs.sigs)))
    p = Parser(lx, funcs)
    root = []
    stack = []  # frames: [kind, list, single_line, node]

    def current():
        return stack[-1][1] if stack else root

    def close_all():
        stack.clear()
        p.exit_function()

    def add(stmt):
        if isinstance(stmt, Seq):
            current().extend(stmt.stmts)
        else:
            current().append(stmt)
        while stack and stack[-1][2] and stack[-1][1]:
            pop()   # a same-line body ("repeat 5 times: say hi") holds one sentence

    def pop():
        frame = stack.pop()
        if frame[0] == "function":
            p.exit_function()

    for item in sentences:
        p.line, p.file = item.line, item.file
        if starts_with_word(item.text, "to"):
            close_all()
            sig = funcs.by_header[(item.file, item.line)]
            root.append(FuncDef(sig))
            p.enter_function(sig)
            _, body = header_split(item.text, item.line, item.file)
            if body:
                p.memo = {}
                node = p.sentence(apply_lexicon(normalize(body, lx), lx))
                sig.body.append(node)
                if is_header(node):
                    stack.append(["function", sig.body, False, None])
                    stack.append(header_frame(node, item.header_colon))
                else:
                    p.exit_function()
            else:
                stack.append(["function", sig.body, item.header_colon, None])
            continue
        toks = canonical(item.text, item.line, item.file, lx)
        if toks and toks[0].kind == "ON_MESSAGE":
            close_all()
        node = p.sentence(toks)
        if node is None:
            continue
        if isinstance(node, End):
            if not stack:
                raise EngError('"That\'s all" closes a group, but no group is open here.', item.line, item.file)
            pop()
            continue
        if isinstance(node, ElseMark):
            prev = open_if(current())
            if prev is None and stack and stack[-1][0] == "then":
                pop()   # "otherwise" ends the block of a multi-line "if"
                prev = open_if(current())
            if prev is None:
                raise EngError('"Otherwise" needs an "if" sentence right before it.', item.line, item.file)
            prev.else_ = []
            if node.stmt is None:
                stack.append(["else", prev.else_, item.header_colon, prev])
            else:
                prev.else_.append(node.stmt)
                if is_header(node.stmt):     # "otherwise, if X" with a block below
                    stack.append(header_frame(node.stmt, item.header_colon))
            continue
        if isinstance(node, Persist) and stack:
            raise EngError('"Remember everything in" must stand on its own, outside of any group.',
                           item.line, item.file)
        add(node)
        target = node.stmts[-1] if isinstance(node, Seq) else node
        if is_header(target):
            stack.append(header_frame(target, item.header_colon))
    close_all()
    return root, funcs
