"""Core IR: what every English sentence turns into. Backends only see these nodes.

Statements get .line and .file attributes from the parser; expressions get .ty
from the type checker.
"""

import json
from dataclasses import dataclass, field


def _args(xs):
    return ", ".join(map(str, xs))


# ───────────── expressions ─────────────

@dataclass(eq=False)
class Num:
    value: int
    def __str__(self): return f"Int({self.value})"

@dataclass(eq=False)
class Dec:
    value: float
    def __str__(self): return f"Dec({self.value!r})"

@dataclass(eq=False)
class Text:
    value: str
    def __str__(self): return f"Text({json.dumps(self.value, ensure_ascii=False)})"

@dataclass(eq=False)
class Truth:
    def __str__(self): return "Always"

@dataclass(eq=False)
class Var:
    name: str
    display: str = ""
    scope: str = "global"     # global | local | user
    func: str = ""            # owning function for locals
    def __str__(self): return f"Var({self.name})"

@dataclass(eq=False)
class Bin:
    op: str                   # + - * / % ^
    left: object
    right: object
    def __str__(self): return f"Bin({self.op}, {self.left}, {self.right})"

@dataclass(eq=False)
class Cmp:
    op: str                   # > < >= <= == != contains starts ~=
    left: object
    right: object
    def __str__(self): return f"Cmp({self.op}, {self.left}, {self.right})"

@dataclass(eq=False)
class Logic:
    op: str                   # and / or / not
    args: list
    def __str__(self): return f"{self.op.capitalize()}({_args(self.args)})"

@dataclass(eq=False)
class Random:
    low: object
    high: object
    def __str__(self): return f"Random({self.low}, {self.high})"

@dataclass(eq=False)
class OneOf:
    items: list
    def __str__(self): return f"OneOf({_args(self.items)})"

@dataclass(eq=False)
class RandomItem:
    items: object
    def __str__(self): return f"RandomItem({self.items})"

@dataclass(eq=False)
class Concat:
    parts: list
    def __str__(self): return f"Concat({_args(self.parts)})"

@dataclass(eq=False)
class ListLit:
    items: list
    def __str__(self): return f"List({_args(self.items)})"

@dataclass(eq=False)
class EmptyMap:
    def __str__(self): return "Table()"

@dataclass(eq=False)
class Index:
    container: object
    index: object             # None means the last item
    def __str__(self): return f"Item({'last' if self.index is None else self.index}, {self.container})"

@dataclass(eq=False)
class Slice:
    container: object
    start: object
    end: object
    def __str__(self): return f"Items({self.start}, {self.end}, {self.container})"

@dataclass(eq=False)
class Lookup:
    table: object
    key: object
    or_zero: bool = False     # a missing key reads as zero / empty instead of failing
    def __str__(self): return f"Lookup({self.table}, {self.key})"

@dataclass(eq=False)
class Builtin:
    name: str
    args: list
    def __str__(self): return f"{self.name}({_args(self.args)})"

@dataclass(eq=False)
class Call:
    func: object              # FuncSig
    args: list
    def __str__(self): return f"Call({self.func.name}{', ' if self.args else ''}{_args(self.args)})"

@dataclass(eq=False)
class Msg:
    def __str__(self): return "Message"

@dataclass(eq=False)
class Name:
    def __str__(self): return "SenderName"

@dataclass(eq=False)
class MsgNum:
    def __str__(self): return "MessageNumber"

@dataclass(eq=False)
class IsNum:
    def __str__(self): return "MessageIsNumber"


# ───────────── statements ─────────────

def dump_body(body, ind):
    return [l for s in body for l in s.dump(ind + "  ")]


@dataclass(eq=False)
class Assign:
    target: object            # Var | Index | Lookup
    expr: object
    def dump(self, ind): return [f"{ind}Assign({self.target}, {self.expr})"]

@dataclass(eq=False)
class AddTo:
    target: object
    expr: object
    create: bool = False      # the name did not exist before this sentence
    kind: str = ""            # num | list | text, decided by the type checker
    def dump(self, ind): return [f"{ind}AddTo({self.target}, {self.expr})"]

