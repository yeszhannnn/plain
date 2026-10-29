"""Runs Core IR directly. This is the reference: native binaries must print the same."""

import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request

from .core import (AddTo, Ask, Assign, Bin, Break, Builtin, Call, CallStmt, Cmp, Concat, Continue, Dec, DeleteFile,
                   EmptyMap, Exit, ForEach, ForRange, FuncDef, Handler, If, Index, IsNum, ListLit, Logic, Lookup,
                   Msg, MsgNum, Name, Num, OneOf, Persist, Print, Random, RandomItem, RemoveAt, RemoveKey,
                   RemoveValue, Repeat, Reply, Return, Reverse, Slice, Sort, Text, Truth, Var, While, WriteFile,
                   english)
from .errors import EngError
from .types import prune
from .values import (DEC_RE, INT_RE, fmt, join_parts, lines_of, lower, text_same, upper, words_of, wrap)


class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass


class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value


class ExitSignal(Exception):
    pass


def zero(t):
    return {"Int": 0, "Float": 0.0, "Text": "", "Bool": False}.get(t.name) if t.name not in ("List", "Map") else \
        ([] if t.name == "List" else {})


def coerce(v, t):
    if t.name == "Float" and isinstance(v, int) and not isinstance(v, bool):
        return float(v)
    return v


