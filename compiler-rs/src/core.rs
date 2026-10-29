//! Core IR: what every English sentence turns into. Mirrors plainc/core.py, including the text of
//! every node (it decides which readings of a sentence count as the same).

use std::cell::{Cell, RefCell};
use std::fmt;
use std::rc::Rc;
use std::sync::atomic::{AtomicUsize, Ordering};

use indexmap::IndexMap;

use crate::pyfmt::{float_repr, json_str};
use crate::syntax::Tok;

static NEXT_ID: AtomicUsize = AtomicUsize::new(1);

pub type E = Rc<Expr>;
pub type S = Rc<Stmt>;
pub type Sig = Rc<FuncSig>;
pub type Kind = Rc<KindInfo>;
pub type Body = RefCell<Vec<S>>;

#[derive(Clone, Debug)]
pub struct VarRef {
    pub name: String,
    pub display: String,
    pub scope: String, // global | local | user
    pub func: String,  // owning function for locals
}

#[derive(Clone, Debug)]
pub enum Spec {
    Int,
    Float,
    Text,
    Bool,
    Kind(Kind),
    List(Box<Spec>),
    Table(Box<Spec>),
}

pub fn spec_words(spec: &Spec) -> String {
    match spec {
        Spec::List(s) => format!("a list of {}", spec_words(s)),
        Spec::Table(s) => format!("a table of {}", spec_words(s)),
        Spec::Kind(k) => k.name.clone(),
        Spec::Int => "numbers".into(),
        Spec::Float => "decimals".into(),
        Spec::Text => "texts".into(),
        Spec::Bool => "yes/no values".into(),
    }
}

#[derive(Debug)]
pub enum Ex {
    Num(i64),
    Dec(f64),
    Text(String),
    Truth,
    Var(VarRef),
    Bin(String, E, E),
    Cmp { op: String, left: E, right: E, kind: RefCell<String> },
    Logic(String, Vec<E>),
    Random(E, E),
    OneOf(Vec<E>),
    RandomItem(E),
    Concat(Vec<E>),
    ListLit(Vec<E>),
    EmptyMap,
    Index { container: E, index: Option<E>, kind: RefCell<String> },
    Slice(E, E, E),
    Lookup { table: E, key: E, or_zero: bool },
    Builtin(String, Vec<E>),
    Call { func: RefCell<Sig>, args: Vec<E> },
    New(Kind, Vec<(String, E)>),
    Field { obj: E, field: String, display: String, kind: RefCell<Option<Kind>> },
    Nothing,
    FromJson(E, Spec),
    ErrorText,
    Comprehension { var: E, items: E, expr: Option<E>, cond: Option<E> },
    BestBy { var: E, items: E, key: E, largest: bool },
    Msg,
    Name,
    MsgNum,
    IsNum,
}

#[derive(Debug)]
pub struct Expr {
    pub id: usize,
    pub k: Ex,
}

pub fn ex(k: Ex) -> E {
    Rc::new(Expr { id: NEXT_ID.fetch_add(1, Ordering::Relaxed), k })
}

pub fn num(n: i64) -> E {
    ex(Ex::Num(n))
}
pub fn text(s: &str) -> E {
    ex(Ex::Text(s.to_string()))
}
pub fn builtin(name: &str, args: Vec<E>) -> E {
    ex(Ex::Builtin(name.to_string(), args))
}
pub fn bin(op: &str, l: E, r: E) -> E {
    ex(Ex::Bin(op.to_string(), l, r))
}
pub fn cmp(op: &str, l: E, r: E) -> E {
    ex(Ex::Cmp { op: op.to_string(), left: l, right: r, kind: RefCell::new(String::new()) })
}
pub fn logic(op: &str, args: Vec<E>) -> E {
    ex(Ex::Logic(op.to_string(), args))
}
pub fn index(container: E, index: Option<E>) -> E {
    ex(Ex::Index { container, index, kind: RefCell::new(String::new()) })
}
pub fn field(obj: E, f: &str, display: &str) -> E {
    ex(Ex::Field { obj, field: f.to_string(), display: display.to_string(), kind: RefCell::new(None) })
}
pub fn call(sig: Sig, args: Vec<E>) -> E {
    ex(Ex::Call { func: RefCell::new(sig), args })
}
pub fn lookup(table: E, key: E, or_zero: bool) -> E {
    ex(Ex::Lookup { table, key, or_zero })
}

