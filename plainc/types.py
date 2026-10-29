"""Type inference: type variables, unification and a few deferred decisions.

Nobody writes types in Plain. Every value gets a type variable; sentences add
equations between them. Some decisions wait until more is known ("add X to Y"
depends on whether Y is a number, a list or a text), so they are kept as
deferred constraints and solved in rounds.

Numbers: a name that only ever holds whole numbers is Int; if it ever holds a
decimal it is Float, and whole numbers stored in it are converted. That is the
only automatic conversion.
"""

from .core import (AddTo, Ask, Assign, Bin, Break, Builtin, Call, CallStmt, Cmp, Concat, Continue, Dec, DeleteFile,
                   EmptyMap, Exit, ForEach, ForRange, FuncDef, Handler, If, Index, IsNum, ListLit, Logic, Lookup,
                   Msg, MsgNum, Name, Num, OneOf, Persist, Print, Random, RandomItem, RemoveAt, RemoveKey,
                   RemoveValue, Repeat, Reply, Return, Reverse, Slice, Sort, Text, Truth, Var, While, WriteFile,
                   english, New, Field, Nothing, FuncSig, clone_body)
from .errors import EngError


class TVar:
    def __init__(self):
        self.ref = None
        self.cands = set()    # number kinds that flowed in before the type was known


class Con:
    def __init__(self, name, *args):
        self.name = name
        self.args = list(args)

    def __repr__(self):
        return self.name + (f"[{', '.join(map(repr, self.args))}]" if self.args else "")


INT, FLOAT, TEXT, BOOL, NOTHING = Con("Int"), Con("Float"), Con("Text"), Con("Bool"), Con("Nothing")
NUMERIC = ("Int", "Float")


def ListT(t):
    return Con("List", t)


def MapT(k, v):
    return Con("Map", k, v)


def prune(t):
    while isinstance(t, TVar) and t.ref is not None:
        t = t.ref
    return t


def deep(t):
    """A fully known type: Con with known arguments all the way down."""
    t = prune(t)
    if isinstance(t, TVar):
        return INT
    return Con(t.name, *[deep(a) for a in t.args]) if t.args else t


def con(t):
    t = prune(t)
    return t if isinstance(t, Con) else None


def is_num(t):
    t = con(t)
    return t is not None and t.name in NUMERIC


def words(t, plural=False):
    t = prune(t)
    if isinstance(t, TVar):
        if t.cands:
            return words(FLOAT if "Float" in t.cands else INT, plural)
        return "things" if plural else "something"
    if t.name == "Int":
        return "whole numbers" if plural else "a whole number"
    if t.name == "Float":
        return "decimal numbers" if plural else "a decimal number"
    if t.name == "Text":
        return "texts" if plural else "text"
    if t.name == "Bool":
        return "yes/no values" if plural else "a yes/no value"
    if t.name == "Nothing":
        return "nothing"
    if t.name.startswith("Kind:"):
        return f"{t.name[5:]}s" if plural else f"a {t.name[5:]}"
    if t.name == "List":
        return "lists" if plural else f"a list of {words(t.args[0], True)}"
    return "tables" if plural else f"a table from {words(t.args[0], True)} to {words(t.args[1], True)}"


KIND_IDS = {}   # kind name -> number, set by check()


def kind_con(kind):
    return Con("Kind:" + kind.name)


def is_kind(t):
    t = con(t)
    return t is not None and t.name.startswith("Kind:")


def desc(t):
    """Short type code the runtime uses for printing and saving:
    i f s b, L<item>, M<key><value>, K<kind number>. (a thing of a declared kind)."""
    t = prune(t)
    if t.name.startswith("Kind:"):
        return f"K{KIND_IDS[t.name[5:]]}."
    return {"Int": "i", "Float": "f", "Text": "s", "Bool": "b"}.get(t.name) or \
        ("L" + desc(t.args[0]) if t.name == "List" else "M" + desc(t.args[0]) + desc(t.args[1]))


def has_tvar(t):
    t = prune(t)
    return isinstance(t, TVar) or any(has_tvar(a) for a in t.args)


def occurs(v, t):
    t = prune(t)
    if t is v:
        return True
    return isinstance(t, Con) and any(occurs(v, a) for a in t.args)


class Clash(Exception):
    pass


