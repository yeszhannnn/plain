"""Core IR -> LLVM IR text, linked with runtime/*.c (and Boehm GC) into a native executable."""

import dataclasses
import hashlib
import os
import shutil
import struct
import subprocess
import tempfile

from .core import (AddTo, Ask, Assign, Bin, Break, Builtin, Call, CallStmt, Cmp, Concat, Continue, Dec, DeleteFile,
                   EmptyMap, Exit, ForEach, ForRange, FuncDef, Handler, If, Index, IsNum, ListLit, Logic, Lookup,
                   Msg, MsgNum, Name, Num, OneOf, Persist, Print, Random, RandomItem, RemoveAt, RemoveKey,
                   RemoveValue, Repeat, Reply, Return, Reverse, Slice, Sort, Text, Truth, Var, While, WriteFile,
                   english, is_bot, walk_stmts, New, Field, Nothing, Try, Fail, ErrorText, Comprehension, BestBy,
                   FromJson, Eval)
from .errors import EngError
from .types import BOOL, FLOAT, INT, TEXT, desc

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(HERE, "runtime")
CORE_SOURCES = ["core.c", "text.c", "list.c", "map.c", "files.c", "json.c"]
NET_SOURCES = ["net.c"]
BOT_SOURCES = ["telegram.c"]

LLT = {"Int": "i64", "Float": "double", "Bool": "i1", "Text": "ptr", "List": "ptr", "Map": "ptr", "Nothing": "void"}

with open(os.path.join(RUNTIME_DIR, "declarations.llvm"), encoding="utf-8") as _f:
    DECLS = _f.read().rstrip("\n")   # the runtime's functions, shared with compiler-rs


def llt(t):
    return "ptr" if t.name.startswith("Kind:") else LLT[t.name]


def san(name):
    return "".join(ch if ch.isalnum() or ch in "._" else "_" for ch in name)


def fconst(x):
    return "0x" + struct.pack(">d", x).hex().upper()


class Fn:
    def __init__(self, gen, header):
        self.gen = gen
        self.header = header
        self.lines = []
        self.allocas = []
        self.prologue = []
        self.tmp = 0
        self.terminated = False
        self.loops = []           # (continue label, break label, "try" depth at the loop)
        self.tries = 0            # how many "try" blocks the code being emitted is inside
        self.volatile = False     # the function has "try": keep variables in memory across longjmp

    def t(self):
        self.tmp += 1
        return f"%t{self.tmp}"

    def label(self, hint):
        self.gen.lbl += 1
        return f"{hint}.{self.gen.lbl}"

    def emit(self, s):
        if self.terminated:
            self.start(self.label("dead"))
        if self.volatile:
            if " = load " in s:
                s = s.replace(" = load ", " = load volatile ", 1)
            elif s.startswith("store "):
                s = "store volatile " + s[6:]
        self.lines.append("  " + s)

    def start(self, label):
        if not self.terminated:
            self.lines.append(f"  br label %{label}")
        self.lines.append(f"{label}:")
        self.terminated = False

    def term(self, s):
        self.emit(s)
        self.terminated = True

    def alloca(self, name, ty):
        self.allocas.append(f"  {name} = alloca {ty}")

    def text(self):
        return "\n".join([self.header + " {", "entry:"] + self.allocas + self.prologue + self.lines + ["}"])