impl Expr {
    pub fn var(&self) -> Option<&VarRef> {
        match &self.k {
            Ex::Var(v) => Some(v),
            _ => None,
        }
    }
}

fn args(xs: &[E]) -> String {
    xs.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(", ")
}

impl fmt::Display for Expr {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        let s = match &self.k {
            Ex::Num(n) => format!("Int({})", n),
            Ex::Dec(x) => format!("Dec({})", float_repr(*x)),
            Ex::Text(s) => format!("Text({})", json_str(s)),
            Ex::Truth => "Always".into(),
            Ex::Var(v) => format!("Var({})", v.name),
            Ex::Bin(op, l, r) => format!("Bin({}, {}, {})", op, l, r),
            Ex::Cmp { op, left, right, .. } => format!("Cmp({}, {}, {})", op, left, right),
            Ex::Logic(op, xs) => {
                let mut c = op.chars();
                let cap: String = c.next().map(|h| h.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default();
                format!("{}({})", cap, args(xs))
            }
            Ex::Random(a, b) => format!("Random({}, {})", a, b),
            Ex::OneOf(xs) => format!("OneOf({})", args(xs)),
            Ex::RandomItem(x) => format!("RandomItem({})", x),
            Ex::Concat(xs) => format!("Concat({})", args(xs)),
            Ex::ListLit(xs) => format!("List({})", args(xs)),
            Ex::EmptyMap => "Table()".into(),
            Ex::Index { container, index, .. } => format!(
                "Item({}, {})",
                index.as_ref().map(|i| i.to_string()).unwrap_or_else(|| "last".into()),
                container
            ),
            Ex::Slice(c, a, b) => format!("Items({}, {}, {})", a, b, c),
            Ex::Lookup { table, key, .. } => format!("Lookup({}, {})", table, key),
            Ex::Builtin(n, xs) => format!("{}({})", n, args(xs)),
            Ex::Call { func, args: xs } => {
                format!("Call({}{}{})", func.borrow().name, if xs.is_empty() { "" } else { ", " }, args(xs))
            }
            Ex::New(k, inits) => {
                let inits: Vec<String> = inits.iter().map(|(k, v)| format!("{}={}", k, v)).collect();
                format!("New({}{}{})", k.name, if inits.is_empty() { "" } else { ", " }, inits.join(", "))
            }
            Ex::Field { obj, field, .. } => format!("Field({}, {})", obj, field),
            Ex::Nothing => "Nothing".into(),
            Ex::FromJson(e, spec) => format!("FromJson({}, {})", e, spec_words(spec)),
            Ex::ErrorText => "Error".into(),
            Ex::Comprehension { var, items, expr, cond } => format!(
                "Each({}, {}{}{})",
                var,
                items,
                expr.as_ref().map(|e| format!(", {}", e)).unwrap_or_default(),
                cond.as_ref().map(|c| format!(", where {}", c)).unwrap_or_default()
            ),
            Ex::BestBy { var, items, key, largest } => {
                format!("{}By({}, {}, {})", if *largest { "Largest" } else { "Smallest" }, var, items, key)
            }
            Ex::Msg => "Message".into(),
            Ex::Name => "SenderName".into(),
            Ex::MsgNum => "MessageNumber".into(),
            Ex::IsNum => "MessageIsNumber".into(),
        };
        f.write_str(&s)
    }
}

// ───────────── kinds and functions ─────────────