class Telegram:
    """Bot API client. Same behaviour as runtime/telegram.c."""

    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise EngError("Set TELEGRAM_BOT_TOKEN to your bot's token first. "
                           "You get one from @BotFather in Telegram.")
        self.base = (os.environ.get("TELEGRAM_API_URL") or "https://api.telegram.org").rstrip("/")
        self.idle_exit = "PLAIN_BOT_EXIT_WHEN_IDLE" in os.environ
        self.offset = 0
        self.pending = []
        print("The bot is running. Press Ctrl+C to stop.", file=sys.stderr)

    def call(self, method, payload):
        req = urllib.request.Request(f"{self.base}/bot{self.token}/{method}",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            data = json.load(e)
        except (urllib.error.URLError, OSError) as e:
            print(f"Network problem while talking to Telegram: {e}", file=sys.stderr)
            return None
        if not data.get("ok"):
            raise EngError(f"Telegram refused the request: {data.get('description', 'unknown error')}")
        return data

    def next(self):
        while True:
            while self.pending:
                u = self.pending.pop(0)
                self.offset = max(self.offset, u.get("update_id", 0) + 1)
                m = u.get("message") or {}
                if not isinstance(m.get("text"), str) or "id" not in m.get("chat", {}):
                    continue
                name = (m.get("from") or {}).get("first_name", "")
                print(f"[{name}] {m['text']}", file=sys.stderr)
                return m["chat"]["id"], m["text"], name
            data = self.call("getUpdates", {"offset": self.offset, "timeout": 0 if self.idle_exit else 30,
                                            "allowed_updates": ["message"]})
            if data is None:
                if self.idle_exit:
                    return None
                time.sleep(3)
                continue
            self.pending = list(data.get("result", []))
            if self.idle_exit and not self.pending:
                return None

    def send(self, chat, text, buttons):
        payload = {"chat_id": chat, "text": text or " "}
        if buttons:
            rows = [[{"text": b} for b in buttons[i:i + 3]] for i in range(0, len(buttons), 3)]
            payload["reply_markup"] = {"resize_keyboard": True, "keyboard": rows}
        self.call("sendMessage", payload)


class Interpreter:
    def __init__(self, prog, types, argv=(), out=sys.stdout, inp=sys.stdin):
        self.prog = prog
        self.types = types
        self.argv = list(argv)
        self.out = out
        self.inp = inp
        self.line, self.file = 0, ""
        self.globals = {name: zero(t) for name, _, t in types.variables("global")}
        self.user_types = {name: t for name, _, t in types.variables("user")}
        self.local_types = {}
        for name, func, t in types.variables("local"):
            self.local_types.setdefault(func, {})[name] = t
        self.frames = []
        self.users = {}
        self.ctx = {}
        self.bot = None

    def fail(self, msg):
        raise EngError(msg, self.line, self.file)

    def T(self, e):
        return self.types.of(e)

    # ── storage ──

    def scope(self, v):
        if v.scope == "global":
            return self.globals
        if v.scope == "local":
            return self.frames[-1]
        if self.ctx.get("chat") not in self.users:
            self.users[self.ctx["chat"]] = {n: zero(t) for n, t in self.user_types.items()}
        return self.users[self.ctx["chat"]]

    def get_index(self, c, i, kind):
        n = len(c)
        if i is None:
            if not n:
                self.fail("There is no last item: the list is empty." if kind == "list" else
                          "There is no last letter: the text is empty.")
            return c[-1]
        if i < 1 or i > n:
            self.fail(f"There is no item {i}: the list has {n} item{'' if n == 1 else 's'}." if kind == "list" else
                      f"There is no letter {i}: the text has {n} letter{'' if n == 1 else 's'}.")
        return c[i - 1]

    def lookup(self, e, create=False):
        table, key = self.ev(e.table), self.ev(e.key)
        if key in table:
            return table[key]
        if e.or_zero or create:
            table[key] = zero(self.T(e))
            return table[key]
        shown = f'"{key}"' if isinstance(key, str) else str(key)
        self.fail(f"There is no {english(e.table)} of {shown}.")

    def store(self, target, value):
        t = self.T(target)
        value = coerce(value, t)
        if isinstance(target, Var):
            self.scope(target)[target.name] = value
        elif isinstance(target, Index):
            c = self.ev(target.container)
            i = self.ev(target.index) if target.index is not None else len(c)
            self.get_index(c, i, "list")
            c[i - 1] = value
        else:
            self.ev(target.table)[self.ev(target.key)] = value

    # ── expressions ──

    def ev(self, e):
        if isinstance(e, (Num, Dec, Text)):
            return e.value
        if isinstance(e, Truth):
            return True
        if isinstance(e, Var):
            return self.scope(e)[e.name]
        if isinstance(e, Msg):
            return self.ctx["text"]
        if isinstance(e, Name):
            return self.ctx["name"]
        if isinstance(e, MsgNum):
            m = INT_RE.fullmatch(self.ctx["text"])
            return int(m.group(1)) if m else 0
        if isinstance(e, IsNum):
            return INT_RE.fullmatch(self.ctx["text"]) is not None
        if isinstance(e, Random):
            lo, hi = sorted((self.ev(e.low), self.ev(e.high)))
            return random.randint(lo, hi)
        if isinstance(e, OneOf):
            return coerce(self.ev(random.choice(e.items)), self.T(e))
        if isinstance(e, RandomItem):
            items = self.ev(e.items)
            if not items:
                self.fail("I can't pick from an empty list.")
            return random.choice(items)
        if isinstance(e, Concat):
            return join_parts(fmt(self.ev(x), self.T(x)) for x in e.parts)
        if isinstance(e, ListLit):
            el = self.T(e).args[0]
            return [coerce(self.ev(x), el) for x in e.items]
        if isinstance(e, EmptyMap):
            return {}
        if isinstance(e, Bin):
            return self.arith(e)
        if isinstance(e, Cmp):
            return self.compare(e)
        if isinstance(e, Logic):
            if e.op == "not":
                return not self.ev(e.args[0])
            vals = [self.ev(a) for a in e.args]
            return all(vals) if e.op == "and" else any(vals)
        if isinstance(e, Index):
            c = self.ev(e.container)
            return self.get_index(c, self.ev(e.index) if e.index is not None else None, e.kind)
        if isinstance(e, Slice):
            c = self.ev(e.container)
            a, b = max(self.ev(e.start), 1), min(self.ev(e.end), len(c))
            return c[a - 1:b] if a <= b else c[:0]
        if isinstance(e, Lookup):
            return self.lookup(e)
        if isinstance(e, Call):
            return self.call(e)
        if isinstance(e, Builtin):
            return self.builtin(e)
        self.fail(f"internal: unknown expression {e}")

    def arith(self, e):
        a, b = self.ev(e.left), self.ev(e.right)
        if self.T(e).name == "Float":
            a, b = float(a), float(b)
            if e.op in ("/", "%") and b == 0:
                self.fail("Division by zero.")
            return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a / b,
                    "%": lambda: math.fmod(a, b), "^": lambda: self.fpow(a, b)}[e.op]()
        if e.op in ("/", "%") and b == 0:
            self.fail("Division by zero.")
        if e.op == "/":
            return self.idiv(a, b)
        if e.op == "%":
            return a - b * self.idiv(a, b)
        if e.op == "^":
            if b < 0:
                self.fail("A whole number to a negative power is a fraction. Use a decimal, for example 2.0.")
            r = 1
            for _ in range(min(b, 64) if a in (0, 1, -1) else b):
                r = wrap(r * a)
            return r
        return wrap({"+": a + b, "-": a - b, "*": a * b}[e.op])

    def fpow(self, a, b):
        try:
            return math.pow(a, b)
        except (OverflowError, ValueError):
            return float("nan") if a < 0 else float("inf")

    @staticmethod
    def idiv(a, b):
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q

    def compare(self, e):
        a, b = self.ev(e.left), self.ev(e.right)
        if e.op == "~=":
            return text_same(a, b)
        if e.op == "contains":
            if e.kind == "text":
                return lower(b) in lower(a)
            return b in a
        if e.op == "starts":
            return lower(a).startswith(lower(b))
        return {">": lambda: a > b, "<": lambda: a < b, ">=": lambda: a >= b, "<=": lambda: a <= b,
                "==": lambda: a == b, "!=": lambda: a != b}[e.op]()

    def builtin(self, e):
        n = e.name
        args = [self.ev(a) for a in e.args]
        a = args[0] if args else None
        if n == "length":
            return len(a)
        if n == "sum":
            return sum(a, 0.0 if self.T(e).name == "Float" else 0)
        if n == "average":
            if not a:
                self.fail("There is no average of an empty list.")
            return sum(float(x) for x in a) / len(a)
        if n in ("largest", "smallest"):
            if not a:
                self.fail(f"There is no {n} item in an empty list.")
            return max(a) if n == "largest" else min(a)
        if n == "sqrt":
            if a < 0:
                self.fail("I can't take the square root of a negative number.")
            return math.sqrt(a)
        if n == "abs":
            return abs(a)
        if n == "squared":
            return a * a if isinstance(a, float) else wrap(a * a)
        if n == "round":
            return a if isinstance(a, int) else int(math.copysign(math.floor(abs(a) + 0.5), a))
        if n == "round_up":
            return a if isinstance(a, int) else math.ceil(a)
        if n == "round_down":
            return a if isinstance(a, int) else math.floor(a)
        if n == "upper":
            return upper(a)
        if n == "lower":
            return lower(a)
        if n == "trim":
            return a.strip(" \t\r\n")
        if n == "words":
            return words_of(a)
        if n == "lines":
            return lines_of(a)
        if n == "letters":
            return list(a)
        if n == "split":
            if not args[1]:
                self.fail("I can't split by an empty text.")
            return a.split(args[1])
        if n == "join":
            el = self.T(e.args[0]).args[0]
            return args[1].join(fmt(x, el) for x in a)
        if n == "replace":
            if not args[1]:
                return a
            return a.replace(args[1], args[2])
        if n == "to_int":
            if isinstance(a, str):
                m = INT_RE.fullmatch(a)
                if not m:
                    self.fail(f'"{a}" is not a whole number.')
                return int(m.group(1))
            return int(a)
        if n == "to_float":
            if isinstance(a, str):
                m = DEC_RE.fullmatch(a)
                if not m:
                    self.fail(f'"{a}" is not a number.')
                return float(m.group(1))
            return float(a)
        if n == "to_text":
            return fmt(a, self.T(e.args[0]))
        if n == "is_number":
            return DEC_RE.fullmatch(a) is not None
        if n == "file_text":
            try:
                with open(a, encoding="utf-8", newline="") as f:
                    return f.read()
            except (OSError, UnicodeDecodeError):
                self.fail(f'I can\'t read the file "{a}".')
        if n == "file_exists":
            return os.path.isfile(a)
        if n == "arguments":
            return list(self.argv)
        if n == "known":
            return args[1] in a
        self.fail(f"internal: unknown builtin {n}")

    def call(self, e):
        sig = e.func
        frame = {name: zero(t) for name, t in self.local_types.get(sig.name, {}).items()}
        for name, x, t in zip(sig.params, e.args, self.types.param_types(sig)):
            frame[name] = coerce(self.ev(x), t)
        self.frames.append(frame)
        saved = self.line, self.file
        try:
            self.run_block(sig.body)
            value = frame.get("result")
        except ReturnSignal as r:
            value = r.value
        finally:
            self.frames.pop()
            self.line, self.file = saved
        if sig.returns:
            t = self.types.ret_type(sig)
            return zero(t) if value is None else coerce(value, t)
        return None

    # ── statements ──

    def run_block(self, body):
        for s in body:
            self.line, self.file = getattr(s, "line", self.line), getattr(s, "file", self.file)
            self.stmt(s)

    def loop_body(self, body):
        try:
            self.run_block(body)
        except ContinueSignal:
            pass

    def stmt(self, s):
        if isinstance(s, Assign):
            self.store(s.target, self.ev(s.expr))
        elif isinstance(s, AddTo):
            self.add_to(s)
        elif isinstance(s, Print):
            self.out.write(join_parts(fmt(self.ev(p), self.T(p)) for p in s.parts) + "\n")
            self.out.flush()
        elif isinstance(s, WriteFile):
            path = self.ev(s.path)
            text = join_parts(fmt(self.ev(p), self.T(p)) for p in s.parts)
            if not text.endswith("\n"):
                text += "\n"
            try:
                with open(path, "a" if s.append else "w", encoding="utf-8", newline="") as f:
                    f.write(text)
            except OSError:
                self.fail(f'I can\'t write to the file "{path}".')
        elif isinstance(s, DeleteFile):
            path = self.ev(s.path)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    self.fail(f'I can\'t delete the file "{path}".')
        elif isinstance(s, Reply):
            text = join_parts(fmt(self.ev(p), self.T(p)) for p in s.parts)
            self.bot.send(self.ctx["chat"], text, [self.ev(b) for b in s.buttons])
        elif isinstance(s, Repeat):
            try:
                for _ in range(self.ev(s.count)):
                    self.loop_body(s.body)
            except BreakSignal:
                pass
        elif isinstance(s, While):
            try:
                while self.ev(s.cond):
                    self.loop_body(s.body)
            except BreakSignal:
                pass
        elif isinstance(s, ForEach):
            items = self.ev(s.items)
            if s.kind == "map":
                items = list(items.keys())
            elif s.kind == "text":
                items = list(items)
            try:
                i = 0
                while i < len(items):
                    self.store(s.var, items[i])
                    self.loop_body(s.body)
                    i += 1
            except BreakSignal:
                pass
        elif isinstance(s, ForRange):
            a, b = self.ev(s.start), self.ev(s.end)
            step = 1 if a <= b else -1
            try:
                for v in range(a, b + step, step):
                    self.store(s.var, v)
                    self.loop_body(s.body)
            except BreakSignal:
                pass
        elif isinstance(s, If):
            self.run_block(s.then if self.ev(s.cond) else (s.else_ or []))
        elif isinstance(s, Break):
            raise BreakSignal()
        elif isinstance(s, Continue):
            raise ContinueSignal()
        elif isinstance(s, Exit):
            raise ExitSignal()
        elif isinstance(s, Return):
            raise ReturnSignal(self.ev(s.expr) if s.expr is not None else None)
        elif isinstance(s, RemoveValue):
            items, v = self.ev(s.items), self.ev(s.value)
            if v in items:
                items.remove(v)
        elif isinstance(s, RemoveAt):
            items, i = self.ev(s.items), self.ev(s.index)
            self.get_index(items, i, "list")
            del items[i - 1]
        elif isinstance(s, RemoveKey):
            self.ev(s.table).pop(self.ev(s.key), None)
        elif isinstance(s, Sort):
            self.ev(s.items).sort()
        elif isinstance(s, Reverse):
            self.ev(s.items).reverse()
        elif isinstance(s, Ask):
            self.store(s.target, self.ask(self.ev(s.prompt), self.T(s.target)))
        elif isinstance(s, CallStmt):
            self.call(s.call)
        elif isinstance(s, (Persist, FuncDef, Handler)):
            pass
        else:
            self.fail(f"internal: unknown statement {s}")

    def add_to(self, s):
        t = s.target
        if s.kind == "list":
            items = self.lookup(t, create=True) if isinstance(t, Lookup) else self.ev(t)
            items.append(coerce(self.ev(s.expr), self.T(t).args[0]))
            return
        current = self.lookup(t, create=True) if isinstance(t, Lookup) else self.ev(t)
        v = self.ev(s.expr)
        if s.kind == "text":
            self.store(t, current + v)
        elif isinstance(current, float) or isinstance(v, float):
            self.store(t, float(current) + float(v))
        else:
            self.store(t, wrap(current + v))

    def ask(self, prompt, t):
        while True:
            text = prompt if not prompt or prompt[-1] in " \n" else prompt + " "
            self.out.write(text)
            self.out.flush()
            line = self.inp.readline()
            if not line:
                self.fail("There is no more input to read.")
            line = line.rstrip("\n").rstrip("\r")
            if t.name == "Text":
                return line
            m = (INT_RE if t.name == "Int" else DEC_RE).fullmatch(line)
            if m:
                return int(m.group(1)) if t.name == "Int" else float(m.group(1))
            self.out.write("Please type a whole number.\n" if t.name == "Int" else "Please type a number.\n")

    # ── whole program ──

    def run(self):
        try:
            self.run_block([s for s in self.prog if not isinstance(s, (Handler, FuncDef))])
            handlers = [s for s in self.prog if isinstance(s, Handler)]
            if handlers:
                self.serve(handlers)
        except ExitSignal:
            pass
        finally:
            self.out.flush()

    def serve(self, handlers):
        persist = next((s for s in self.prog if isinstance(s, Persist)), None)
        path = self.ev(persist.path) if persist else None
        if path:
            self.load(path)
        self.bot = Telegram()
        while True:
            msg = self.bot.next()
            if msg is None:
                return
            self.ctx["chat"], self.ctx["text"], self.ctx["name"] = msg
            for h in handlers:          # the first rule that matches wins
                self.line, self.file = h.line, h.file
                if self.ev(h.cond):
                    self.run_block(h.body)
                    break
            if path:
                self.save(path)

    # ── bot memory on disk: same JSON as runtime/telegram.c ──

    def save(self, path):
        def enc(v, t):
            if t.name == "List":
                return [enc(x, t.args[0]) for x in v]
            if t.name == "Map":
                return {str(k): enc(x, t.args[1]) for k, x in v.items()}
            return v
        data = {"globals": {n: enc(self.globals[n], t) for n, _, t in self.types.variables("global")},
                "users": {str(chat): {n[len("their."):]: enc(v, self.user_types[n]) for n, v in vals.items()}
                          for chat, vals in self.users.items()}}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)

    def load(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return

        def dec(v, t):
            if t.name == "List":
                return [dec(x, t.args[0]) for x in v] if isinstance(v, list) else []
            if t.name == "Map":
                if not isinstance(v, dict):
                    return {}
                key = int if t.args[0].name == "Int" else str
                out = {}
                for k, x in v.items():
                    try:
                        out[key(k)] = dec(x, t.args[1])
                    except ValueError:
                        pass
                return out
            if t.name == "Float":
                return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
            if t.name == "Int":
                return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
            if t.name == "Bool":
                return v if isinstance(v, bool) else False
            return v if isinstance(v, str) else ""
        for n, _, t in self.types.variables("global"):
            if n in data.get("globals", {}):
                self.globals[n] = dec(data["globals"][n], t)
        for chat, vals in data.get("users", {}).items():
            try:
                chat_id = int(chat)
            except ValueError:
                continue
            user = {n: zero(t) for n, t in self.user_types.items()}
            for n, t in self.user_types.items():
                short = n[len("their."):]
                if short in vals:
                    user[n] = dec(vals[short], t)
            self.users[chat_id] = user


def run(prog, types, argv=(), out=sys.stdout, inp=sys.stdin):
    Interpreter(prog, types, argv, out, inp).run()