@dataclass(eq=False)
class Print:
    parts: list
    def dump(self, ind): return [f"{ind}Print({_args(self.parts)})"]

@dataclass(eq=False)
class WriteFile:
    path: object
    parts: list
    append: bool
    def dump(self, ind): return [f"{ind}{'AppendFile' if self.append else 'WriteFile'}({self.path}, {_args(self.parts)})"]

@dataclass(eq=False)
class Reply:
    parts: list
    buttons: list = field(default_factory=list)
    def dump(self, ind):
        b = f" Buttons({_args(self.buttons)})" if self.buttons else ""
        return [f"{ind}Reply({_args(self.parts)}){b}"]

@dataclass(eq=False)
class Repeat:
    count: object
    body: list = field(default_factory=list)
    def dump(self, ind): return [f"{ind}Repeat({self.count})"] + dump_body(self.body, ind)

@dataclass(eq=False)
class While:
    cond: object
    body: list = field(default_factory=list)
    def dump(self, ind): return [f"{ind}While({self.cond})"] + dump_body(self.body, ind)

@dataclass(eq=False)
class ForEach:
    var: object
    items: object
    body: list = field(default_factory=list)
    def dump(self, ind): return [f"{ind}ForEach({self.var}, {self.items})"] + dump_body(self.body, ind)

@dataclass(eq=False)
class ForRange:
    var: object
    start: object
    end: object
    body: list = field(default_factory=list)
    def dump(self, ind): return [f"{ind}ForRange({self.var}, {self.start}, {self.end})"] + dump_body(self.body, ind)

@dataclass(eq=False)
class If:
    cond: object
    then: list = field(default_factory=list)
    else_: list = None
    def dump(self, ind):
        lines = [f"{ind}If({self.cond})"] + dump_body(self.then, ind)
        if self.else_ is not None:
            lines += [f"{ind}Else"] + dump_body(self.else_, ind)
        return lines

@dataclass(eq=False)
class Break:
    def dump(self, ind): return [f"{ind}Break"]

@dataclass(eq=False)
class Continue:
    def dump(self, ind): return [f"{ind}Continue"]

@dataclass(eq=False)
class Exit:
    def dump(self, ind): return [f"{ind}Exit"]

@dataclass(eq=False)
class Return:
    expr: object = None
    def dump(self, ind): return [f"{ind}Return({self.expr if self.expr is not None else ''})"]

@dataclass(eq=False)
class RemoveValue:
    items: object
    value: object
    def dump(self, ind): return [f"{ind}RemoveValue({self.items}, {self.value})"]

@dataclass(eq=False)
class RemoveAt:
    items: object
    index: object
    def dump(self, ind): return [f"{ind}RemoveAt({self.items}, {self.index})"]

@dataclass(eq=False)
class RemoveKey:
    table: object
    key: object
    def dump(self, ind): return [f"{ind}RemoveKey({self.table}, {self.key})"]

@dataclass(eq=False)
class DeleteFile:
    path: object
    def dump(self, ind): return [f"{ind}DeleteFile({self.path})"]

@dataclass(eq=False)
class Sort:
    items: object
    def dump(self, ind): return [f"{ind}Sort({self.items})"]

@dataclass(eq=False)
class Reverse:
    items: object
    def dump(self, ind): return [f"{ind}Reverse({self.items})"]

@dataclass(eq=False)
class Ask:
    prompt: object
    target: object            # Var
    def dump(self, ind): return [f"{ind}Ask({self.prompt}, {self.target})"]

@dataclass(eq=False)
class CallStmt:
    call: object
    def dump(self, ind): return [f"{ind}{self.call}"]

@dataclass(eq=False)
class Persist:
    path: object
    def dump(self, ind): return [f"{ind}Persist({self.path})"]

@dataclass(eq=False)
class Handler:
    cond: object
    body: list = field(default_factory=list)
    def dump(self, ind): return [f"{ind}OnMessage({self.cond})"] + dump_body(self.body, ind)