#[derive(Debug)]
pub struct KindInfo {
    pub name: String,
    pub display: String,
    pub fields: Vec<String>,                      // field keys in order
    pub field_display: IndexMap<String, String>,  // key -> words as written
    pub id: Cell<usize>,
    pub line: u32,
    pub file: String,
    pub raw_specs: Vec<(String, Vec<Tok>)>,       // types written in brackets, before they are resolved
    pub field_specs: RefCell<IndexMap<String, Spec>>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Pat {
    Lit { kind: String, value: String },
    Param(String),
}

#[derive(Debug)]
pub struct FuncSig {
    pub name: String,
    pub display: String,
    pub pattern: Vec<Pat>,
    pub params: Vec<String>,
    pub returns: bool,
    pub line: u32,
    pub file: String,
    pub body: Body,
    pub uses_bot: Cell<bool>,
    pub param_kinds: IndexMap<String, Kind>,
    #[allow(dead_code)] // kept like plainc: the generic function a copy was made from
    pub origin: Option<Sig>,
}

// ───────────── statements ─────────────

#[derive(Debug)]
pub enum St {
    Assign { target: E, expr: E },
    AddTo { target: E, expr: E, create: bool, kind: RefCell<String> },
    Print(Vec<E>),
    WriteFile { path: E, parts: Vec<E>, append: bool },
    Reply { parts: Vec<E>, buttons: Vec<E> },
    Repeat { count: E, body: Body },
    While { cond: E, body: Body },
    ForEach { var: E, items: E, body: Body, kind: RefCell<String> },
    ForRange { var: E, start: E, end: E, body: Body, down: bool },
    If { cond: E, then: Body, else_: RefCell<Option<Vec<S>>> },
    Break,
    Continue,
    Exit,
    Return(Option<E>),
    RemoveValue { items: E, value: E },
    RemoveAt { items: E, index: E },
    RemoveKey { table: E, key: E },
    DeleteFile(E),
    Sort { items: E, var: Option<E>, key: Option<E>, descending: bool },
    Eval(E),
    Try { body: Body, handler: RefCell<Option<Vec<S>>> },
    Fail(Vec<E>),
    Reverse(E),
    Ask { prompt: E, target: E },
    CallStmt(E),
    Persist(E),
    Handler { cond: E, body: Body },
    FuncDef(Sig),
}

#[derive(Debug)]
pub struct Stmt {
    pub line: u32, // 0: none (a function definition)
    pub file: String,
    pub k: St,
}

pub fn body(v: Vec<S>) -> Body {
    RefCell::new(v)
}

impl Stmt {
    /// The nested statement lists, in the order plainc walks them: body, then, else, handler.
    pub fn bodies(&self) -> Vec<Vec<S>> {
        match &self.k {
            St::Repeat { body, .. }
            | St::While { body, .. }
            | St::ForEach { body, .. }
            | St::ForRange { body, .. }
            | St::Handler { body, .. } => vec![body.borrow().clone()],
            St::If { then, else_, .. } => {
                let mut v = vec![then.borrow().clone()];
                if let Some(e) = else_.borrow().as_ref() {
                    v.push(e.clone());
                }
                v
            }
            St::Try { body, handler } => {
                let mut v = vec![body.borrow().clone()];
                if let Some(h) = handler.borrow().as_ref() {
                    v.push(h.clone());
                }
                v
            }
            _ => vec![],
        }
    }