def unify(a, b):
    a, b = prune(a), prune(b)
    if a is b:
        return
    if isinstance(b, TVar) and not isinstance(a, TVar):
        a, b = b, a
    if isinstance(a, TVar):
        if occurs(a, b):
            raise Clash()
        if isinstance(b, TVar):
            b.cands |= a.cands
        elif a.cands and (b.name not in NUMERIC or ("Float" in a.cands and b.name == "Int")):
            raise Clash()
        a.ref = b
        return
    if a.name != b.name or len(a.args) != len(b.args):
        raise Clash()
    for x, y in zip(a.args, b.args):
        unify(x, y)


class Constraint:
    def __init__(self, step, line, file, weak=False):
        self.step = step      # step(final) -> True when solved
        self.line = line
        self.file = file
        self.weak = weak      # its fallback is only a guess: use it after every other decision


class Checker:
    def __init__(self, funcs, generic=()):
        self.funcs = funcs
        self.tvars = []
        self.vars = {}            # (scope, func, name) -> type
        self.kinds = getattr(funcs, "kinds", None)
        kinds = self.kinds.by_name.values() if self.kinds else []
        self.kind_fields = {k.name: {f: self.new() for f in k.fields} for k in kinds}
        self.params, self.ret = {}, {}
        for sig in funcs.sigs:
            self.signature(sig)
        self.generic = set(generic)   # functions checked again for every use, with that use's types
        self.active = {}              # generic function -> the copy being checked (for recursion)
        self.instances = []
        self.deferred = []
        self.line, self.file = 0, ""
        self.func = None
        self.in_handler = False
        self.loops = 0
        self.direct_bot = set()   # functions that use bot words themselves
        self.calls = []           # (caller sig name or None, callee sig, in_handler, line, file)
        self.persist = None
        self.handlers = False

    def signature(self, sig):
        self.params[sig.name] = [self.new() for _ in sig.params]
        self.ret[sig.name] = self.new() if sig.returns else NOTHING
        for name, t in zip(sig.params, self.params[sig.name]):
            if name in sig.param_kinds:
                unify(t, kind_con(sig.param_kinds[name]))

    # ── helpers ──

    def new(self):
        v = TVar()
        self.tvars.append(v)
        return v

    def fail(self, msg, line=None, file=None):
        raise EngError(msg, line or self.line, file or self.file)

    def eq(self, a, b, msg):
        try:
            unify(a, b)
        except Clash:
            self.fail(msg() if callable(msg) else msg)

    def defer(self, step, weak=False):
        self.deferred.append(Constraint(step, self.line, self.file, weak))

    def var_type(self, v):
        key = (v.scope, v.func, v.name)
        if key not in self.vars:
            self.vars[key] = self.new()
        return self.vars[key]

    def flow(self, src, dst, what):
        """A value of type src is stored where dst is expected. Int may go into Float."""
        def step(final):
            s, d = prune(src), prune(dst)
            if isinstance(s, Con) and isinstance(d, Con):
                if not (s.name == "Int" and d.name == "Float"):
                    self.eq(s, d, lambda: what(s, d))
                return True
            if isinstance(s, Con):
                if s.name in NUMERIC:
                    d.cands.add(s.name)
                else:
                    self.eq(d, s, lambda: what(s, d))
                return True
            if isinstance(d, Con):
                if d.name == "Float" and not final:
                    return False
                if d.name != "Float":
                    self.eq(s, d, lambda: what(s, d))
                return True
            if final:
                self.eq(s, d, lambda: what(s, d))
                return True
            return False
        if not step(False):
            self.defer(step)

    def numeric(self, t, what):
        def step(final):
            c = con(t)
            if c is None:
                if final:
                    self.eq(t, INT, what)
                    return True
                return False
            if c.name not in NUMERIC:
                self.fail(what() if callable(what) else what)
            return True
        if not step(False):
            self.defer(step)

    def when_known(self, t, decide, default=None, weak=False):
        """Run decide(con) once t is known; at the end use default() if it never becomes known."""
        def step(final):
            c = con(t)
            if c is not None:
                decide(c)
                return True
            if final:
                if default is None:
                    return False
                default()
                return True
            return False
        if not step(False):
            self.defer(step, weak)

    def bot_word(self, what):
        if self.func is not None:
            self.direct_bot.add(self.func.name)
        elif not self.in_handler:
            self.fail(f'"{what}" only exists inside a "when someone sends ..." rule.')

    # ── expressions ──

    def expr(self, e):
        e.ty = self.expr_type(e)
        return e.ty

    def expr_type(self, e):
        if isinstance(e, Num):
            return INT
        if isinstance(e, Dec):
            return FLOAT
        if isinstance(e, Text):
            return TEXT
        if isinstance(e, Truth):
            return BOOL
        if isinstance(e, Var):
            if e.scope == "user":
                if self.func is not None:
                    self.direct_bot.add(self.func.name)
                elif not self.in_handler:
                    self.fail(f'"{e.display}" only exists inside a "when someone sends ..." rule.')
            return self.var_type(e)
        if isinstance(e, (Msg, Name)):
            self.bot_word(english(e))
            return TEXT
        if isinstance(e, MsgNum):
            self.bot_word(english(e))
            return INT
        if isinstance(e, IsNum):
            return BOOL
        if isinstance(e, Random):
            for x in (e.low, e.high):
                self.eq(self.expr(x), INT, "A random number needs whole numbers on both ends.")
            return INT
        if isinstance(e, OneOf):
            r = self.new()
            for x in e.items:
                self.flow(self.expr(x), r, lambda s, d: f'All choices in "{english(e)}" must be the same kind.')
            return r
        if isinstance(e, RandomItem):
            r = self.new()
            self.eq(self.expr(e.items), ListT(r), lambda: f'"{english(e.items)}" is not a list to pick from.')
            return r
        if isinstance(e, Concat):
            for x in e.parts:
                self.expr(x)
            return TEXT
        if isinstance(e, ListLit):
            el = self.new()
            for x in e.items:
                self.flow(self.expr(x), el, lambda s, d: f'All items of a list must be the same kind, but '
                          f'"{english(e)}" mixes {words(s, True)} and {words(d, True)}.')
            return ListT(el)
        if isinstance(e, EmptyMap):
            k, v = self.new(), self.new()
            self.when_known(k, lambda c: c.name in ("Int", "Text") or
                            self.fail("A table can only be looked up by text or whole numbers."))
            return MapT(k, v)
        if isinstance(e, Bin):
            l, r = self.expr(e.left), self.expr(e.right)
            res = self.new()
            self.arith(e, l, r, res)
            return res
        if isinstance(e, Cmp):
            self.compare(e)
            return BOOL
        if isinstance(e, Logic):
            for a in e.args:
                self.eq(self.expr(a), BOOL, lambda: f'"{english(a)}" is not a yes/no question.')
            return BOOL
        if isinstance(e, Index):
            return self.index(e, target=False)
        if isinstance(e, Slice):
            c = self.expr(e.container)
            for x in (e.start, e.end):
                self.eq(self.expr(x), INT, "Item positions must be whole numbers.")
            r = self.new()
            self.when_known(c, lambda k: self.eq(r, k, "") if k.name in ("List", "Text") else
                            self.fail(f'I can only take items of a list or letters of a text, and '
                                      f'"{english(e.container)}" is {words(k)}.'))
            return r
        if isinstance(e, Lookup):
            k, v = self.new(), self.new()
            self.eq(self.expr(e.table), MapT(k, v),
                    lambda: f'"{english(e.table)}" is {words(e.table.ty)}, not a table, so "{english(e)}" '
                            f'does not work.')
            self.eq(self.expr(e.key), k, lambda: f'The table "{english(e.table)}" is looked up by '
                                                 f'{words(k, True)}, not by {words(e.key.ty, True)}.')
            return v
        if isinstance(e, Call):
            return self.call(e)
        if isinstance(e, Builtin):
            return self.builtin(e)
        if isinstance(e, New):
            fields = self.kind_fields[e.kind.name]
            for key, x in e.inits:
                self.flow(self.expr(x), fields[key],
                          lambda s, d, key=key: f'The {e.kind.field_display[key]} of a {e.kind.name} holds '
                                                f'{words(d)}, so it can\'t be {words(s)}.')
            return kind_con(e.kind)
        if isinstance(e, Field):
            return self.field(e)
        if isinstance(e, Nothing):
            t = self.new()
            self.when_known(t, lambda c: c.name.startswith("Kind:") or self.fail(
                f'Only things of a kind you described (like "a book has a title") can be nothing, not {words(c)}.'))
            return t
        self.fail(f"internal: unknown expression {e}")

    def field(self, e):
        o, r = self.expr(e.obj), self.new()

        def decide(c):
            if not c.name.startswith("Kind:"):
                self.fail(f'"{english(e.obj)}" is {words(c)}, so it has no {e.display}.')
            kname = c.name[5:]
            fields = self.kind_fields[kname]
            if e.field not in fields:
                self.fail(f'A {kname} has no {e.display}. It has: '
                          f'{", ".join(self.kinds.by_name[kname].field_display[f] for f in fields)}.')
            e.kind = self.kinds.by_name[kname]
            self.eq(r, fields[e.field], lambda: f'The {e.display} of a {kname} is {words(fields[e.field])}, '
                                                f'not {words(r)}.')
        self.when_known(o, decide)
        return r

    def arith(self, e, l, r, res):
        what = lambda: (f'I can only do arithmetic with numbers, but "{english(e)}" uses {words(l)} '
                        f'and {words(r)}.' + (' To glue texts together use "and then".' if
                                              con(l) is TEXT or con(r) is TEXT else ""))

        def step(final):
            a, b = con(l), con(r)
            for c in (a, b):
                if c is not None and c.name not in NUMERIC:
                    self.fail(what())
            if a is not None and b is not None:
                both_int = a.name == "Int" and b.name == "Int"
                self.eq(res, INT if both_int else FLOAT, what)
                return True
            if not final:
                return False
            known = a or b
            for t in (l, r):
                if con(t) is None:
                    self.eq(t, known or INT, what)
            return step(False)
        if not step(False):
            self.defer(step)

    def compare(self, e):
        l, r = self.expr(e.left), self.expr(e.right)
        op = e.op
        bad = lambda: f'I can\'t compare {words(l)} with {words(r)} in "{english(e)}".'
        if op in ("starts", "~="):
            self.eq(l, TEXT, f'Only text can start with something, in "{english(e)}".')
            self.eq(r, TEXT, bad)
            return
        if op == "contains":
            def decide(c):
                if c.name == "Text":
                    e.kind = "text"
                    self.eq(r, TEXT, bad)
                elif c.name == "List":
                    e.kind = "list"
                    self.same_kind(r, c.args[0], bad)
                elif c.name == "Map":
                    e.kind = "map"
                    self.eq(r, c.args[0], bad)
                else:
                    self.fail(f'"{english(e.left)}" is {words(c)}; only texts, lists and tables can contain '
                              f'something.')
            self.when_known(l, decide, lambda: (self.eq(l, TEXT, bad), decide(TEXT)))
            return
        if op in (">", "<", ">=", "<="):
            def check(c):
                if c.name not in NUMERIC + ("Text",):
                    self.fail(f'Only numbers and texts can be bigger or smaller, in "{english(e)}".')
            self.when_known(l, check)
            self.same_kind(l, r, bad)
            return
        self.same_kind(l, r, bad)

    def same_kind(self, a, b, msg):
        """Equal types, except that whole and decimal numbers may be compared."""
        def step(final):
            x, y = con(a), con(b)
            if x is not None and y is not None:
                if not (x.name in NUMERIC and y.name in NUMERIC):
                    self.eq(x, y, msg)
                return True
            if x is not None and x.name not in NUMERIC:
                self.eq(b, x, msg)
                return True
            if y is not None and y.name not in NUMERIC:
                self.eq(a, y, msg)
                return True
            if final:
                self.eq(a, b, msg)
                return True
            return False
        if not step(False):
            self.defer(step)

    def index(self, e, target):
        c = self.expr(e.container)
        if e.index is not None:
            self.eq(self.expr(e.index), INT, "Item positions must be whole numbers.")
        r = self.new()

        def decide(k):
            if k.name == "List":
                e.kind = "list"
                self.eq(r, k.args[0], "")
            elif k.name == "Text":
                if target:
                    self.fail("Texts can't be changed letter by letter. Build a new text with \"and then\" instead.")
                e.kind = "text"
                self.eq(r, TEXT, "")
            else:
                self.fail(f'I can only take items of a list or letters of a text, and "{english(e.container)}" '
                          f'is {words(k)}.')
        self.when_known(c, decide)
        return r

    def call(self, e):
        sig = e.func
        if sig.name in self.generic:
            sig = e.func = self.active.get(sig.name) or self.instantiate(sig)
        for a, p in zip(e.args, self.params[sig.name]):
            self.flow(self.expr(a), p, lambda s, d: f'"{sig.display}" got {words(s)} for a value that is '
                                                    f'{words(d)} elsewhere.')
        self.calls.append((self.func.name if self.func else None, sig, self.in_handler, self.line, self.file))
        return self.ret[sig.name]

    def instantiate(self, sig):
        """A copy of a generic function for one place where it is used."""
        inst = FuncSig(f"{sig.name}_v{len(self.instances)}", sig.display, sig.pattern, sig.params, sig.returns,
                       sig.line, sig.file, clone_body(sig.body, sig.name, f"{sig.name}_v{len(self.instances)}"),
                       param_kinds=sig.param_kinds, origin=sig)
        self.signature(inst)
        self.instances.append(inst)
        saved = self.func, self.loops, self.line, self.file, self.in_handler
        self.active[sig.name] = inst
        self.in_handler = False
        self.function(inst)
        del self.active[sig.name]
        self.func, self.loops, self.line, self.file, self.in_handler = saved
        return inst

    def builtin(self, e):
        n, args = e.name, e.args
        ts = [self.expr(a) for a in args]
        a0 = ts[0] if ts else None
        sub = english(args[0], False) if args else ""
        num_msg = lambda: f'"{english(e)}" needs a number, but "{sub}" is {words(a0)}.'
        if n == "length":
            def decide(c):
                if c.name not in ("Text", "List", "Map"):
                    self.fail(f'Only texts, lists and tables have a length, and "{sub}" is {words(c)}.')
            self.when_known(a0, decide)
            return INT
        if n in ("sum", "average", "largest", "smallest"):
            el = self.new()
            self.eq(a0, ListT(el), lambda: f'"{english(e)}" needs a list, but "{sub}" is {words(a0)}.')
            if n in ("sum", "average"):
                self.numeric(el, lambda: f'I can only add up numbers, and "{sub}" holds {words(el, True)}.')
            return FLOAT if n == "average" else el
        if n == "sqrt":
            self.numeric(a0, num_msg)
            return FLOAT
        if n in ("abs", "squared"):
            self.numeric(a0, num_msg)
            return a0
        if n in ("round", "round_up", "round_down"):
            self.numeric(a0, num_msg)
            return INT
        if n in ("upper", "lower", "trim"):
            self.eq(a0, TEXT, lambda: f'"{english(e)}" needs text, but "{sub}" is {words(a0)}.')
            return TEXT
        if n in ("words", "lines", "letters", "split"):
            for t in ts:
                self.eq(t, TEXT, lambda: f'"{english(e)}" needs text, but "{sub}" is {words(a0)}.')
            return ListT(TEXT)
        if n == "join":
            self.eq(a0, ListT(self.new()), lambda: f'Only lists can be joined, and "{sub}" is {words(a0)}.')
            self.eq(ts[1], TEXT, "What goes between the items must be text.")
            return TEXT
        if n == "replace":
            for t in ts:
                self.eq(t, TEXT, lambda: f'"{english(e)}" works with texts only.')
            return TEXT
        if n in ("to_int", "to_float"):
            def decide(c):
                if c.name not in ("Text", "Int", "Float"):
                    self.fail(f'{words(c).capitalize()} can\'t be turned into a number.')
            self.when_known(a0, decide)
            return INT if n == "to_int" else FLOAT
        if n == "to_text":
            return TEXT
        if n == "is_number":
            self.eq(a0, TEXT, lambda: f'Only text can be checked for being a number, and "{sub}" is {words(a0)}.')
            return BOOL
        if n in ("file_text", "file_exists"):
            self.eq(a0, TEXT, "A file name must be text in quotes.")
            return TEXT if n == "file_text" else BOOL
        if n == "arguments":
            return ListT(TEXT)
        if n == "known":
            self.eq(a0, MapT(ts[1], self.new()), lambda: f'"{sub}" is not a table.')
            return BOOL
        self.fail(f"internal: unknown builtin {n}")

    # ── statements ──

    def target_type(self, t):
        if isinstance(t, Var):
            t.ty = self.expr(t)
            return t.ty
        if isinstance(t, Index):
            t.ty = self.index(t, target=True)
            return t.ty
        return self.expr(t)

    def stored(self, s, d):
        return f'{words(s).capitalize()} can\'t be stored where {words(d)} is kept.'

    def block(self, body):
        for s in body:
            self.line, self.file = getattr(s, "line", self.line), getattr(s, "file", self.file)
            self.stmt(s)

    def stmt(self, s):
        if isinstance(s, Assign):
            tt = self.target_type(s.target)
            vt = self.expr(s.expr)
            self.flow(vt, tt, lambda a, b: f'"{english(s.target)}" holds {words(b)}, so it can\'t become '
                                           f'{words(a)}.' + (' Write the first value as a decimal, for example 0.0.'
                                                             if con(a) is FLOAT and con(b) is INT else ""))
        elif isinstance(s, AddTo):
            self.add_to(s)
        elif isinstance(s, (Print, WriteFile)):
            if isinstance(s, WriteFile):
                self.eq(self.expr(s.path), TEXT, "A file name must be text in quotes.")
            for p in s.parts:
                self.expr(p)
        elif isinstance(s, Reply):
            self.bot_word("reply")
            for p in s.parts:
                self.expr(p)
            for b in s.buttons:
                self.eq(self.expr(b), TEXT, "Button labels must be text in quotes.")
        elif isinstance(s, Repeat):
            self.eq(self.expr(s.count), INT, "The number of times must be a whole number.")
            self.loop(s.body)
        elif isinstance(s, While):
            self.eq(self.expr(s.cond), BOOL, lambda: f'"{english(s.cond)}" is not a yes/no question.')
            self.loop(s.body)
        elif isinstance(s, ForEach):
            items, var = self.expr(s.items), self.target_type(s.var)

            def decide(c):
                if c.name == "List":
                    s.kind = "list"
                    self.eq(var, c.args[0], lambda: f'"{s.var.display}" already holds {words(var)}.')
                elif c.name == "Text":
                    s.kind = "text"
                    self.eq(var, TEXT, lambda: f'"{s.var.display}" already holds {words(var)}.')
                elif c.name == "Map":
                    s.kind = "map"
                    self.eq(var, c.args[0], lambda: f'"{s.var.display}" already holds {words(var)}.')
                else:
                    self.fail(f'I can only go through a list, a text or a table, and "{english(s.items)}" is '
                              f'{words(c)}.')
            self.when_known(items, decide)
            self.loop(s.body)
        elif isinstance(s, ForRange):
            self.eq(self.target_type(s.var), INT, lambda: f'"{s.var.display}" already holds something else.')
            for x in (s.start, s.end):
                self.eq(self.expr(x), INT, "Counting goes from one whole number to another.")
            self.loop(s.body)
        elif isinstance(s, If):
            self.eq(self.expr(s.cond), BOOL, lambda: f'"{english(s.cond)}" is not a yes/no question.')
            self.block(s.then)
            self.block(s.else_ or [])
        elif isinstance(s, (Break, Continue)):
            if not self.loops:
                self.fail(f'"{"stop the loop" if isinstance(s, Break) else "skip to the next one"}" only works '
                          f'inside a repeat or "for every".')
        elif isinstance(s, Exit):
            pass
        elif isinstance(s, Return):
            self.give_back(s)
        elif isinstance(s, RemoveValue):
            items, v = self.expr(s.items), self.expr(s.value)
            el = self.new()
            self.eq(items, ListT(el), lambda: f'I can only remove things from a list, and "{english(s.items)}" is '
                                              f'{words(items)}.')
            self.same_kind(v, el, lambda: f'"{english(s.items)}" holds {words(el, True)}, not {words(v, True)}.')
        elif isinstance(s, RemoveAt):
            items = self.expr(s.items)
            self.eq(items, ListT(self.new()), lambda: f'"{english(s.items)}" is not a list.')
            self.eq(self.expr(s.index), INT, "Item positions must be whole numbers.")
        elif isinstance(s, RemoveKey):
            self.eq(self.expr(s.table), MapT(self.expr(s.key), self.new()), lambda: f'"{english(s.table)}" is '
                                                                                     f'not a table.')
        elif isinstance(s, DeleteFile):
            self.eq(self.expr(s.path), TEXT, "A file name must be text in quotes.")
        elif isinstance(s, (Sort, Reverse)):
            el = self.new()
            items = self.expr(s.items)
            self.eq(items, ListT(el), lambda: f'Only lists can be sorted or reversed, and "{english(s.items)}" '
                                              f'is {words(items)}.')
            if isinstance(s, Sort):
                self.when_known(el, lambda c: c.name in NUMERIC + ("Text", "Bool") or self.fail(
                    f'I can\'t sort {words(c, True)}.'))
        elif isinstance(s, Ask):
            if self.in_handler:
                self.fail('"ask" waits for someone typing in a terminal, so it can\'t be used in a bot.')
            self.expr(s.prompt)
            t = self.target_type(s.target)
            self.when_known(t, lambda c: c.name in ("Text", "Int", "Float") or self.fail(
                f'An answer can be text or a number, not {words(c)}.'), lambda: self.eq(t, TEXT, ""), weak=True)
        elif isinstance(s, CallStmt):
            self.expr(s.call)
        elif isinstance(s, Persist):
            self.eq(self.expr(s.path), TEXT, "A file name must be text in quotes.")
            self.persist = s
        elif isinstance(s, Handler):
            self.handlers = True
            self.in_handler = True
            self.eq(self.expr(s.cond), BOOL, "")
            self.block(s.body)
            self.in_handler = False
        elif isinstance(s, FuncDef):
            if s.sig.name not in self.generic:
                self.function(s.sig)
        else:
            self.fail(f"internal: unknown statement {s}")

    def loop(self, body):
        self.loops += 1
        self.block(body)
        self.loops -= 1

    def add_to(self, s):
        tt, vt = self.target_type(s.target), self.expr(s.expr)
        msg = lambda a, b: f'I can\'t add {words(a)} to "{english(s.target)}", which is {words(b)}.'

        def as_kind(kind):
            s.kind = kind
            if kind == "num":
                self.numeric(vt, lambda: msg(vt, tt))
                self.flow(vt, tt, msg)
            elif kind == "list":
                el = self.new()
                self.eq(tt, ListT(el), lambda: msg(vt, tt))
                self.flow(vt, el, lambda a, b: f'"{english(s.target)}" is a list of {words(b, True)}, so '
                                               f'{words(a)} can\'t go into it.')
            else:
                self.eq(vt, TEXT, lambda: msg(vt, tt))

        def step(final):
            t = prune(tt)
            if isinstance(t, Con):
                if t.name in NUMERIC:
                    as_kind("num")
                elif t.name == "List":
                    as_kind("list")
                elif t.name == "Text":
                    as_kind("text")
                else:
                    self.fail(msg(vt, tt))
                return True
            if t.cands:
                as_kind("num")
                return True
            v = con(vt)
            if v is not None and (v.name in NUMERIC or s.create or final):
                as_kind("num" if v.name in NUMERIC else "list")
                return True
            if final:
                as_kind("num")
                return True
            return False
        if not step(False):
            self.defer(step)

    def give_back(self, s):
        if self.func is None:
            self.fail('"give back" only works inside a definition that starts with "to find".')
        if not self.func.returns:
            if s.expr is not None:
                self.fail(f'"{self.func.display}" is a command, so it can\'t give back a value. '
                          f'Start the definition with "to find" to make it give back a value.')
            return
        if s.expr is None:
            self.fail(f'"{self.func.display}" has to give back a value, for example: give back the total.')
        self.flow(self.expr(s.expr), self.ret[self.func.name],
                  lambda a, b: f'"{self.func.display}" gives back {words(a)} here and {words(b)} elsewhere.')

    def function(self, sig):
        self.func = sig
        for name, t in zip(sig.params, self.params[sig.name]):
            self.vars[("local", sig.name, name)] = t
        saved = self.loops
        self.loops = 0
        self.block(sig.body)
        self.loops = saved
        result = self.vars.get(("local", sig.name, "result"))
        if sig.returns:
            gives = any(isinstance(x, Return) and x.expr is not None for x in walk(sig.body))
            if result is not None:
                self.flow(result, self.ret[sig.name],
                          lambda a, b: f'The result of "{sig.display}" is {words(a)} but it gives back {words(b)}.')
            elif not gives:
                self.fail(f'"{sig.display}" never gives back a value. Add "give back ..." or '
                          f'"the result is ...".', sig.line, sig.file)
        self.func = None

    # ── solving ──

    def solve(self):
        while True:
            progress = True
            while progress:
                progress = False
                for c in list(self.deferred):
                    self.line, self.file = c.line, c.file
                    if c.step(False):
                        self.deferred.remove(c)
                        progress = True
            # numbers whose kind was only seen through stored values
            bound = False
            for v in self.tvars:
                p = prune(v)
                if isinstance(p, TVar) and p.cands:
                    p.ref = FLOAT if "Float" in p.cands else INT
                    bound = True
            if bound:
                continue
            if not self.deferred:
                break
            done = False
            for c in sorted(self.deferred, key=lambda c: c.weak):
                self.line, self.file = c.line, c.file
                if c.step(True):
                    self.deferred.remove(c)
                    done = True
                    break
            if not done:
                c = self.deferred[0]
                self.fail("I can't tell what kind of value this is. Give it a value first.", c.line, c.file)
        for v in self.tvars:
            if isinstance(prune(v), TVar):
                prune(v).ref = INT

    def settle(self):
        """Solve what can be solved without guessing (used to find generic functions)."""
        progress = True
        while progress:
            progress = False
            for c in list(self.deferred):
                if c.step(False):
                    self.deferred.remove(c)
                    progress = True

    def check_bot_calls(self):
        uses = set(self.direct_bot)
        changed = True
        while changed:
            changed = False
            for caller, callee, _, _, _ in self.calls:
                if caller and callee.name in uses and caller not in uses:
                    uses.add(caller)
                    changed = True
        for sig in self.funcs.sigs + self.instances:
            sig.uses_bot = sig.name in uses
        for caller, callee, in_handler, line, file in self.calls:
            if caller is None and not in_handler and callee.name in uses:
                self.fail(f'"{callee.display}" answers Telegram messages, so it can only be used inside a '
                          f'"when someone sends ..." rule.', line, file)
        if self.persist is not None and not self.handlers:
            self.fail('"Remember everything in" works only in bots, programs with "when someone sends ..." rules.',
                      self.persist.line, self.persist.file)