@dataclass(eq=False)
class FuncDef:
    sig: object
    def dump(self, ind):
        params = ", ".join(self.sig.params)
        kind = "Function" if self.sig.returns else "Command"
        return [f"{ind}{kind}({self.sig.name}{': ' if params else ''}{params})"] + dump_body(self.sig.body, ind)


@dataclass(eq=False)
class FuncSig:
    name: str                 # unique internal name, e.g. "area_of_and"
    display: str              # the header as written, for messages
    pattern: list             # ("lit", kind, value) | ("param", name)
    params: list
    returns: bool             # "to find ..." gives back a value
    line: int = 0
    file: str = ""
    body: list = field(default_factory=list)
    uses_bot: bool = False


# Markers produced by the sentence parser while building blocks.
@dataclass(eq=False)
class End:
    pass

@dataclass(eq=False)
class ElseMark:
    stmt: object = None       # inline statement, or None when a block follows

@dataclass(eq=False)
class Seq:
    stmts: list


def dump_program(prog):
    return "\n".join(l for s in prog for l in s.dump(""))


def is_bot(prog):
    return any(isinstance(s, Handler) for s in prog)


def walk_stmts(body):
    """Every statement, depth first, including nested bodies."""
    for s in body:
        yield s
        for attr in ("body", "then", "else_"):
            sub = getattr(s, attr, None)
            if isinstance(sub, list):
                yield from walk_stmts(sub)
        if isinstance(s, FuncDef):
            yield from walk_stmts(s.sig.body)


# ───────────── back to English, for messages ─────────────

OP_WORDS = {"+": "plus", "-": "minus", "*": "times", "/": "divided by", "%": "mod", "^": "to the power of"}
CMP_WORDS = {">": "is more than", "<": "is less than", ">=": "is at least", "<=": "is at most",
             "!=": "is not", "==": "is", "~=": "is", "contains": "contains", "starts": "starts with"}


def english(e, top=True):
    if isinstance(e, Num):
        return str(e.value)
    if isinstance(e, Dec):
        return repr(e.value)
    if isinstance(e, Text):
        return json.dumps(e.value, ensure_ascii=False)
    if isinstance(e, Var):
        return e.display or e.name
    if isinstance(e, Bin):
        s = f"{english(e.left, False)} {OP_WORDS[e.op]} {english(e.right, False)}"
        return s if top else f"({s})"
    if isinstance(e, Cmp):
        return f"{english(e.left)} {CMP_WORDS[e.op]} {english(e.right)}"
    if isinstance(e, Logic):
        if e.op == "not":
            return f"not ({english(e.args[0])})"
        return f" {e.op} ".join(f"({english(a)})" for a in e.args)
    if isinstance(e, Msg):
        return "the message"
    if isinstance(e, Name):
        return "their name"
    if isinstance(e, MsgNum):
        return "the number they sent"
    if isinstance(e, Random):
        return f"a random number from {english(e.low)} to {english(e.high)}"
    if isinstance(e, OneOf):
        return "one of " + ", ".join(english(x) for x in e.items)
    if isinstance(e, RandomItem):
        return f"one of {english(e.items)}"
    if isinstance(e, Concat):
        return " and then ".join(english(x) for x in e.parts)
    if isinstance(e, ListLit):
        return ", ".join(english(x) for x in e.items)
    if isinstance(e, Index):
        return f"the last item of {english(e.container)}" if e.index is None else \
            f"item {english(e.index)} of {english(e.container)}"
    if isinstance(e, Slice):
        return f"items {english(e.start)} to {english(e.end)} of {english(e.container)}"
    if isinstance(e, Lookup):
        return f"{english(e.table)} of {english(e.key)}"
    if isinstance(e, Call):
        out, k = [], 0
        for el in e.func.pattern:
            if el[0] == "param":
                out.append(english(e.args[k], False))
                k += 1
            else:
                out.append(str(el[2]))
        s = " ".join(out)
        return s if top else f"({s})"
    if isinstance(e, Builtin):
        return f"{e.name.replace('_', ' ')} of {', '.join(english(a, False) for a in e.args)}"
    return str(e)