    pub fn dump(&self, ind: &str) -> Vec<String> {
        let inner = format!("{}  ", ind);
        let sub = |b: &[S]| -> Vec<String> { b.iter().flat_map(|s| s.dump(&inner)).collect() };
        match &self.k {
            St::Assign { target, expr } => vec![format!("{}Assign({}, {})", ind, target, expr)],
            St::AddTo { target, expr, .. } => vec![format!("{}AddTo({}, {})", ind, target, expr)],
            St::Print(parts) => vec![format!("{}Print({})", ind, args(parts))],
            St::WriteFile { path, parts, append } => {
                vec![format!("{}{}({}, {})", ind, if *append { "AppendFile" } else { "WriteFile" }, path, args(parts))]
            }
            St::Reply { parts, buttons } => {
                let b = if buttons.is_empty() { String::new() } else { format!(" Buttons({})", args(buttons)) };
                vec![format!("{}Reply({}){}", ind, args(parts), b)]
            }
            St::Repeat { count, body } => {
                let mut v = vec![format!("{}Repeat({})", ind, count)];
                v.extend(sub(&body.borrow()));
                v
            }
            St::While { cond, body } => {
                let mut v = vec![format!("{}While({})", ind, cond)];
                v.extend(sub(&body.borrow()));
                v
            }
            St::ForEach { var, items, body, .. } => {
                let mut v = vec![format!("{}ForEach({}, {})", ind, var, items)];
                v.extend(sub(&body.borrow()));
                v
            }
            St::ForRange { var, start, end, body, down } => {
                let mut v = vec![format!("{}ForRange({}, {}, {}{})", ind, var, start, end, if *down { ", down" } else { "" })];
                v.extend(sub(&body.borrow()));
                v
            }
            St::If { cond, then, else_ } => {
                let mut v = vec![format!("{}If({})", ind, cond)];
                v.extend(sub(&then.borrow()));
                if let Some(e) = else_.borrow().as_ref() {
                    v.push(format!("{}Else", ind));
                    v.extend(sub(e));
                }
                v
            }
            St::Break => vec![format!("{}Break", ind)],
            St::Continue => vec![format!("{}Continue", ind)],
            St::Exit => vec![format!("{}Exit", ind)],
            St::Return(e) => vec![format!("{}Return({})", ind, e.as_ref().map(|e| e.to_string()).unwrap_or_default())],
            St::RemoveValue { items, value } => vec![format!("{}RemoveValue({}, {})", ind, items, value)],
            St::RemoveAt { items, index } => vec![format!("{}RemoveAt({}, {})", ind, items, index)],
            St::RemoveKey { table, key } => vec![format!("{}RemoveKey({}, {})", ind, table, key)],
            St::DeleteFile(p) => vec![format!("{}DeleteFile({})", ind, p)],
            St::Sort { items, key, descending, .. } => vec![format!(
                "{}Sort({}{}{})",
                ind,
                items,
                key.as_ref().map(|k| format!(", by {}", k)).unwrap_or_default(),
                if *descending { ", descending" } else { "" }
            )],
            St::Eval(e) => vec![format!("{}Do({})", ind, e)],
            St::Try { body, handler } => {
                let mut v = vec![format!("{}Try", ind)];
                v.extend(sub(&body.borrow()));
                if let Some(h) = handler.borrow().as_ref() {
                    v.push(format!("{}OnError", ind));
                    v.extend(sub(h));
                }
                v
            }
            St::Fail(parts) => vec![format!("{}Fail({})", ind, args(parts))],
            St::Reverse(e) => vec![format!("{}Reverse({})", ind, e)],
            St::Ask { prompt, target } => vec![format!("{}Ask({}, {})", ind, prompt, target)],
            St::CallStmt(c) => vec![format!("{}{}", ind, c)],
            St::Persist(p) => vec![format!("{}Persist({})", ind, p)],
            St::Handler { cond, body } => {
                let mut v = vec![format!("{}OnMessage({})", ind, cond)];
                v.extend(sub(&body.borrow()));
                v
            }
            St::FuncDef(sig) => {
                let params = sig.params.join(", ");
                let kind = if sig.returns { "Function" } else { "Command" };
                let mut v = vec![format!("{}{}({}{}{})", ind, kind, sig.name, if params.is_empty() { "" } else { ": " }, params)];
                v.extend(sub(&sig.body.borrow()));
                v
            }
        }
    }
}

pub fn dump_program(prog: &[S]) -> String {
    prog.iter().flat_map(|s| s.dump("")).collect::<Vec<_>>().join("\n")
}

pub fn is_bot(prog: &[S]) -> bool {
    prog.iter().any(|s| matches!(s.k, St::Handler { .. }))
}

/// Every statement, depth first, including nested bodies (and function bodies when with_funcs).
pub fn walk_stmts(body: &[S], with_funcs: bool, out: &mut Vec<S>) {
    for s in body {
        out.push(s.clone());
        for b in s.bodies() {
            walk_stmts(&b, with_funcs, out);
        }
        if with_funcs {
            if let St::FuncDef(sig) = &s.k {
                walk_stmts(&sig.body.borrow(), with_funcs, out);
            }
        }
    }
}

pub fn walk(body: &[S]) -> Vec<S> {
    let mut out = Vec::new();
    walk_stmts(body, true, &mut out);
    out
}

// ───────────── back to English, for messages ─────────────

pub fn op_words(op: &str) -> &'static str {
    match op {
        "+" => "plus",
        "-" => "minus",
        "*" => "times",
        "/" => "divided by",
        "%" => "mod",
        _ => "to the power of",
    }
}