def walk(body):
    for s in body:
        yield s
        for attr in ("body", "then", "else_"):
            sub = getattr(s, attr, None)
            if isinstance(sub, list):
                yield from walk(sub)


class Types:
    """The result of type checking, as the backends see it."""

    def __init__(self, checker):
        self.checker = checker
        self.params = checker.params
        self.ret = checker.ret
        self.kinds = checker.kinds

    def kind_fields(self, kind):
        return [(f, deep(self.checker.kind_fields[kind.name][f])) for f in kind.fields]

    def of(self, e):
        return deep(e.ty)

    def var(self, v):
        return deep(self.checker.vars[(v.scope, v.func, v.name)])

    def variables(self, scope):
        return [(key[2], key[1], deep(t)) for key, t in self.checker.vars.items() if key[0] == scope]

    def param_types(self, sig):
        return [deep(t) for t in self.params[sig.name]]

    def ret_type(self, sig):
        return deep(self.ret[sig.name])


def find_generic(funcs):
    """Functions whose values stay open after checking the body on its own: they work with any type."""
    generic = set()
    for sig in funcs.sigs:
        scratch = Checker(funcs)
        try:
            scratch.function(sig)
            scratch.settle()
        except EngError:
            continue
        if any(has_tvar(t) for t in scratch.params[sig.name] + ([scratch.ret[sig.name]] if sig.returns else [])):
            generic.add(sig.name)
    return generic


def check(prog, funcs):
    kinds = getattr(funcs, "kinds", None)
    KIND_IDS.clear()
    if kinds:
        KIND_IDS.update({name: k.id for name, k in kinds.by_name.items()})
    generic = find_generic(funcs)
    c = Checker(funcs, generic)
    setup = [s for s in prog if not isinstance(s, (Handler, FuncDef))]
    c.block(setup)
    c.block([s for s in prog if isinstance(s, FuncDef)])
    c.block([s for s in prog if isinstance(s, Handler)])
    c.solve()
    c.check_bot_calls()
    # backends see one function per generic use, with known types
    prog[:] = [s for s in prog if not (isinstance(s, FuncDef) and s.sig.name in generic)] + \
        [FuncDef(i) for i in c.instances]
    return Types(c)