class Gen:
    def __init__(self, prog, funcs, types, file):
        self.prog = prog
        self.funcs = funcs
        self.types = types
        self.file = file
        self.strings = {}
        self.lbl = 0
        self.f = None
        self.users = {name: k for k, (name, _, _) in enumerate(types.variables("user"))}
        self.user_types = {name: t for name, _, t in types.variables("user")}
        self.globals_ = types.variables("global")
        self.main_locals = self.private_globals()
        self.builders = {(t.scope, t.name) for t in walk_all(prog)
                         if isinstance(t, AddTo) and t.kind == "text" and isinstance(t.target, Var)
                         for t in [t.target]}
        self.extra = []
        files = {os.path.basename(getattr(s, "file", "") or file) for s in walk_all(prog)}
        self.one_file = len(files) <= 1     # then the file name is set once, at start-up

    def private_globals(self):
        """Top-level variables that no function and no bot rule touches: they become locals of main,
        so LLVM can keep them in registers."""
        if any(isinstance(st, (Handler, Persist)) for st in self.prog):
            return set()
        used = set()
        for st in self.prog:
            if isinstance(st, FuncDef):
                used |= var_names(st.sig.body)
        return {name for name, _, _ in self.globals_} - used

    # ── helpers ──

    def T(self, e):
        return self.types.of(e)

    def string(self, s):
        if s not in self.strings:
            self.strings[s] = f"@.s{len(self.strings)}"
        return self.strings[s]

    def call(self, ret, fn, args=""):
        if ret == "void":
            self.f.emit(f"call void @{fn}({args})")
            return None
        r = self.f.t()
        self.f.emit(f"{r} = call {ret} @{fn}({args})")
        return r

    def truthy(self, fn, args):
        r = self.call("i32", fn, args)
        b = self.f.t()
        self.f.emit(f"{b} = icmp ne i32 {r}, 0")
        return b

    def conv(self, v, src, dst):
        if src.name == "Int" and dst.name == "Float":
            r = self.f.t()
            self.f.emit(f"{r} = sitofp i64 {v} to double")
            return r
        return v

    def to_slot(self, v, t):
        if t.name == "Int":
            return v
        r = self.f.t()
        if t.name == "Float":
            self.f.emit(f"{r} = bitcast double {v} to i64")
        elif t.name == "Bool":
            self.f.emit(f"{r} = zext i1 {v} to i64")
        else:
            self.f.emit(f"{r} = ptrtoint ptr {v} to i64")
        return r

    def from_slot(self, v, t):
        if t.name == "Int":
            return v
        r = self.f.t()
        if t.name == "Float":
            self.f.emit(f"{r} = bitcast i64 {v} to double")
        elif t.name == "Bool":
            self.f.emit(f"{r} = trunc i64 {v} to i1")
        else:
            self.f.emit(f"{r} = inttoptr i64 {v} to ptr")
        return r

    def zero(self, t):
        if t.name == "Int":
            return "0"
        if t.name == "Float":
            return fconst(0.0)
        if t.name == "Bool":
            return "false"
        if t.name == "Text":
            return self.string("")
        if t.name.startswith("Kind:"):
            return "null"
        if t.name == "List":
            return self.call("ptr", "pl_list_of", f"ptr {self.desc(t.args[0])}")
        return self.call("ptr", "pl_map_new", f"i32 {1 if t.args[0].name == 'Text' else 0}")

    def desc(self, t):
        return self.string(desc(t))

    def addr(self, v):
        if v.scope == "global":
            return f"%m.{san(v.name)}" if v.name in self.main_locals else f"@g.{san(v.name)}"
        if v.scope == "local":
            return f"%l.{san(v.name)}"
        return self.call("ptr", "pl_user_slot", f"i32 {self.users[v.name]}")

    # ── expressions ──

    def expr(self, e, share=True):
        """share=False: the value is only looked at right away (printed, compared, measured),
        so a text that is being built in place may stay unshared."""
        t = self.T(e)
        f = self.f
        if isinstance(e, Num):
            return str(e.value)
        if isinstance(e, Dec):
            return fconst(e.value)
        if isinstance(e, Text):
            return self.string(e.value)
        if isinstance(e, Truth):
            return "true"
        if isinstance(e, Var):
            a = self.addr(e)
            r = f.t()
            f.emit(f"{r} = load {llt(t)}, ptr {a}")
            if share and (e.scope, e.name) in self.builders:
                self.call("void", "pl_text_share", f"ptr {r}")
            return r
        if isinstance(e, Msg):
            return self.call("ptr", "pl_msg_text")
        if isinstance(e, Name):
            return self.call("ptr", "pl_msg_name")
        if isinstance(e, MsgNum):
            return self.call("i64", "pl_msg_number")
        if isinstance(e, IsNum):
            return self.truthy("pl_msg_is_number", "")
        if isinstance(e, Random):
            lo, hi = self.expr(e.low), self.expr(e.high)
            return self.call("i64", "pl_random", f"i64 {lo}, i64 {hi}")
        if isinstance(e, OneOf):
            vals = [self.conv(self.expr(x), self.T(x), t) for x in e.items]
            idx = self.call("i64", "pl_random", f"i64 0, i64 {len(vals) - 1}")
            acc = vals[-1]
            for k in range(len(vals) - 2, -1, -1):
                c, r = f.t(), f.t()
                f.emit(f"{c} = icmp eq i64 {idx}, {k}")
                f.emit(f"{r} = select i1 {c}, {llt(t)} {vals[k]}, {llt(t)} {acc}")
                acc = r
            return acc
        if isinstance(e, RandomItem):
            return self.from_slot(self.call("i64", "pl_list_random", f"ptr {self.expr(e.items)}"), t)
        if isinstance(e, Concat):
            vals = [(self.T(x), self.expr(x, False)) for x in e.parts]
            self.call("void", "pl_cat_begin")
            for pt, v in vals:
                self.out_value("cat", v, pt)
            return self.call("ptr", "pl_cat_end")
        if isinstance(e, ListLit):
            el = t.args[0]
            lst = self.call("ptr", "pl_list_of", f"ptr {self.desc(el)}")
            for x in e.items:
                v = self.to_slot(self.conv(self.expr(x), self.T(x), el), el)
                self.call("void", "pl_list_add", f"ptr {lst}, i64 {v}")
            return lst
        if isinstance(e, EmptyMap):
            return self.zero(t)
        if isinstance(e, Bin):
            return self.arith(e, t)
        if isinstance(e, Cmp):
            return self.compare(e)
        if isinstance(e, Logic):
            return self.logic(e)
        if isinstance(e, Index):
            c = self.expr(e.container)
            i = self.expr(e.index) if e.index is not None else "-1"
            if e.kind == "text":
                return self.call("ptr", "pl_text_char", f"ptr {c}, i64 {i}")
            if e.index is None:
                return self.from_slot(self.call("i64", "pl_list_get", f"ptr {c}, i64 {i}"), t)
            r = f.t()
            f.emit(f"{r} = load i64, ptr {self.item_ptr(c, i)}")
            return self.from_slot(r, t)
        if isinstance(e, Slice):
            c, a, b = self.expr(e.container), self.expr(e.start), self.expr(e.end)
            fn = "pl_text_slice" if t.name == "Text" else "pl_list_slice"
            return self.call("ptr", fn, f"ptr {c}, i64 {a}, i64 {b}")
        if isinstance(e, Lookup):
            return self.from_slot(self.lookup_slot(e, create=e.or_zero), t)
        if isinstance(e, Call):
            return self.call_fn(e)
        if isinstance(e, Builtin):
            return self.builtin(e, t)
        if isinstance(e, Nothing):
            return "null"
        if isinstance(e, ErrorText):
            return self.call("ptr", "pl_error_text")
        if isinstance(e, FromJson):
            v = self.call("i64", "pl_from_json", f"ptr {self.expr(e.expr)}, ptr {self.desc(t)}")
            return self.from_slot(v, t)
        if isinstance(e, Comprehension):
            items = self.expr(e.items)
            el, out_t = self.T(e.items).args[0], t.args[0]
            out = self.call("ptr", "pl_list_of", f"ptr {self.desc(out_t)}")

            def body(nxt):
                if e.cond is not None:
                    c = self.expr(e.cond)
                    take = f.label("each.take")
                    f.term(f"br i1 {c}, label %{take}, label %{nxt}")
                    f.start(take)
                v = self.conv(self.expr(e.expr), self.T(e.expr), out_t) if e.expr is not None else \
                    self.expr(e.var)
                self.call("void", "pl_list_add", f"ptr {out}, i64 {self.to_slot(v, out_t)}")
            self.each_item(items, el, e.var, body)
            return out
        if isinstance(e, BestBy):
            items = self.expr(e.items)
            keys, kt = self.keys_of(items, t, e.var, e.key)
            best = self.call("i64", "pl_list_best", f"ptr {items}, ptr {keys}, ptr {self.desc(kt)}, "
                                                    f"i32 {int(e.largest)}")
            return self.from_slot(best, t)
        if isinstance(e, Field):
            o = self.expr(e.obj)
            s = self.call("i64", "pl_obj_get", f"ptr {o}, i32 {e.kind.id}, i32 {e.kind.fields.index(e.field)}")
            return self.from_slot(s, t)
        if isinstance(e, New):
            o = self.call("ptr", "pl_obj_new", f"i32 {e.kind.id}")
            fields = dict(self.types.kind_fields(e.kind))
            for key, x in e.inits:
                v = self.conv(self.expr(x), self.T(x), fields[key])
                self.call("void", "pl_obj_set", f"ptr {o}, i32 {e.kind.id}, i32 {e.kind.fields.index(key)}, "
                                                f"i64 {self.to_slot(v, fields[key])}")
            return o
        raise EngError(f"internal: no code for {e}")

    def lookup_slot(self, e, create=False):
        m = self.expr(e.table)
        k = self.to_slot(self.expr(e.key), self.T(e.key))
        if create:
            return self.call("i64", "pl_map_get_or_new", f"ptr {m}, i64 {k}, ptr {self.desc(self.T(e))}")
        return self.call("i64", "pl_map_get", f"ptr {m}, i64 {k}, ptr {self.string(english(e.table))}")

    def arith(self, e, t):
        f = self.f
        a, b = self.expr(e.left), self.expr(e.right)
        r = f.t()
        if t.name == "Float":
            a, b = self.conv(a, self.T(e.left), FLOAT), self.conv(b, self.T(e.right), FLOAT)
            if e.op in "+-*":
                f.emit(f"{r} = {dict(zip('+-*', ['fadd', 'fsub', 'fmul']))[e.op]} double {a}, {b}")
                return r
            fn = {"/": "pl_fdiv", "%": "pl_fmod", "^": "pl_fpow"}[e.op]
            return self.call("double", fn, f"double {a}, double {b}")
        if e.op in "+-*":
            f.emit(f"{r} = {dict(zip('+-*', ['add', 'sub', 'mul']))[e.op]} i64 {a}, {b}")
            return r
        fn = {"/": "pl_div", "%": "pl_rem", "^": "pl_ipow"}[e.op]
        return self.call("i64", fn, f"i64 {a}, i64 {b}")

    def compare(self, e):
        f = self.f
        lt, rt = self.T(e.left), self.T(e.right)
        a, b = self.expr(e.left, False), self.expr(e.right, False)
        if e.op == "~=":
            return self.truthy("pl_text_same", f"ptr {a}, ptr {b}")
        if e.op == "starts":
            return self.truthy("pl_starts", f"ptr {a}, ptr {b}")
        if e.op == "contains":
            if e.kind == "text":
                return self.truthy("pl_contains", f"ptr {a}, ptr {b}")
            if e.kind == "list":
                el = lt.args[0]
                v = self.to_slot(self.conv(b, rt, el), el)
                return self.truthy("pl_list_contains", f"ptr {a}, i64 {v}, ptr {self.desc(el)}")
            return self.truthy("pl_map_has", f"ptr {a}, i64 {self.to_slot(b, rt)}")
        r = f.t()
        if lt.name in ("Int", "Float") and (lt.name == "Float" or rt.name == "Float"):
            a, b = self.conv(a, lt, FLOAT), self.conv(b, rt, FLOAT)
            pred = {">": "ogt", "<": "olt", ">=": "oge", "<=": "ole", "==": "oeq", "!=": "une"}[e.op]
            f.emit(f"{r} = fcmp {pred} double {a}, {b}")
            return r
        pred = {">": "sgt", "<": "slt", ">=": "sge", "<=": "sle", "==": "eq", "!=": "ne"}[e.op]
        if lt.name == "Text":
            c = self.call("i32", "pl_text_cmp", f"ptr {a}, ptr {b}")
            f.emit(f"{r} = icmp {pred} i32 {c}, 0")
            return r
        if lt.name.startswith("Kind:"):
            f.emit(f"{r} = icmp {pred} ptr {a}, {b}")
            return r
        if lt.name in ("List", "Map"):
            raise EngError("Lists and tables can't be compared with \"is\". Compare their items instead.",
                           getattr(e, "line", None))
        f.emit(f"{r} = icmp {pred} {llt(lt)} {a}, {b}")
        return r

    def logic(self, e):
        f = self.f
        first = self.expr(e.args[0])
        if e.op == "not":
            r = f.t()
            f.emit(f"{r} = xor i1 {first}, true")
            return r
        slot = f.t().replace("%t", "%logic")
        f.alloca(slot, "i1")
        f.emit(f"store i1 {first}, ptr {slot}")
        done = f.label("logic.end")
        for a in e.args[1:]:
            more = f.label("logic.next")
            cur = f.t()
            f.emit(f"{cur} = load i1, ptr {slot}")
            if e.op == "and":
                f.term(f"br i1 {cur}, label %{more}, label %{done}")
            else:
                f.term(f"br i1 {cur}, label %{done}, label %{more}")
            f.start(more)
            v = self.expr(a)
            f.emit(f"store i1 {v}, ptr {slot}")
        f.start(done)
        r = f.t()
        f.emit(f"{r} = load i1, ptr {slot}")
        return r

    def call_fn(self, e):
        sig = e.func
        args = []
        for x, pt in zip(e.args, self.types.param_types(sig)):
            args.append(f"{llt(pt)} {self.conv(self.expr(x), self.T(x), pt)}")
        ret = self.types.ret_type(sig) if sig.returns else None
        return self.call(llt(ret) if ret else "void", f"plain.{sig.name}", ", ".join(args))

    def builtin(self, e, t):
        f = self.f
        n = e.name
        ts = [self.T(a) for a in e.args]
        vs = [self.expr(a, n != "length") for a in e.args]
        a, at = (vs[0], ts[0]) if vs else (None, None)
        if n == "length":
            fn = {"Text": "pl_text_len", "List": "pl_list_len", "Map": "pl_map_len"}[at.name]
            return self.call("i64", fn, f"ptr {a}")
        if n == "sum":
            if at.args[0].name == "Float":
                return self.call("double", "pl_list_sum_float", f"ptr {a}")
            return self.call("i64", "pl_list_sum_int", f"ptr {a}")
        if n == "average":
            return self.call("double", "pl_list_average", f"ptr {a}, ptr {self.desc(at.args[0])}")
        if n in ("largest", "smallest"):
            s = self.call("i64", "pl_list_extreme", f"ptr {a}, ptr {self.desc(at.args[0])}, "
                                                    f"i32 {1 if n == 'largest' else 0}")
            return self.from_slot(s, t)
        if n == "sqrt":
            return self.call("double", "pl_sqrt", f"double {self.conv(a, at, FLOAT)}")
        if n == "abs":
            if at.name == "Float":
                return self.call("double", "llvm.fabs.f64", f"double {a}")
            neg, c, r = f.t(), f.t(), f.t()
            f.emit(f"{neg} = sub i64 0, {a}")
            f.emit(f"{c} = icmp slt i64 {a}, 0")
            f.emit(f"{r} = select i1 {c}, i64 {neg}, i64 {a}")
            return r
        if n == "squared":
            r = f.t()
            f.emit(f"{r} = {'fmul double' if at.name == 'Float' else 'mul i64'} {a}, {a}")
            return r
        if n in ("round", "round_up", "round_down"):
            if at.name == "Int":
                return a
            return self.call("i64", {"round": "pl_round", "round_up": "pl_ceil", "round_down": "pl_floor"}[n],
                             f"double {a}")
        if n in ("upper", "lower", "trim", "words", "lines", "letters"):
            return self.call("ptr", f"pl_{n}", f"ptr {a}")
        if n in ("split", "replace"):
            return self.call("ptr", f"pl_{n}", ", ".join(f"ptr {v}" for v in vs))
        if n == "join":
            return self.call("ptr", "pl_join", f"ptr {a}, ptr {self.desc(at.args[0])}, ptr {vs[1]}")
        if n == "to_int":
            if at.name == "Text":
                return self.call("i64", "pl_text_to_int", f"ptr {a}")
            if at.name == "Float":
                r = f.t()
                f.emit(f"{r} = fptosi double {a} to i64")
                return r
            return a
        if n == "to_float":
            if at.name == "Text":
                return self.call("double", "pl_text_to_float", f"ptr {a}")
            return self.conv(a, at, FLOAT)
        if n == "to_text":
            if at.name == "Text":
                return a
            return self.call("ptr", "pl_to_text", f"i64 {self.to_slot(a, at)}, ptr {self.desc(at)}")
        if n == "is_number":
            return self.truthy("pl_text_is_number", f"ptr {a}")
        if n == "file_text":
            return self.call("ptr", "pl_file_read", f"ptr {a}")
        if n == "file_exists":
            return self.truthy("pl_file_exists", f"ptr {a}")
        if n == "arguments":
            return self.call("ptr", "pl_arguments")
        if n == "known":
            return self.truthy("pl_map_has", f"ptr {a}, i64 {self.to_slot(vs[1], ts[1])}")
        if n == "now":
            return self.call("ptr", "pl_now", f"i32 {self.types.kinds.by_name['moment'].id}")
        if n in ("timestamp", "millis", "exit_code"):
            return self.call("i64", f"pl_{n}")
        if n in ("sin", "cos", "tan"):
            return self.call("double", n, f"double {self.conv(a, at, FLOAT)}")
        if n == "log":
            return self.call("double", "pl_log", f"double {self.conv(a, at, FLOAT)}")
        if n == "random_float":
            lo, hi = self.conv(vs[0], ts[0], FLOAT), self.conv(vs[1], ts[1], FLOAT)
            return self.call("double", "pl_random_float", f"double {lo}, double {hi}")
        if n == "code":
            return self.call("i64", "pl_code", f"ptr {a}")
        if n == "char":
            return self.call("ptr", "pl_char", f"i64 {a}")
        if n in ("env", "fetch", "run_output"):
            return self.call("ptr", f"pl_{n}", f"ptr {a}")
        if n == "run":
            return self.call("i64", "pl_run", f"ptr {a}")
        if n == "position":
            return self.call("i64", "pl_position", f"ptr {a}, ptr {vs[1]}")
        if n in ("pad", "pad_left"):
            text = a if at.name == "Text" else self.call("ptr", "pl_to_text", f"i64 {self.to_slot(a, at)}, "
                                                                              f"ptr {self.desc(at)}")
            return self.call("ptr", "pl_pad", f"ptr {text}, i64 {vs[1]}, i32 {int(n == 'pad_left')}")
        if n == "repeat_text":
            return self.call("ptr", "pl_repeat_text", f"ptr {a}, i64 {vs[1]}")
        if n == "decimals":
            return self.call("ptr", "pl_decimals", f"double {self.conv(a, at, FLOAT)}, i64 {vs[1]}")
        if n == "to_json":
            return self.call("ptr", "pl_to_json", f"i64 {self.to_slot(a, at)}, ptr {self.desc(at)}")
        if n == "post":
            body = a if at.name == "Text" else self.call("ptr", "pl_to_json", f"i64 {self.to_slot(a, at)}, "
                                                                              f"ptr {self.desc(at)}")
            return self.call("ptr", "pl_post", f"ptr {body}, ptr {vs[1]}, i32 {2 if at.name == 'Text' else 1}")
        if n == "wait":
            amount = self.conv(a, at, FLOAT)
            ms = f.t()
            f.emit(f"{ms} = fmul double {amount}, {fconst(float(e.args[1].value))}")
            self.call("void", "pl_wait", f"double {ms}")
            return "0"
        raise EngError(f"internal: no code for {n}")

    def out_value(self, kind, v, t):
        """Append one value to the output line ("out") or to glued text ("cat")."""
        if t.name == "Int":
            self.call("void", f"pl_{kind}_int", f"i64 {v}")
        elif t.name == "Float":
            self.call("void", f"pl_{kind}_float", f"double {v}")
        elif t.name == "Text":
            self.call("void", f"pl_{kind}_text", f"ptr {v}")
        elif t.name == "Bool":
            r = self.f.t()
            self.f.emit(f"{r} = select i1 {v}, ptr {self.string('true')}, ptr {self.string('false')}")
            self.call("void", f"pl_{kind}_text", f"ptr {r}")
        else:
            self.call("void", f"pl_{kind}_value", f"i64 {self.to_slot(v, t)}, ptr {self.desc(t)}")

    def glued(self, parts):
        vals = [(self.T(p), self.expr(p)) for p in parts]
        self.call("void", "pl_cat_begin")
        for t, v in vals:
            self.out_value("cat", v, t)
        return self.call("ptr", "pl_cat_end")

    # ── statements ──

    def write_target(self, target, v, vt):
        t = self.T(target)
        v = self.conv(v, vt, t)
        if isinstance(target, Var):
            self.f.emit(f"store {llt(t)} {v}, ptr {self.addr(target)}")
        elif isinstance(target, Field):
            o = self.expr(target.obj)
            k = target.kind
            self.call("void", "pl_obj_set", f"ptr {o}, i32 {k.id}, i32 {k.fields.index(target.field)}, "
                                            f"i64 {self.to_slot(v, t)}")
        elif isinstance(target, Index):
            c = self.expr(target.container)
            if target.index is None:
                self.call("void", "pl_list_set", f"ptr {c}, i64 -1, i64 {self.to_slot(v, t)}")
            else:
                slot = self.to_slot(v, t)
                self.f.emit(f"store i64 {slot}, ptr {self.item_ptr(c, self.expr(target.index))}")
        else:
            m = self.expr(target.table)
            k = self.to_slot(self.expr(target.key), self.T(target.key))
            self.call("void", "pl_map_set", f"ptr {m}, i64 {k}, i64 {self.to_slot(v, t)}")

    def item_at(self, lst, k):
        """Item k (counted from 0) of a list, already known to be there."""
        f = self.f
        pi, items, p, r = f.t(), f.t(), f.t(), f.t()
        f.emit(f"{pi} = getelementptr inbounds i8, ptr {lst}, i64 16")
        f.emit(f"{items} = load ptr, ptr {pi}")
        f.emit(f"{p} = getelementptr inbounds i64, ptr {items}, i64 {k}")
        f.emit(f"{r} = load i64, ptr {p}")
        return r

    def item_ptr(self, lst, i):
        """Address of item i (counted from 1) of a list, after checking that the item is there."""
        f = self.f
        k, n, ok, items, p = f.t(), f.t(), f.t(), f.t(), f.t()
        f.emit(f"{k} = sub i64 {i}, 1")
        f.emit(f"{n} = load i64, ptr {lst}")
        f.emit(f"{ok} = icmp ult i64 {k}, {n}")
        good, bad = f.label("item.ok"), f.label("item.bad")
        f.term(f"br i1 {ok}, label %{good}, label %{bad}, !prof !0")
        f.start(bad)
        f.emit(f"call void @pl_list_bad_index(ptr {lst}, i64 {i})")
        f.term("unreachable")
        f.start(good)
        pi = f.t()
        f.emit(f"{pi} = getelementptr inbounds i8, ptr {lst}, i64 16")
        f.emit(f"{items} = load ptr, ptr {pi}")
        f.emit(f"{p} = getelementptr inbounds i64, ptr {items}, i64 {k}")
        return p

    def read_target(self, target):
        if isinstance(target, Lookup):
            return self.from_slot(self.lookup_slot(target, create=True), self.T(target))
        return self.expr(target)

    def block(self, body):
        for s in body:
            if isinstance(s, (FuncDef, Handler, Persist)):
                continue
            line = getattr(s, "line", 0)
            if line:
                self.f.emit(f"store i32 {line}, ptr @pl_line")
                if not self.one_file:
                    self.f.emit(f"store ptr {self.string(os.path.basename(s.file or self.file))}, ptr @pl_file")
            self.stmt(s)

    def loop(self, cont, brk, body):
        self.f.loops.append((cont, brk, self.f.tries))
        self.block(body)
        self.f.loops.pop()

    def leave_tries(self, depth):
        """Jumping out of "try" blocks: forget their error handlers."""
        for _ in range(self.f.tries - depth):
            self.call("void", "pl_try_pop")

    def each_item(self, items, el, var, body):
        """Go through a list (as it was at the start); body(next_label) is emitted for every item."""
        f = self.f
        n = self.call("i64", "pl_list_len", f"ptr {items}")
        ctr = f.t().replace("%t", "%item")
        f.alloca(ctr, "i64")
        f.emit(f"store i64 0, ptr {ctr}")
        cond, step, done = f.label("item.cond"), f.label("item.next"), f.label("item.end")
        f.start(cond)
        i, c = f.t(), f.t()
        f.emit(f"{i} = load i64, ptr {ctr}")
        f.emit(f"{c} = icmp slt i64 {i}, {n}")
        body_label = f.label("item.body")
        f.term(f"br i1 {c}, label %{body_label}, label %{done}")
        f.start(body_label)
        pos = f.t()
        f.emit(f"{pos} = add i64 {i}, 1")
        got = f.t()
        f.emit(f"{got} = load i64, ptr {self.item_ptr(items, pos)}")    # the list may have shrunk meanwhile
        item = self.from_slot(got, el)
        self.write_target(var, item, el)
        body(step)
        f.start(step)
        i2, nx = f.t(), f.t()
        f.emit(f"{i2} = load i64, ptr {ctr}")
        f.emit(f"{nx} = add i64 {i2}, 1")
        f.emit(f"store i64 {nx}, ptr {ctr}")
        f.term(f"br label %{cond}")
        f.start(done)

    def keys_of(self, items, el, var, key):
        """A list with the key of every item, for sorting and for "with the largest"."""
        kt = self.T(key)
        keys = self.call("ptr", "pl_list_of", f"ptr {self.desc(kt)}")

        def body(nxt):
            k = self.expr(key)
            self.call("void", "pl_list_add", f"ptr {keys}, i64 {self.to_slot(k, kt)}")
        self.each_item(items, el, var, body)
        return keys, kt

    def stmt(self, s):
        f = self.f
        if isinstance(s, Assign):
            self.write_target(s.target, self.expr(s.expr), self.T(s.expr))
        elif isinstance(s, AddTo):
            t, vt = self.T(s.target), self.T(s.expr)
            if s.kind == "list":
                lst = self.read_target(s.target)
                v = self.expr(s.expr)
                el = t.args[0]
                self.call("void", "pl_list_add", f"ptr {lst}, i64 {self.to_slot(self.conv(v, vt, el), el)}")
                return
            if s.kind == "text" and isinstance(s.target, Var):     # grows in place while nothing else holds it
                v = self.expr(s.expr, False)
                cur = self.expr(s.target, False)
                self.write_target(s.target, self.call("ptr", "pl_text_append", f"ptr {cur}, ptr {v}"), TEXT)
                return
            cur = self.read_target(s.target)
            v = self.expr(s.expr)
            if s.kind == "text":
                self.write_target(s.target, self.call("ptr", "pl_text_glue", f"ptr {cur}, ptr {v}"), TEXT)
                return
            r = f.t()
            if t.name == "Float":
                f.emit(f"{r} = fadd double {cur}, {self.conv(v, vt, FLOAT)}")
            else:
                f.emit(f"{r} = add i64 {cur}, {v}")
            self.write_target(s.target, r, t)
        elif isinstance(s, Print):
            vals = [(self.T(p), self.expr(p, False)) for p in s.parts]
            for t, v in vals:
                self.out_value("out", v, t)
            self.call("void", "pl_out_print")
        elif isinstance(s, WriteFile):
            path = self.expr(s.path)
            text = self.glued(s.parts)
            self.call("void", "pl_file_write", f"ptr {path}, ptr {text}, i32 {1 if s.append else 0}")
        elif isinstance(s, DeleteFile):
            self.call("void", "pl_file_delete", f"ptr {self.expr(s.path)}")
        elif isinstance(s, Reply):
            vals = [(self.T(p), self.expr(p)) for p in s.parts]
            buttons = [self.expr(b) for b in s.buttons]
            for t, v in vals:
                self.out_value("out", v, t)
            for b in buttons:
                self.call("void", "pl_button", f"ptr {b}")
            self.call("void", "pl_out_reply")
        elif isinstance(s, Repeat):
            n = self.expr(s.count)
            ctr = f.t().replace("%t", "%rep")
            f.alloca(ctr, "i64")
            f.emit(f"store i64 0, ptr {ctr}")
            cond, body, step, done = f.label("repeat.cond"), f.label("repeat.body"), f.label("repeat.next"), \
                f.label("repeat.end")
            f.start(cond)
            i, c = f.t(), f.t()
            f.emit(f"{i} = load i64, ptr {ctr}")
            f.emit(f"{c} = icmp slt i64 {i}, {n}")
            f.term(f"br i1 {c}, label %{body}, label %{done}")
            f.start(body)
            self.loop(step, done, s.body)
            f.start(step)
            i2, nx = f.t(), f.t()
            f.emit(f"{i2} = load i64, ptr {ctr}")
            f.emit(f"{nx} = add i64 {i2}, 1")
            f.emit(f"store i64 {nx}, ptr {ctr}")
            f.term(f"br label %{cond}")
            f.start(done)
        elif isinstance(s, While):
            cond, body, done = f.label("while.cond"), f.label("while.body"), f.label("while.end")
            f.start(cond)
            c = self.expr(s.cond)
            f.term(f"br i1 {c}, label %{body}, label %{done}")
            f.start(body)
            self.loop(cond, done, s.body)
            f.term(f"br label %{cond}")
            f.start(done)
        elif isinstance(s, ForEach):
            items = self.expr(s.items)
            if s.kind == "text":
                items = self.call("ptr", "pl_letters", f"ptr {items}")
            elif s.kind == "map":
                items = self.call("ptr", "pl_map_keys", f"ptr {items}")
            vt = self.T(s.var)
            ctr = f.t().replace("%t", "%each")
            f.alloca(ctr, "i64")
            f.emit(f"store i64 0, ptr {ctr}")
            cond, body, step, done = f.label("each.cond"), f.label("each.body"), f.label("each.next"), \
                f.label("each.end")
            f.start(cond)
            i, n, c = f.t(), f.t(), f.t()
            f.emit(f"{i} = load i64, ptr {ctr}")
            f.emit(f"{n} = call i64 @pl_list_len(ptr {items})")
            f.emit(f"{c} = icmp slt i64 {i}, {n}")
            f.term(f"br i1 {c}, label %{body}, label %{done}")
            f.start(body)
            pos = f.t()
            f.emit(f"{pos} = add i64 {i}, 1")
            v = self.from_slot(self.item_at(items, i), vt)   # i is below the length, checked above
            self.write_target(s.var, v, vt)
            self.loop(step, done, s.body)
            f.start(step)
            i2, nx = f.t(), f.t()
            f.emit(f"{i2} = load i64, ptr {ctr}")
            f.emit(f"{nx} = add i64 {i2}, 1")
            f.emit(f"store i64 {nx}, ptr {ctr}")
            f.term(f"br label %{cond}")
            f.start(done)
        elif isinstance(s, ForRange):
            a, b = self.expr(s.start), self.expr(s.end)
            ctr = f.t().replace("%t", "%count")
            f.alloca(ctr, "i64")
            f.emit(f"store i64 {a}, ptr {ctr}")
            stepv = -1 if s.down else 1
            cond, body, step, done = f.label("count.cond"), f.label("count.body"), f.label("count.next"), \
                f.label("count.end")
            f.start(cond)
            i, c = f.t(), f.t()
            f.emit(f"{i} = load i64, ptr {ctr}")
            f.emit(f"{c} = icmp {'sge' if s.down else 'sle'} i64 {i}, {b}")
            f.term(f"br i1 {c}, label %{body}, label %{done}")
            f.start(body)
            self.write_target(s.var, i, INT)
            self.loop(step, done, s.body)
            f.start(step)
            i2, nx = f.t(), f.t()
            f.emit(f"{i2} = load i64, ptr {ctr}")
            f.emit(f"{nx} = add i64 {i2}, {stepv}")
            f.emit(f"store i64 {nx}, ptr {ctr}")
            f.term(f"br label %{cond}")
            f.start(done)
        elif isinstance(s, If):
            c = self.expr(s.cond)
            yes, no, done = f.label("if.then"), f.label("if.else"), f.label("if.end")
            f.term(f"br i1 {c}, label %{yes}, label %{no}")
            f.start(yes)
            self.block(s.then)
            f.term(f"br label %{done}")
            f.start(no)
            self.block(s.else_ or [])
            f.term(f"br label %{done}")
            f.start(done)
        elif isinstance(s, Break):
            self.leave_tries(f.loops[-1][2])
            f.term(f"br label %{f.loops[-1][1]}")
        elif isinstance(s, Continue):
            self.leave_tries(f.loops[-1][2])
            f.term(f"br label %{f.loops[-1][0]}")
        elif isinstance(s, Try):
            jb = self.call("ptr", "pl_try_push")
            r, c = f.t(), f.t()
            f.emit(f"{r} = call i32 @_setjmp(ptr {jb}) returns_twice")
            f.emit(f"{c} = icmp eq i32 {r}, 0")
            body, failed, done = f.label("try.body"), f.label("try.failed"), f.label("try.end")
            f.term(f"br i1 {c}, label %{body}, label %{failed}")
            f.start(body)
            f.tries += 1
            self.block(s.body)
            f.tries -= 1
            self.call("void", "pl_try_pop")
            f.term(f"br label %{done}")
            f.start(failed)
            self.block(s.handler or [])
            f.term(f"br label %{done}")
            f.start(done)
        elif isinstance(s, Eval):
            self.expr(s.expr)
        elif isinstance(s, Fail):
            self.call("void", "pl_raise", f"ptr {self.glued(s.parts)}")
            f.term("unreachable")
        elif isinstance(s, Exit):
            self.call("void", "pl_exit")
            f.term("unreachable")
        elif isinstance(s, Return):
            value = None
            if s.expr is not None:
                rt = self.types.ret_type(self.sig)
                value = self.conv(self.expr(s.expr), self.T(s.expr), rt)
            self.leave_tries(0)
            if s.expr is None:
                f.term("ret void")
            else:
                f.term(f"ret {llt(rt)} {value}")
        elif isinstance(s, RemoveValue):
            lst = self.expr(s.items)
            el = self.T(s.items).args[0]
            v = self.to_slot(self.conv(self.expr(s.value), self.T(s.value), el), el)
            self.call("void", "pl_list_remove_value", f"ptr {lst}, i64 {v}, ptr {self.desc(el)}")
        elif isinstance(s, RemoveAt):
            self.call("void", "pl_list_remove_at", f"ptr {self.expr(s.items)}, i64 {self.expr(s.index)}")
        elif isinstance(s, RemoveKey):
            m = self.expr(s.table)
            self.call("void", "pl_map_remove", f"ptr {m}, i64 {self.to_slot(self.expr(s.key), self.T(s.key))}")
        elif isinstance(s, Sort):
            lst = self.expr(s.items)
            el = self.T(s.items).args[0]
            if s.key is None:
                self.call("void", "pl_list_sort", f"ptr {lst}, ptr {self.desc(el)}, i32 {int(s.descending)}")
            else:
                keys, kt = self.keys_of(lst, el, s.var, s.key)
                self.call("void", "pl_list_sort_by", f"ptr {lst}, ptr {keys}, ptr {self.desc(kt)}, "
                                                     f"i32 {int(s.descending)}")
        elif isinstance(s, Reverse):
            self.call("void", "pl_list_reverse", f"ptr {self.expr(s.items)}")
        elif isinstance(s, Ask):
            pt = self.T(s.prompt)
            prompt = self.expr(s.prompt)
            if pt.name != "Text":
                prompt = self.call("ptr", "pl_to_text", f"i64 {self.to_slot(prompt, pt)}, ptr {self.desc(pt)}")
            t = self.T(s.target)
            fn, ret = {"Text": ("pl_ask_text", "ptr"), "Int": ("pl_ask_int", "i64"),
                       "Float": ("pl_ask_float", "double")}[t.name]
            self.write_target(s.target, self.call(ret, fn, f"ptr {prompt}"), t)
        elif isinstance(s, CallStmt):
            self.call_fn(s.call)
        else:
            raise EngError(f"internal: no code for {s}")

    # ── functions and the module ──

    def function(self, sig):
        params = self.types.param_types(sig)
        ret = self.types.ret_type(sig) if sig.returns else None
        header = (f"define internal {llt(ret) if ret else 'void'} @plain.{sig.name}(" +
                  ", ".join(f"{llt(t)} %p.{san(n)}" for n, t in zip(sig.params, params)) + ")")
        f = self.f = Fn(self, header)
        f.volatile = any(isinstance(x, Try) for x in walk_stmts(sig.body))
        self.sig = sig
        local_types = {name: t for name, func, t in self.types.variables("local") if func == sig.name}
        for name, t in local_types.items():
            f.alloca(f"%l.{san(name)}", llt(t))
        for name, t in zip(sig.params, params):
            f.emit(f"store {llt(t)} %p.{san(name)}, ptr %l.{san(name)}")
        for name, t in local_types.items():
            if name not in sig.params:
                f.emit(f"store {llt(t)} {self.zero(t)}, ptr %l.{san(name)}")
        self.block(sig.body)
        if ret is None:
            f.term("ret void")
        elif "result" in local_types:
            r = f.t()
            f.emit(f"{r} = load {llt(local_types['result'])}, ptr %l.result")
            f.term(f"ret {llt(ret)} {self.conv(r, local_types['result'], ret)}")
        else:
            f.term(f"ret {llt(ret)} {self.zero(ret)}")
        return f.text()

    def main(self):
        f = self.f = Fn(self, "define i32 @main(i32 %argc, ptr %argv)")
        f.volatile = any(isinstance(x, Try) for x in walk_stmts([s for s in self.prog if not isinstance(s, FuncDef)]))
        self.sig = None
        f.emit("call void @pl_init(i32 %argc, ptr %argv)")
        kinds = list(self.types.kinds.by_name.values()) if self.types.kinds else []
        if kinds:
            rows = []
            for k in kinds:
                fields = self.types.kind_fields(k)
                names = ", ".join(f"ptr {self.string(k.field_display[f])}" for f, _ in fields)
                descs = ", ".join(f"ptr {self.desc(t)}" for _, t in fields)
                self.extra.append(f"@pl.kind{k.id}.fields = private constant [{len(fields)} x ptr] [{names}]")
                self.extra.append(f"@pl.kind{k.id}.descs = private constant [{len(fields)} x ptr] [{descs}]")
                rows.append(f"{{ptr, i32, ptr, ptr}} {{ptr {self.string(k.name)}, i32 {len(fields)}, "
                            f"ptr @pl.kind{k.id}.fields, ptr @pl.kind{k.id}.descs}}")
            self.extra.append(f"@pl.kinds = private constant [{len(rows)} x {{ptr, i32, ptr, ptr}}] [{', '.join(rows)}]")
            f.emit(f"call void @pl_register_kinds(ptr @pl.kinds, i32 {len(rows)})")
        f.emit(f"store ptr {self.string(os.path.basename(self.file))}, ptr @pl_file")
        for name, _, t in self.globals_:
            where = f"%m.{san(name)}" if name in self.main_locals else f"@g.{san(name)}"
            if name in self.main_locals:
                f.alloca(where, llt(t))
            f.emit(f"store {llt(t)} {self.zero(t)}, ptr {where}")
        self.block([s for s in self.prog if not isinstance(s, (Handler, FuncDef))])
        handlers = [s for s in self.prog if isinstance(s, Handler)]
        if handlers:
            self.bot_loop(handlers)
        f.term("ret i32 0")
        return f.text()

    def bot_loop(self, handlers):
        f = self.f
        users = self.types.variables("user")
        names = [f"ptr {self.string(name[len('their.'):])}" for name, _, _ in users]
        descs = [f"ptr {self.desc(t)}" for _, _, t in users]
        self.extra.append(f"@pl.user_names = private constant [{len(names)} x ptr] [{', '.join(names)}]")
        self.extra.append(f"@pl.user_descs = private constant [{len(descs)} x ptr] [{', '.join(descs)}]")
        f.emit(f"call void @pl_bot_start(i32 {len(descs)}, ptr @pl.user_names, ptr @pl.user_descs)")
        persist = next((s for s in self.prog if isinstance(s, Persist)), None)
        if persist is not None:
            rows = [f"{{ptr, ptr, ptr}} {{ptr {self.string(name)}, ptr {self.desc(t)}, ptr @g.{san(name)}}}"
                    for name, _, t in self.globals_]
            self.extra.append(f"@pl.persist = private constant [{len(rows)} x {{ptr, ptr, ptr}}] "
                              f"[{', '.join(rows)}]")
            path = self.expr(persist.path)
            f.emit(f"call void @pl_persist(ptr {path}, ptr @pl.persist, i32 {len(rows)})")
        poll, stop = f.label("bot.poll"), f.label("bot.stop")
        f.start(poll)
        got = self.truthy("pl_bot_next", "")
        first = f.label("rule")
        f.term(f"br i1 {got}, label %{first}, label %{stop}")
        f.start(first)
        for h in handlers:          # the first rule that matches wins
            f.emit(f"store i32 {h.line}, ptr @pl_line")
            c = self.expr(h.cond)
            body, nxt = f.label("rule.body"), f.label("rule")
            f.term(f"br i1 {c}, label %{body}, label %{nxt}")
            f.start(body)
            self.block(h.body)
            f.term(f"br label %{poll}")
            f.start(nxt)
        f.term(f"br label %{poll}")
        f.start(stop)

    def module(self):
        fns = [self.function(s.sig) for s in self.prog if isinstance(s, FuncDef)]
        main = self.main()
        out = ["; generated by engc from Plain source; link with runtime/*.c and -lgc", ""]
        for name, _, t in self.globals_:
            if name in self.main_locals:
                continue
            init = {"Int": "0", "Float": "0.0", "Bool": "false"}.get(t.name, "null")
            out.append(f"@g.{san(name)} = internal global {llt(t)} {init}")
        for s, g in self.strings.items():
            data = s.encode("utf-8") + b"\0"
            enc = "".join(chr(b) if 32 <= b < 127 and chr(b) not in '"\\' else f"\\{b:02X}" for b in data)
            out.append(f'{g} = private unnamed_addr constant [{len(data)} x i8] c"{enc}"')
        out += self.extra
        out += ["", DECLS, ""] + fns + [main, "", "!0 = !{!\"branch_weights\", i32 2000, i32 1}", ""]
        return "\n".join(out)