pub fn cmp_words(op: &str) -> &'static str {
    match op {
        ">" => "is more than",
        "<" => "is less than",
        ">=" => "is at least",
        "<=" => "is at most",
        "!=" => "is not",
        "==" | "~=" => "is",
        "contains" => "contains",
        _ => "starts with",
    }
}

pub fn english(e: &Expr, top: bool) -> String {
    let wrap = |s: String| if top { s } else { format!("({})", s) };
    match &e.k {
        Ex::Num(n) => n.to_string(),
        Ex::Dec(x) => float_repr(*x),
        Ex::Text(s) => json_str(s),
        Ex::Var(v) => if v.display.is_empty() { v.name.clone() } else { v.display.clone() },
        Ex::Bin(op, l, r) => wrap(format!("{} {} {}", english(l, false), op_words(op), english(r, false))),
        Ex::Cmp { op, left, right, .. } => format!("{} {} {}", english(left, true), cmp_words(op), english(right, true)),
        Ex::Logic(op, xs) => {
            if op == "not" {
                format!("not ({})", english(&xs[0], true))
            } else {
                xs.iter().map(|a| format!("({})", english(a, true))).collect::<Vec<_>>().join(&format!(" {} ", op))
            }
        }
        Ex::Msg => "the message".into(),
        Ex::Name => "their name".into(),
        Ex::MsgNum => "the number they sent".into(),
        Ex::Random(a, b) => format!("a random number from {} to {}", english(a, true), english(b, true)),
        Ex::OneOf(xs) => format!("one of {}", xs.iter().map(|x| english(x, true)).collect::<Vec<_>>().join(", ")),
        Ex::RandomItem(x) => format!("one of {}", english(x, true)),
        Ex::Concat(xs) => xs.iter().map(|x| english(x, true)).collect::<Vec<_>>().join(" and then "),
        Ex::ListLit(xs) => xs.iter().map(|x| english(x, true)).collect::<Vec<_>>().join(", "),
        Ex::Index { container, index, .. } => match index {
            None => format!("the last item of {}", english(container, true)),
            Some(i) => format!("item {} of {}", english(i, true), english(container, true)),
        },
        Ex::Slice(c, a, b) => format!("items {} to {} of {}", english(a, true), english(b, true), english(c, true)),
        Ex::Lookup { table, key, .. } => format!("{} of {}", english(table, true), english(key, true)),
        Ex::Call { func, args: xs } => {
            let sig = func.borrow();
            let mut out = Vec::new();
            let mut k = 0;
            for el in &sig.pattern {
                match el {
                    Pat::Param(_) => {
                        out.push(english(&xs[k], false));
                        k += 1;
                    }
                    Pat::Lit { value, .. } => out.push(value.clone()),
                }
            }
            wrap(out.join(" "))
        }
        Ex::Builtin(n, xs) => format!(
            "{} of {}",
            n.replace('_', " "),
            xs.iter().map(|a| english(a, false)).collect::<Vec<_>>().join(", ")
        ),
        Ex::Field { obj, field, display, .. } => {
            format!("the {} of {}", if display.is_empty() { field } else { display }, english(obj, false))
        }
        Ex::New(k, _) => format!("a new {}", k.display),
        Ex::Nothing => "nothing".into(),
        Ex::ErrorText => "the error".into(),
        Ex::Comprehension { var, items, expr, cond } => {
            let mut base = format!("every {} in {}", english(var, true), english(items, true));
            if let Some(x) = expr {
                base = format!("{} for {}", english(x, true), base);
            }
            if let Some(c) = cond {
                base = format!("{} where {}", base, english(c, true));
            }
            base
        }
        Ex::BestBy { var, items, key, largest } => format!(
            "the {} in {} with the {} {}",
            english(var, true),
            english(items, true),
            if *largest { "largest" } else { "smallest" },
            english(key, true)
        ),
        _ => e.to_string(),
    }
}

pub fn en(e: &Expr) -> String {
    english(e, true)
}

// ───────────── copies of generic functions ─────────────

/// A fresh copy of a function body for one use of a generic function: new nodes (so new types),
/// the same functions and kinds, local variables moved to the new function's name.
pub fn clone_body(body: &[S], old: &str, new: &str) -> Vec<S> {
    body.iter().map(|s| clone_stmt(s, old, new)).collect()
}

fn ce(e: &E, old: &str, new: &str) -> E {
    let c = |x: &E| ce(x, old, new);
    let cs = |xs: &[E]| xs.iter().map(|x| ce(x, old, new)).collect::<Vec<_>>();
    let co = |x: &Option<E>| x.as_ref().map(|x| ce(x, old, new));
    let k = match &e.k {
        Ex::Num(n) => Ex::Num(*n),
        Ex::Dec(x) => Ex::Dec(*x),
        Ex::Text(s) => Ex::Text(s.clone()),
        Ex::Truth => Ex::Truth,
        Ex::Var(v) => {
            let mut v = v.clone();
            if v.func == old {
                v.func = new.to_string();
            }
            Ex::Var(v)
        }
        Ex::Bin(op, l, r) => Ex::Bin(op.clone(), c(l), c(r)),
        Ex::Cmp { op, left, right, kind } => {
            Ex::Cmp { op: op.clone(), left: c(left), right: c(right), kind: RefCell::new(kind.borrow().clone()) }
        }
        Ex::Logic(op, xs) => Ex::Logic(op.clone(), cs(xs)),
        Ex::Random(a, b) => Ex::Random(c(a), c(b)),
        Ex::OneOf(xs) => Ex::OneOf(cs(xs)),
        Ex::RandomItem(x) => Ex::RandomItem(c(x)),
        Ex::Concat(xs) => Ex::Concat(cs(xs)),
        Ex::ListLit(xs) => Ex::ListLit(cs(xs)),
        Ex::EmptyMap => Ex::EmptyMap,
        Ex::Index { container, index, kind } => {
            Ex::Index { container: c(container), index: co(index), kind: RefCell::new(kind.borrow().clone()) }
        }
        Ex::Slice(a, b, d) => Ex::Slice(c(a), c(b), c(d)),
        Ex::Lookup { table, key, or_zero } => Ex::Lookup { table: c(table), key: c(key), or_zero: *or_zero },
        Ex::Builtin(n, xs) => Ex::Builtin(n.clone(), cs(xs)),
        Ex::Call { func, args } => Ex::Call { func: RefCell::new(func.borrow().clone()), args: cs(args) },
        Ex::New(k, inits) => Ex::New(k.clone(), inits.iter().map(|(f, x)| (f.clone(), c(x))).collect()),
        Ex::Field { obj, field, display, kind } => Ex::Field {
            obj: c(obj),
            field: field.clone(),
            display: display.clone(),
            kind: RefCell::new(kind.borrow().clone()),
        },
        Ex::Nothing => Ex::Nothing,
        Ex::FromJson(x, spec) => Ex::FromJson(c(x), spec.clone()),
        Ex::ErrorText => Ex::ErrorText,
        Ex::Comprehension { var, items, expr, cond } => {
            Ex::Comprehension { var: c(var), items: c(items), expr: co(expr), cond: co(cond) }
        }
        Ex::BestBy { var, items, key, largest } => Ex::BestBy { var: c(var), items: c(items), key: c(key), largest: *largest },
        Ex::Msg => Ex::Msg,
        Ex::Name => Ex::Name,
        Ex::MsgNum => Ex::MsgNum,
        Ex::IsNum => Ex::IsNum,
    };
    ex(k)
}