def var_names(x, seen=None):
    """Names of the variables used anywhere inside a node or a list of them."""
    seen = set() if seen is None else seen
    out = set()
    if isinstance(x, (list, tuple)):
        for y in x:
            out |= var_names(y, seen)
        return out
    if not dataclasses.is_dataclass(x) or isinstance(x, type) or id(x) in seen or type(x).__name__ in ("FuncSig", "KindInfo"):
        return out
    seen.add(id(x))
    if isinstance(x, Var):
        out.add(x.name)
    if isinstance(x, FuncDef):
        out |= var_names(x.sig.body, seen)
    for fl in dataclasses.fields(x):
        out |= var_names(getattr(x, fl.name), seen)
    return out


def walk_all(prog):
    for s in walk_stmts(prog):
        yield s


def emit(prog, funcs, types, file=""):
    return Gen(prog, funcs, types, file).module()


# Link-time optimisation lets LLVM inline the runtime (list access, arithmetic helpers) into the program.
LTO = ["-flto", "-fuse-ld=lld"] if shutil.which("ld.lld") else []


def runtime_objects(bot, net=False):
    """Compiled runtime, cached by the hash of its sources."""
    sources = CORE_SOURCES + (NET_SOURCES if net or bot else []) + (BOT_SOURCES if bot else [])
    h = hashlib.sha256()
    for name in sorted(os.listdir(RUNTIME_DIR)):
        if name.endswith((".c", ".h")):
            with open(os.path.join(RUNTIME_DIR, name), "rb") as f:
                h.update(name.encode() + f.read())
    h.update(" ".join(LTO).encode())
    cache = os.path.join(tempfile.gettempdir(), f"plain-runtime-{h.hexdigest()[:16]}")
    os.makedirs(cache, exist_ok=True)
    objs = []
    for name in sources:
        obj = os.path.join(cache, name[:-2] + ".o")
        if not os.path.exists(obj):
            tmp = obj + f".{os.getpid()}.tmp"
            subprocess.run(["clang", "-O2", *LTO[:1], "-c", "-DPLAIN_TELEGRAM", os.path.join(RUNTIME_DIR, name),
                            "-o", tmp], check=True)
            os.replace(tmp, obj)
        objs.append(obj)
    return objs


def build(prog, funcs, types, file, output):
    ir = emit(prog, funcs, types, file)
    bot = is_bot(prog)
    with tempfile.TemporaryDirectory() as d:
        ll = os.path.join(d, "out.ll")
        with open(ll, "w", encoding="utf-8") as f:
            f.write(ir)
        net = "call ptr @pl_fetch(" in ir or "call ptr @pl_post(" in ir
        libs = ["-lgc", "-lm"] + (["-lcurl"] if bot or net else [])
        subprocess.run(["clang", "-O3", *LTO, "-Wno-override-module", ll] + runtime_objects(bot, net) +
                       ["-o", output] + libs, check=True)