fn clone_stmt(s: &S, old: &str, new: &str) -> S {
    let c = |x: &E| ce(x, old, new);
    let cs = |xs: &[E]| xs.iter().map(|x| ce(x, old, new)).collect::<Vec<_>>();
    let cb = |b: &Body| body(clone_body(&b.borrow(), old, new));
    let co = |b: &RefCell<Option<Vec<S>>>| RefCell::new(b.borrow().as_ref().map(|v| clone_body(v, old, new)));
    let k = match &s.k {
        St::Assign { target, expr } => St::Assign { target: c(target), expr: c(expr) },
        St::AddTo { target, expr, create, kind } => {
            St::AddTo { target: c(target), expr: c(expr), create: *create, kind: RefCell::new(kind.borrow().clone()) }
        }
        St::Print(p) => St::Print(cs(p)),
        St::WriteFile { path, parts, append } => St::WriteFile { path: c(path), parts: cs(parts), append: *append },
        St::Reply { parts, buttons } => St::Reply { parts: cs(parts), buttons: cs(buttons) },
        St::Repeat { count, body } => St::Repeat { count: c(count), body: cb(body) },
        St::While { cond, body } => St::While { cond: c(cond), body: cb(body) },
        St::ForEach { var, items, body, kind } => {
            St::ForEach { var: c(var), items: c(items), body: cb(body), kind: RefCell::new(kind.borrow().clone()) }
        }
        St::ForRange { var, start, end, body, down } => {
            St::ForRange { var: c(var), start: c(start), end: c(end), body: cb(body), down: *down }
        }
        St::If { cond, then, else_ } => St::If { cond: c(cond), then: cb(then), else_: co(else_) },
        St::Break => St::Break,
        St::Continue => St::Continue,
        St::Exit => St::Exit,
        St::Return(e) => St::Return(e.as_ref().map(c)),
        St::RemoveValue { items, value } => St::RemoveValue { items: c(items), value: c(value) },
        St::RemoveAt { items, index } => St::RemoveAt { items: c(items), index: c(index) },
        St::RemoveKey { table, key } => St::RemoveKey { table: c(table), key: c(key) },
        St::DeleteFile(p) => St::DeleteFile(c(p)),
        St::Sort { items, var, key, descending } => {
            St::Sort { items: c(items), var: var.as_ref().map(c), key: key.as_ref().map(c), descending: *descending }
        }
        St::Eval(e) => St::Eval(c(e)),
        St::Try { body, handler } => St::Try { body: cb(body), handler: co(handler) },
        St::Fail(p) => St::Fail(cs(p)),
        St::Reverse(e) => St::Reverse(c(e)),
        St::Ask { prompt, target } => St::Ask { prompt: c(prompt), target: c(target) },
        St::CallStmt(e) => St::CallStmt(c(e)),
        St::Persist(e) => St::Persist(c(e)),
        St::Handler { cond, body } => St::Handler { cond: c(cond), body: cb(body) },
        St::FuncDef(sig) => St::FuncDef(sig.clone()),
    };
    Rc::new(Stmt { line: s.line, file: s.file.clone(), k })
}

/// Names of the variables used anywhere inside some statements (function bodies included).
pub fn var_names(body: &[S], out: &mut std::collections::HashSet<String>) {
    for s in walk(body) {
        for e in stmt_exprs(&s) {
            expr_var_names(&e, out);
        }
    }
}

fn expr_var_names(e: &E, out: &mut std::collections::HashSet<String>) {
    if let Ex::Var(v) = &e.k {
        out.insert(v.name.clone());
    }
    for c in children(e) {
        expr_var_names(&c, out);
    }
}

/// The expressions directly inside an expression.
pub fn children(e: &E) -> Vec<E> {
    match &e.k {
        Ex::Bin(_, l, r) | Ex::Random(l, r) => vec![l.clone(), r.clone()],
        Ex::Cmp { left, right, .. } => vec![left.clone(), right.clone()],
        Ex::Logic(_, xs) | Ex::OneOf(xs) | Ex::Concat(xs) | Ex::ListLit(xs) | Ex::Builtin(_, xs) => xs.clone(),
        Ex::RandomItem(x) | Ex::FromJson(x, _) => vec![x.clone()],
        Ex::Index { container, index, .. } => {
            let mut v = vec![container.clone()];
            v.extend(index.clone());
            v
        }
        Ex::Slice(a, b, c) => vec![a.clone(), b.clone(), c.clone()],
        Ex::Lookup { table, key, .. } => vec![table.clone(), key.clone()],
        Ex::Call { args, .. } => args.clone(),
        Ex::New(_, inits) => inits.iter().map(|(_, x)| x.clone()).collect(),
        Ex::Field { obj, .. } => vec![obj.clone()],
        Ex::Comprehension { var, items, expr, cond } => {
            let mut v = vec![var.clone(), items.clone()];
            v.extend(expr.clone());
            v.extend(cond.clone());
            v
        }
        Ex::BestBy { var, items, key, .. } => vec![var.clone(), items.clone(), key.clone()],
        _ => vec![],
    }
}

/// The expressions directly inside a statement (not inside its nested statements).
pub fn stmt_exprs(s: &Stmt) -> Vec<E> {
    match &s.k {
        St::Assign { target, expr } | St::AddTo { target, expr, .. } => vec![target.clone(), expr.clone()],
        St::Print(p) | St::Fail(p) => p.clone(),
        St::WriteFile { path, parts, .. } => {
            let mut v = vec![path.clone()];
            v.extend(parts.iter().cloned());
            v
        }
        St::Reply { parts, buttons } => parts.iter().chain(buttons.iter()).cloned().collect(),
        St::Repeat { count: x, .. } | St::While { cond: x, .. } | St::If { cond: x, .. } | St::Handler { cond: x, .. } => {
            vec![x.clone()]
        }
        St::ForEach { var, items, .. } => vec![var.clone(), items.clone()],
        St::ForRange { var, start, end, .. } => vec![var.clone(), start.clone(), end.clone()],
        St::Return(e) => e.iter().cloned().collect(),
        St::RemoveValue { items: a, value: b } | St::RemoveAt { items: a, index: b } | St::RemoveKey { table: a, key: b } => {
            vec![a.clone(), b.clone()]
        }
        St::DeleteFile(x) | St::Eval(x) | St::Reverse(x) | St::CallStmt(x) | St::Persist(x) => vec![x.clone()],
        St::Sort { items, var, key, .. } => {
            let mut v = vec![items.clone()];
            v.extend(var.clone());
            v.extend(key.clone());
            v
        }
        St::Ask { prompt, target } => vec![prompt.clone(), target.clone()],
        _ => vec![],
    }
}
