//! Core IR -> LLVM IR text, linked with the runtime (and Boehm GC) into a native executable.
//! Mirrors plainc/llvm.py and produces the same text.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::Command;

use indexmap::IndexMap;

use crate::core::*;
use crate::pyfmt::basename;
use crate::syntax::{EngError, Res};
use crate::types::{float, text_t, Ty, Types};

const DECLS: &str = include_str!("../../runtime/declarations.llvm"); // shared with plainc

pub const RUNTIME: [(&str, &str); 10] = [
    ("core.c", include_str!("../../runtime/core.c")),
    ("text.c", include_str!("../../runtime/text.c")),
    ("list.c", include_str!("../../runtime/list.c")),
    ("map.c", include_str!("../../runtime/map.c")),
    ("files.c", include_str!("../../runtime/files.c")),
    ("json.c", include_str!("../../runtime/json.c")),
    ("net.c", include_str!("../../runtime/net.c")),
    ("telegram.c", include_str!("../../runtime/telegram.c")),
    ("json.h", include_str!("../../runtime/json.h")),
    ("plain_rt.h", include_str!("../../runtime/plain_rt.h")),
];
const CORE_SOURCES: [&str; 6] = ["core.c", "text.c", "list.c", "map.c", "files.c", "json.c"];

fn llt(t: &Ty) -> &'static str {
    let n = t.name();
    if n.starts_with("Kind:") {
        return "ptr";
    }
    match n {
        "Int" => "i64",
        "Float" => "double",
        "Bool" => "i1",
        "Nothing" => "void",
        _ => "ptr",
    }
}

fn san(name: &str) -> String {
    name.chars().map(|c| if c.is_alphanumeric() || c == '.' || c == '_' { c } else { '_' }).collect()
}

fn fconst(x: f64) -> String {
    format!("0x{:016X}", x.to_bits())
}

struct Fn {
    header: String,
    lines: Vec<String>,
    allocas: Vec<String>,
    tmp: usize,
    terminated: bool,
    loops: Vec<(String, String, usize)>, // (continue label, break label, "try" depth at the loop)
    tries: usize,                        // how many "try" blocks the code being emitted is inside
    volatile: bool,                      // the function has "try": keep variables in memory across longjmp
}

impl Fn {
    fn new(header: String) -> Fn {
        Fn { header, lines: vec![], allocas: vec![], tmp: 0, terminated: false, loops: vec![], tries: 0, volatile: false }
    }
    fn text(&self) -> String {
        let mut v = vec![format!("{} {{", self.header), "entry:".to_string()];
        v.extend(self.allocas.iter().cloned());
        v.extend(self.lines.iter().cloned());
        v.push("}".into());
        v.join("\n")
    }
}

struct Gen<'a> {
    prog: Vec<S>,
    types: &'a Types,
    file: String,
    strings: IndexMap<String, String>,
    lbl: usize,
    f: Fn,
    users: HashMap<String, usize>,
    globals: Vec<(String, String, Ty)>,
    main_locals: HashSet<String>,
    builders: HashSet<(String, String)>,
    extra: Vec<String>,
    one_file: bool,
    sig: Option<Sig>,
}

impl<'a> Gen<'a> {
    fn new(prog: &[S], types: &'a Types, file: &str) -> Gen<'a> {
        let all = walk(prog);
        let users = types.variables("user").into_iter().enumerate().map(|(k, (name, _, _))| (name, k)).collect();
        let globals = types.variables("global");
        let mut builders = HashSet::new();
        for s in &all {
            if let St::AddTo { target, kind, .. } = &s.k {
                if kind.borrow().as_str() == "text" {
                    if let Some(v) = target.var() {
                        builders.insert((v.scope.clone(), v.name.clone()));
                    }
                }
            }
        }
        let files: HashSet<String> = all.iter().map(|s| basename(if s.file.is_empty() { file } else { &s.file })).collect();
        let mut g = Gen {
            prog: prog.to_vec(),
            types,
            file: file.to_string(),
            strings: IndexMap::new(),
            lbl: 0,
            f: Fn::new(String::new()),
            users,
            globals,
            main_locals: HashSet::new(),
            builders,
            extra: vec![],
            one_file: files.len() <= 1, // then the file name is set once, at start-up
            sig: None,
        };
        g.main_locals = g.private_globals();
        g
    }

    /// Top-level variables that no function and no bot rule touches: they become locals of main,
    /// so LLVM can keep them in registers.
    fn private_globals(&self) -> HashSet<String> {
        if self.prog.iter().any(|s| matches!(s.k, St::Handler { .. } | St::Persist(_))) {
            return HashSet::new();
        }
        let mut used = HashSet::new();
        for s in &self.prog {
            if let St::FuncDef(sig) = &s.k {
                var_names(&sig.body.borrow(), &mut used);
            }
        }
        self.globals.iter().map(|g| g.0.clone()).filter(|n| !used.contains(n)).collect()
    }

    // ── emitting ──

    fn t(&mut self) -> String {
        self.f.tmp += 1;
        format!("%t{}", self.f.tmp)
    }

    fn label(&mut self, hint: &str) -> String {
        self.lbl += 1;
        format!("{}.{}", hint, self.lbl)
    }

    fn emit(&mut self, s: impl Into<String>) {
        let mut s = s.into();
        if self.f.terminated {
            let l = self.label("dead");
            self.start(&l);
        }
        if self.f.volatile {
            if s.contains(" = load ") {
                s = s.replacen(" = load ", " = load volatile ", 1);
            } else if let Some(rest) = s.strip_prefix("store ") {
                s = format!("store volatile {}", rest);
            }
        }
        self.f.lines.push(format!("  {}", s));
    }

    fn start(&mut self, label: &str) {
        if !self.f.terminated {
            self.f.lines.push(format!("  br label %{}", label));
        }
        self.f.lines.push(format!("{}:", label));
        self.f.terminated = false;
    }

    fn term(&mut self, s: impl Into<String>) {
        self.emit(s);
        self.f.terminated = true;
    }

    fn alloca(&mut self, name: &str, ty: &str) {
        self.f.allocas.push(format!("  {} = alloca {}", name, ty));
    }

    // ── helpers ──

    fn ty(&self, e: &Expr) -> Ty {
        self.types.of(e)
    }

    fn string(&mut self, s: &str) -> String {
        if let Some(g) = self.strings.get(s) {
            return g.clone();
        }
        let g = format!("@.s{}", self.strings.len());
        self.strings.insert(s.to_string(), g.clone());
        g
    }

    fn call(&mut self, ret: &str, func: &str, args: &str) -> String {
        if ret == "void" {
            self.emit(format!("call void @{}({})", func, args));
            return String::new();
        }
        let r = self.t();
        self.emit(format!("{} = call {} @{}({})", r, ret, func, args));
        r
    }

    fn truthy(&mut self, func: &str, args: &str) -> String {
        let r = self.call("i32", func, args);
        let b = self.t();
        self.emit(format!("{} = icmp ne i32 {}, 0", b, r));
        b
    }

    fn conv(&mut self, v: String, src: &Ty, dst: &Ty) -> String {
        if src.name() == "Int" && dst.name() == "Float" {
            let r = self.t();
            self.emit(format!("{} = sitofp i64 {} to double", r, v));
            return r;
        }
        v
    }

    fn to_slot(&mut self, v: String, t: &Ty) -> String {
        if t.name() == "Int" {
            return v;
        }
        let r = self.t();
        match t.name() {
            "Float" => self.emit(format!("{} = bitcast double {} to i64", r, v)),
            "Bool" => self.emit(format!("{} = zext i1 {} to i64", r, v)),
            _ => self.emit(format!("{} = ptrtoint ptr {} to i64", r, v)),
        }
        r
    }

    fn from_slot(&mut self, v: String, t: &Ty) -> String {
        if t.name() == "Int" {
            return v;
        }
        let r = self.t();
        match t.name() {
            "Float" => self.emit(format!("{} = bitcast i64 {} to double", r, v)),
            "Bool" => self.emit(format!("{} = trunc i64 {} to i1", r, v)),
            _ => self.emit(format!("{} = inttoptr i64 {} to ptr", r, v)),
        }
        r
    }

    fn zero(&mut self, t: &Ty) -> String {
        match t.name() {
            "Int" => "0".into(),
            "Float" => fconst(0.0),
            "Bool" => "false".into(),
            "Text" => self.string(""),
            n if n.starts_with("Kind:") => "null".into(),
            "List" => {
                let d = self.desc(t.arg(0));
                self.call("ptr", "pl_list_of", &format!("ptr {}", d))
            }
            _ => {
                let text_keys = t.arg(0).name() == "Text";
                self.call("ptr", "pl_map_new", &format!("i32 {}", if text_keys { 1 } else { 0 }))
            }
        }
    }

    fn desc(&mut self, t: &Ty) -> String {
        let d = self.types.desc(t);
        self.string(&d)
    }

    fn addr(&mut self, v: &VarRef) -> String {
        match v.scope.as_str() {
            "global" => {
                if self.main_locals.contains(&v.name) {
                    format!("%m.{}", san(&v.name))
                } else {
                    format!("@g.{}", san(&v.name))
                }
            }
            "local" => format!("%l.{}", san(&v.name)),
            _ => {
                let k = self.users[&v.name];
                self.call("ptr", "pl_user_slot", &format!("i32 {}", k))
            }
        }
    }

    // ── expressions ──

    /// share=false: the value is only looked at right away (printed, compared, measured), so a text
    /// that is being built in place may stay unshared.
    fn expr(&mut self, e: &E) -> Res<String> {
        self.expr_s(e, true)
    }

    fn expr_s(&mut self, e: &E, share: bool) -> Res<String> {
        let t = self.ty(e);
        Ok(match &e.k {
            Ex::Num(n) => n.to_string(),
            Ex::Dec(x) => fconst(*x),
            Ex::Text(s) => self.string(s),
            Ex::Truth => "true".into(),
            Ex::Var(v) => {
                let a = self.addr(v);
                let r = self.t();
                self.emit(format!("{} = load {}, ptr {}", r, llt(&t), a));
                if share && self.builders.contains(&(v.scope.clone(), v.name.clone())) {
                    self.call("void", "pl_text_share", &format!("ptr {}", r));
                }
                r
            }
            Ex::Msg => self.call("ptr", "pl_msg_text", ""),
            Ex::Name => self.call("ptr", "pl_msg_name", ""),
            Ex::MsgNum => self.call("i64", "pl_msg_number", ""),
            Ex::IsNum => self.truthy("pl_msg_is_number", ""),
            Ex::Random(lo, hi) => {
                let (lo, hi) = (self.expr(lo)?, self.expr(hi)?);
                self.call("i64", "pl_random", &format!("i64 {}, i64 {}", lo, hi))
            }
            Ex::OneOf(items) => {
                let mut vals = Vec::new();
                for x in items {
                    let v = self.expr(x)?;
                    let xt = self.ty(x);
                    vals.push(self.conv(v, &xt, &t));
                }
                let idx = self.call("i64", "pl_random", &format!("i64 0, i64 {}", vals.len() - 1));
                let mut acc = vals[vals.len() - 1].clone();
                for k in (0..vals.len() - 1).rev() {
                    let (c, r) = (self.t(), self.t());
                    self.emit(format!("{} = icmp eq i64 {}, {}", c, idx, k));
                    self.emit(format!("{} = select i1 {}, {} {}, {} {}", r, c, llt(&t), vals[k], llt(&t), acc));
                    acc = r;
                }
                acc
            }
            Ex::RandomItem(items) => {
                let l = self.expr(items)?;
                let s = self.call("i64", "pl_list_random", &format!("ptr {}", l));
                self.from_slot(s, &t)
            }
            Ex::Concat(parts) => {
                let mut vals = Vec::new();
                for x in parts {
                    let xt = self.ty(x);
                    vals.push((xt, self.expr_s(x, false)?));
                }
                self.call("void", "pl_cat_begin", "");
                for (pt, v) in vals {
                    self.out_value("cat", v, &pt);
                }
                self.call("ptr", "pl_cat_end", "")
            }
            Ex::ListLit(items) => {
                let el = t.arg(0).clone();
                let d = self.desc(&el);
                let lst = self.call("ptr", "pl_list_of", &format!("ptr {}", d));
                for x in items {
                    let v = self.expr(x)?;
                    let xt = self.ty(x);
                    let v = self.conv(v, &xt, &el);
                    let v = self.to_slot(v, &el);
                    self.call("void", "pl_list_add", &format!("ptr {}, i64 {}", lst, v));
                }
                lst
            }
            Ex::EmptyMap => self.zero(&t),
            Ex::Bin(..) => self.arith(e, &t)?,
            Ex::Cmp { .. } => self.compare(e)?,
            Ex::Logic(..) => self.logic(e)?,
            Ex::Index { container, index, kind } => {
                let c = self.expr(container)?;
                let i = match index {
                    Some(i) => self.expr(i)?,
                    None => "-1".into(),
                };
                if kind.borrow().as_str() == "text" {
                    return Ok(self.call("ptr", "pl_text_char", &format!("ptr {}, i64 {}", c, i)));
                }
                if index.is_none() {
                    let s = self.call("i64", "pl_list_get", &format!("ptr {}, i64 {}", c, i));
                    return Ok(self.from_slot(s, &t));
                }
                let r = self.t();
                let p = self.item_ptr(&c, &i);
                self.emit(format!("{} = load i64, ptr {}", r, p));
                self.from_slot(r, &t)
            }
            Ex::Slice(c, a, b) => {
                let (c, a, b) = (self.expr(c)?, self.expr(a)?, self.expr(b)?);
                let func = if t.name() == "Text" { "pl_text_slice" } else { "pl_list_slice" };
                self.call("ptr", func, &format!("ptr {}, i64 {}, i64 {}", c, a, b))
            }
            Ex::Lookup { or_zero, .. } => {
                let s = self.lookup_slot(e, *or_zero)?;
                self.from_slot(s, &t)
            }
            Ex::Call { .. } => self.call_fn(e)?,
            Ex::Builtin(..) => self.builtin(e, &t)?,
            Ex::Nothing => "null".into(),
            Ex::ErrorText => self.call("ptr", "pl_error_text", ""),
            Ex::FromJson(x, _) => {
                let v = self.expr(x)?;
                let d = self.desc(&t);
                let s = self.call("i64", "pl_from_json", &format!("ptr {}, ptr {}", v, d));
                self.from_slot(s, &t)
            }
            Ex::Comprehension { var, items, expr, cond } => {
                let it = self.expr(items)?;
                let el = self.ty(items).arg(0).clone();
                let out_t = t.arg(0).clone();
                let d = self.desc(&out_t);
                let out = self.call("ptr", "pl_list_of", &format!("ptr {}", d));
                let (var, expr, cond) = (var.clone(), expr.clone(), cond.clone());
                let out2 = out.clone();
                self.each_item(&it, &el, &var.clone(), &mut |g: &mut Gen, nxt: &str| -> Res<()> {
                    if let Some(c) = &cond {
                        let cv = g.expr(c)?;
                        let take = g.label("each.take");
                        g.term(format!("br i1 {}, label %{}, label %{}", cv, take, nxt));
                        g.start(&take);
                    }
                    let v = match &expr {
                        Some(x) => {
                            let v = g.expr(x)?;
                            let xt = g.ty(x);
                            g.conv(v, &xt, &out_t)
                        }
                        None => g.expr(&var)?,
                    };
                    let v = g.to_slot(v, &out_t);
                    g.call("void", "pl_list_add", &format!("ptr {}, i64 {}", out2, v));
                    Ok(())
                })?;
                out
            }
            Ex::BestBy { var, items, key, largest } => {
                let it = self.expr(items)?;
                let (keys, kt) = self.keys_of(&it, &t, var, key)?;
                let d = self.desc(&kt);
                let best = self.call("i64", "pl_list_best", &format!("ptr {}, ptr {}, ptr {}, i32 {}", it, keys, d, *largest as i32));
                self.from_slot(best, &t)
            }
            Ex::Field { obj, field, kind, .. } => {
                let o = self.expr(obj)?;
                let k = kind.borrow().clone().unwrap();
                let fi = k.fields.iter().position(|f| f == field).unwrap();
                let s = self.call("i64", "pl_obj_get", &format!("ptr {}, i32 {}, i32 {}", o, k.id.get(), fi));
                self.from_slot(s, &t)
            }
            Ex::New(kind, inits) => {
                let o = self.call("ptr", "pl_obj_new", &format!("i32 {}", kind.id.get()));
                let fields: HashMap<String, Ty> = self.types.kind_fields(kind).into_iter().collect();
                for (key, x) in inits {
                    let v = self.expr(x)?;
                    let xt = self.ty(x);
                    let v = self.conv(v, &xt, &fields[key]);
                    let fi = kind.fields.iter().position(|f| f == key).unwrap();
                    let s = self.to_slot(v, &fields[key]);
                    self.call("void", "pl_obj_set", &format!("ptr {}, i32 {}, i32 {}, i64 {}", o, kind.id.get(), fi, s));
                }
                o
            }
        })
    }

    fn lookup_slot(&mut self, e: &E, create: bool) -> Res<String> {
        let (table, key) = match &e.k {
            Ex::Lookup { table, key, .. } => (table, key),
            _ => unreachable!(),
        };
        let m = self.expr(table)?;
        let kv = self.expr(key)?;
        let kt = self.ty(key);
        let k = self.to_slot(kv, &kt);
        if create {
            let et = self.ty(e);
            let d = self.desc(&et);
            return Ok(self.call("i64", "pl_map_get_or_new", &format!("ptr {}, i64 {}, ptr {}", m, k, d)));
        }
        let name = self.string(&en(table));
        Ok(self.call("i64", "pl_map_get", &format!("ptr {}, i64 {}, ptr {}", m, k, name)))
    }

    fn arith(&mut self, e: &E, t: &Ty) -> Res<String> {
        let (op, l, r) = match &e.k {
            Ex::Bin(op, l, r) => (op.as_str(), l, r),
            _ => unreachable!(),
        };
        let (a, b) = (self.expr(l)?, self.expr(r)?);
        let res = self.t();
        if t.name() == "Float" {
            let (lt, rt) = (self.ty(l), self.ty(r));
            let a = self.conv(a, &lt, &float());
            let b = self.conv(b, &rt, &float());
            if let Some(ins) = match op {
                "+" => Some("fadd"),
                "-" => Some("fsub"),
                "*" => Some("fmul"),
                _ => None,
            } {
                self.emit(format!("{} = {} double {}, {}", res, ins, a, b));
                return Ok(res);
            }
            let func = match op {
                "/" => "pl_fdiv",
                "%" => "pl_fmod",
                _ => "pl_fpow",
            };
            return Ok(self.call("double", func, &format!("double {}, double {}", a, b)));
        }
        if let Some(ins) = match op {
            "+" => Some("add"),
            "-" => Some("sub"),
            "*" => Some("mul"),
            _ => None,
        } {
            self.emit(format!("{} = {} i64 {}, {}", res, ins, a, b));
            return Ok(res);
        }
        let func = match op {
            "/" => "pl_div",
            "%" => "pl_rem",
            _ => "pl_ipow",
        };
        Ok(self.call("i64", func, &format!("i64 {}, i64 {}", a, b)))
    }

    fn compare(&mut self, e: &E) -> Res<String> {
        let (op, left, right, kind) = match &e.k {
            Ex::Cmp { op, left, right, kind } => (op.as_str(), left, right, kind.borrow().clone()),
            _ => unreachable!(),
        };
        let (lt, rt) = (self.ty(left), self.ty(right));
        let a = self.expr_s(left, false)?;
        let b = self.expr_s(right, false)?;
        if op == "~=" {
            return Ok(self.truthy("pl_text_same", &format!("ptr {}, ptr {}", a, b)));
        }
        if op == "starts" {
            return Ok(self.truthy("pl_starts", &format!("ptr {}, ptr {}", a, b)));
        }
        if op == "contains" {
            if kind == "text" {
                return Ok(self.truthy("pl_contains", &format!("ptr {}, ptr {}", a, b)));
            }
            if kind == "list" {
                let el = lt.arg(0).clone();
                let v = self.conv(b, &rt, &el);
                let v = self.to_slot(v, &el);
                let d = self.desc(&el);
                return Ok(self.truthy("pl_list_contains", &format!("ptr {}, i64 {}, ptr {}", a, v, d)));
            }
            let v = self.to_slot(b, &rt);
            return Ok(self.truthy("pl_map_has", &format!("ptr {}, i64 {}", a, v)));
        }
        let r = self.t();
        if (lt.name() == "Int" || lt.name() == "Float") && (lt.name() == "Float" || rt.name() == "Float") {
            let a = self.conv(a, &lt, &float());
            let b = self.conv(b, &rt, &float());
            let pred = match op {
                ">" => "ogt",
                "<" => "olt",
                ">=" => "oge",
                "<=" => "ole",
                "==" => "oeq",
                _ => "une",
            };
            self.emit(format!("{} = fcmp {} double {}, {}", r, pred, a, b));
            return Ok(r);
        }
        let pred = match op {
            ">" => "sgt",
            "<" => "slt",
            ">=" => "sge",
            "<=" => "sle",
            "==" => "eq",
            _ => "ne",
        };
        if lt.name() == "Text" {
            let c = self.call("i32", "pl_text_cmp", &format!("ptr {}, ptr {}", a, b));
            self.emit(format!("{} = icmp {} i32 {}, 0", r, pred, c));
            return Ok(r);
        }
        if lt.name().starts_with("Kind:") {
            self.emit(format!("{} = icmp {} ptr {}, {}", r, pred, a, b));
            return Ok(r);
        }
        if lt.name() == "List" || lt.name() == "Map" {
            return Err(EngError::new("Lists and tables can't be compared with \"is\". Compare their items instead.", 0, ""));
        }
        self.emit(format!("{} = icmp {} {} {}, {}", r, pred, llt(&lt), a, b));
        Ok(r)
    }

    fn logic(&mut self, e: &E) -> Res<String> {
        let (op, args) = match &e.k {
            Ex::Logic(op, args) => (op.as_str(), args),
            _ => unreachable!(),
        };
        let first = self.expr(&args[0])?;
        if op == "not" {
            let r = self.t();
            self.emit(format!("{} = xor i1 {}, true", r, first));
            return Ok(r);
        }
        let slot = self.t().replace("%t", "%logic");
        self.alloca(&slot, "i1");
        self.emit(format!("store i1 {}, ptr {}", first, slot));
        let done = self.label("logic.end");
        for a in &args[1..] {
            let more = self.label("logic.next");
            let cur = self.t();
            self.emit(format!("{} = load i1, ptr {}", cur, slot));
            if op == "and" {
                self.term(format!("br i1 {}, label %{}, label %{}", cur, more, done));
            } else {
                self.term(format!("br i1 {}, label %{}, label %{}", cur, done, more));
            }
            self.start(&more);
            let v = self.expr(a)?;
            self.emit(format!("store i1 {}, ptr {}", v, slot));
        }
        self.start(&done);
        let r = self.t();
        self.emit(format!("{} = load i1, ptr {}", r, slot));
        Ok(r)
    }

    fn call_fn(&mut self, e: &E) -> Res<String> {
        let (func, args) = match &e.k {
            Ex::Call { func, args } => (func.borrow().clone(), args),
            _ => unreachable!(),
        };
        let pts = self.types.param_types(&func);
        let mut out = Vec::new();
        for (x, pt) in args.iter().zip(pts.iter()) {
            let v = self.expr(x)?;
            let xt = self.ty(x);
            let v = self.conv(v, &xt, pt);
            out.push(format!("{} {}", llt(pt), v));
        }
        let ret = if func.returns { llt(&self.types.ret_type(&func)) } else { "void" };
        Ok(self.call(ret, &format!("plain.{}", func.name), &out.join(", ")))
    }

    fn builtin(&mut self, e: &E, t: &Ty) -> Res<String> {
        let (n, args) = match &e.k {
            Ex::Builtin(n, args) => (n.as_str(), args),
            _ => unreachable!(),
        };
        let ts: Vec<Ty> = args.iter().map(|a| self.ty(a)).collect();
        let mut vs = Vec::new();
        for a in args {
            vs.push(self.expr_s(a, n != "length")?);
        }
        let a = vs.first().cloned().unwrap_or_default();
        let at = ts.first().cloned();
        let atn = at.as_ref().map(|t| t.name().to_string()).unwrap_or_default();
        let fl = float();
        Ok(match n {
            "length" => {
                let func = match atn.as_str() {
                    "Text" => "pl_text_len",
                    "List" => "pl_list_len",
                    _ => "pl_map_len",
                };
                self.call("i64", func, &format!("ptr {}", a))
            }
            "sum" => {
                if at.as_ref().unwrap().arg(0).name() == "Float" {
                    self.call("double", "pl_list_sum_float", &format!("ptr {}", a))
                } else {
                    self.call("i64", "pl_list_sum_int", &format!("ptr {}", a))
                }
            }
            "average" => {
                let d = self.desc(at.as_ref().unwrap().arg(0));
                self.call("double", "pl_list_average", &format!("ptr {}, ptr {}", a, d))
            }
            "largest" | "smallest" => {
                let d = self.desc(at.as_ref().unwrap().arg(0));
                let s = self.call("i64", "pl_list_extreme", &format!("ptr {}, ptr {}, i32 {}", a, d, if n == "largest" { 1 } else { 0 }));
                self.from_slot(s, t)
            }
            "sqrt" => {
                let v = self.conv(a, at.as_ref().unwrap(), &fl);
                self.call("double", "pl_sqrt", &format!("double {}", v))
            }
            "abs" => {
                if atn == "Float" {
                    return Ok(self.call("double", "llvm.fabs.f64", &format!("double {}", a)));
                }
                let (neg, c, r) = (self.t(), self.t(), self.t());
                self.emit(format!("{} = sub i64 0, {}", neg, a));
                self.emit(format!("{} = icmp slt i64 {}, 0", c, a));
                self.emit(format!("{} = select i1 {}, i64 {}, i64 {}", r, c, neg, a));
                r
            }
            "squared" => {
                let r = self.t();
                self.emit(format!("{} = {} {}, {}", r, if atn == "Float" { "fmul double" } else { "mul i64" }, a, a));
                r
            }
            "round" | "round_up" | "round_down" => {
                if atn == "Int" {
                    return Ok(a);
                }
                let func = match n {
                    "round" => "pl_round",
                    "round_up" => "pl_ceil",
                    _ => "pl_floor",
                };
                self.call("i64", func, &format!("double {}", a))
            }
            "upper" | "lower" | "trim" | "words" | "lines" | "letters" => self.call("ptr", &format!("pl_{}", n), &format!("ptr {}", a)),
            "split" | "replace" => {
                let s = vs.iter().map(|v| format!("ptr {}", v)).collect::<Vec<_>>().join(", ");
                self.call("ptr", &format!("pl_{}", n), &s)
            }
            "join" => {
                let d = self.desc(at.as_ref().unwrap().arg(0));
                self.call("ptr", "pl_join", &format!("ptr {}, ptr {}, ptr {}", a, d, vs[1]))
            }
            "to_int" => match atn.as_str() {
                "Text" => self.call("i64", "pl_text_to_int", &format!("ptr {}", a)),
                "Float" => {
                    let r = self.t();
                    self.emit(format!("{} = fptosi double {} to i64", r, a));
                    r
                }
                _ => a,
            },
            "to_float" => {
                if atn == "Text" {
                    return Ok(self.call("double", "pl_text_to_float", &format!("ptr {}", a)));
                }
                self.conv(a, at.as_ref().unwrap(), &fl)
            }
            "to_text" => {
                if atn == "Text" {
                    return Ok(a);
                }
                let at = at.unwrap();
                let s = self.to_slot(a, &at);
                let d = self.desc(&at);
                self.call("ptr", "pl_to_text", &format!("i64 {}, ptr {}", s, d))
            }
            "is_number" => self.truthy("pl_text_is_number", &format!("ptr {}", a)),
            "file_text" => self.call("ptr", "pl_file_read", &format!("ptr {}", a)),
            "file_exists" => self.truthy("pl_file_exists", &format!("ptr {}", a)),
            "arguments" => self.call("ptr", "pl_arguments", ""),
            "known" => {
                let s = self.to_slot(vs[1].clone(), &ts[1]);
                self.truthy("pl_map_has", &format!("ptr {}, i64 {}", a, s))
            }
            "now" => {
                let id = self.types.kinds()["moment"].id.get();
                self.call("ptr", "pl_now", &format!("i32 {}", id))
            }
            "timestamp" | "millis" | "exit_code" => self.call("i64", &format!("pl_{}", n), ""),
            "sin" | "cos" | "tan" => {
                let v = self.conv(a, at.as_ref().unwrap(), &fl);
                self.call("double", n, &format!("double {}", v))
            }
            "log" => {
                let v = self.conv(a, at.as_ref().unwrap(), &fl);
                self.call("double", "pl_log", &format!("double {}", v))
            }
            "random_float" => {
                let lo = self.conv(vs[0].clone(), &ts[0], &fl);
                let hi = self.conv(vs[1].clone(), &ts[1], &fl);
                self.call("double", "pl_random_float", &format!("double {}, double {}", lo, hi))
            }
            "code" => self.call("i64", "pl_code", &format!("ptr {}", a)),
            "char" => self.call("ptr", "pl_char", &format!("i64 {}", a)),
            "env" | "fetch" | "run_output" => self.call("ptr", &format!("pl_{}", n), &format!("ptr {}", a)),
            "run" => self.call("i64", "pl_run", &format!("ptr {}", a)),
            "position" => self.call("i64", "pl_position", &format!("ptr {}, ptr {}", a, vs[1])),
            "pad" | "pad_left" => {
                let text = if atn == "Text" {
                    a
                } else {
                    let at = at.unwrap();
                    let s = self.to_slot(a, &at);
                    let d = self.desc(&at);
                    self.call("ptr", "pl_to_text", &format!("i64 {}, ptr {}", s, d))
                };
                self.call("ptr", "pl_pad", &format!("ptr {}, i64 {}, i32 {}", text, vs[1], (n == "pad_left") as i32))
            }
            "repeat_text" => self.call("ptr", "pl_repeat_text", &format!("ptr {}, i64 {}", a, vs[1])),
            "decimals" => {
                let v = self.conv(a, at.as_ref().unwrap(), &fl);
                self.call("ptr", "pl_decimals", &format!("double {}, i64 {}", v, vs[1]))
            }
            "to_json" => {
                let at = at.unwrap();
                let s = self.to_slot(a, &at);
                let d = self.desc(&at);
                self.call("ptr", "pl_to_json", &format!("i64 {}, ptr {}", s, d))
            }
            "post" => {
                let at = at.unwrap();
                let body = if atn == "Text" {
                    a
                } else {
                    let s = self.to_slot(a, &at);
                    let d = self.desc(&at);
                    self.call("ptr", "pl_to_json", &format!("i64 {}, ptr {}", s, d))
                };
                self.call("ptr", "pl_post", &format!("ptr {}, ptr {}, i32 {}", body, vs[1], if atn == "Text" { 2 } else { 1 }))
            }
            "wait" => {
                let amount = self.conv(a, at.as_ref().unwrap(), &fl);
                let ms = self.t();
                let scale = match &args[1].k {
                    Ex::Num(n) => *n as f64,
                    _ => 1.0,
                };
                self.emit(format!("{} = fmul double {}, {}", ms, amount, fconst(scale)));
                self.call("void", "pl_wait", &format!("double {}", ms));
                "0".into()
            }
            _ => return Err(EngError::new(format!("internal: no code for {}", n), 0, "")),
        })
    }

    /// Append one value to the output line ("out") or to glued text ("cat").
    fn out_value(&mut self, kind: &str, v: String, t: &Ty) {
        match t.name() {
            "Int" => {
                self.call("void", &format!("pl_{}_int", kind), &format!("i64 {}", v));
            }
            "Float" => {
                self.call("void", &format!("pl_{}_float", kind), &format!("double {}", v));
            }
            "Text" => {
                self.call("void", &format!("pl_{}_text", kind), &format!("ptr {}", v));
            }
            "Bool" => {
                let r = self.t();
                let (yes, no) = (self.string("true"), self.string("false"));
                self.emit(format!("{} = select i1 {}, ptr {}, ptr {}", r, v, yes, no));
                self.call("void", &format!("pl_{}_text", kind), &format!("ptr {}", r));
            }
            _ => {
                let s = self.to_slot(v, t);
                let d = self.desc(t);
                self.call("void", &format!("pl_{}_value", kind), &format!("i64 {}, ptr {}", s, d));
            }
        }
    }

    fn glued(&mut self, parts: &[E]) -> Res<String> {
        let mut vals = Vec::new();
        for p in parts {
            let pt = self.ty(p);
            vals.push((pt, self.expr(p)?));
        }
        self.call("void", "pl_cat_begin", "");
        for (t, v) in vals {
            self.out_value("cat", v, &t);
        }
        Ok(self.call("ptr", "pl_cat_end", ""))
    }

    // ── statements ──

    fn write_target(&mut self, target: &E, v: String, vt: &Ty) -> Res<()> {
        let t = self.ty(target);
        let v = self.conv(v, vt, &t);
        match &target.k {
            Ex::Var(var) => {
                let a = self.addr(var);
                self.emit(format!("store {} {}, ptr {}", llt(&t), v, a));
            }
            Ex::Field { obj, field, kind, .. } => {
                let o = self.expr(obj)?;
                let k = kind.borrow().clone().unwrap();
                let fi = k.fields.iter().position(|f| f == field).unwrap();
                let s = self.to_slot(v, &t);
                self.call("void", "pl_obj_set", &format!("ptr {}, i32 {}, i32 {}, i64 {}", o, k.id.get(), fi, s));
            }
            Ex::Index { container, index, .. } => {
                let c = self.expr(container)?;
                match index {
                    None => {
                        let s = self.to_slot(v, &t);
                        self.call("void", "pl_list_set", &format!("ptr {}, i64 -1, i64 {}", c, s));
                    }
                    Some(i) => {
                        let slot = self.to_slot(v, &t);
                        let iv = self.expr(i)?;
                        let p = self.item_ptr(&c, &iv);
                        self.emit(format!("store i64 {}, ptr {}", slot, p));
                    }
                }
            }
            Ex::Lookup { table, key, .. } => {
                let m = self.expr(table)?;
                let kv = self.expr(key)?;
                let kt = self.ty(key);
                let k = self.to_slot(kv, &kt);
                let s = self.to_slot(v, &t);
                self.call("void", "pl_map_set", &format!("ptr {}, i64 {}, i64 {}", m, k, s));
            }
            _ => unreachable!(),
        }
        Ok(())
    }

    /// Item k (counted from 0) of a list, already known to be there.
    fn item_at(&mut self, lst: &str, k: &str) -> String {
        let (pi, items, p, r) = (self.t(), self.t(), self.t(), self.t());
        self.emit(format!("{} = getelementptr inbounds i8, ptr {}, i64 16", pi, lst));
        self.emit(format!("{} = load ptr, ptr {}", items, pi));
        self.emit(format!("{} = getelementptr inbounds i64, ptr {}, i64 {}", p, items, k));
        self.emit(format!("{} = load i64, ptr {}", r, p));
        r
    }

    /// Address of item i (counted from 1) of a list, after checking that the item is there.
    fn item_ptr(&mut self, lst: &str, i: &str) -> String {
        let (k, n, ok, items, p) = (self.t(), self.t(), self.t(), self.t(), self.t());
        self.emit(format!("{} = sub i64 {}, 1", k, i));
        self.emit(format!("{} = load i64, ptr {}", n, lst));
        self.emit(format!("{} = icmp ult i64 {}, {}", ok, k, n));
        let (good, bad) = (self.label("item.ok"), self.label("item.bad"));
        self.term(format!("br i1 {}, label %{}, label %{}, !prof !0", ok, good, bad));
        self.start(&bad);
        self.emit(format!("call void @pl_list_bad_index(ptr {}, i64 {})", lst, i));
        self.term("unreachable");
        self.start(&good);
        let pi = self.t();
        self.emit(format!("{} = getelementptr inbounds i8, ptr {}, i64 16", pi, lst));
        self.emit(format!("{} = load ptr, ptr {}", items, pi));
        self.emit(format!("{} = getelementptr inbounds i64, ptr {}, i64 {}", p, items, k));
        p
    }

    fn read_target(&mut self, target: &E) -> Res<String> {
        if let Ex::Lookup { .. } = &target.k {
            let s = self.lookup_slot(target, true)?;
            let t = self.ty(target);
            return Ok(self.from_slot(s, &t));
        }
        self.expr(target)
    }

    fn block(&mut self, body: &[S]) -> Res<()> {
        for s in body {
            if matches!(s.k, St::FuncDef(_) | St::Handler { .. } | St::Persist(_)) {
                continue;
            }
            if s.line > 0 {
                self.emit(format!("store i32 {}, ptr @pl_line", s.line));
                if !self.one_file {
                    let f = basename(if s.file.is_empty() { &self.file } else { &s.file });
                    let g = self.string(&f);
                    self.emit(format!("store ptr {}, ptr @pl_file", g));
                }
            }
            self.stmt(s)?;
        }
        Ok(())
    }

    fn lp(&mut self, cont: &str, brk: &str, body: &Body) -> Res<()> {
        self.f.loops.push((cont.to_string(), brk.to_string(), self.f.tries));
        let b = body.borrow().clone();
        self.block(&b)?;
        self.f.loops.pop();
        Ok(())
    }

    /// Jumping out of "try" blocks: forget their error handlers.
    fn leave_tries(&mut self, depth: usize) {
        for _ in 0..self.f.tries.saturating_sub(depth) {
            self.call("void", "pl_try_pop", "");
        }
    }

    /// Go through a list (as it was at the start); body(next_label) is emitted for every item.
    fn each_item(&mut self, items: &str, el: &Ty, var: &E, body: &mut dyn FnMut(&mut Gen, &str) -> Res<()>) -> Res<()> {
        let n = self.call("i64", "pl_list_len", &format!("ptr {}", items));
        let ctr = self.t().replace("%t", "%item");
        self.alloca(&ctr, "i64");
        self.emit(format!("store i64 0, ptr {}", ctr));
        let (cond, step, done) = (self.label("item.cond"), self.label("item.next"), self.label("item.end"));
        self.start(&cond);
        let (i, c) = (self.t(), self.t());
        self.emit(format!("{} = load i64, ptr {}", i, ctr));
        self.emit(format!("{} = icmp slt i64 {}, {}", c, i, n));
        let body_label = self.label("item.body");
        self.term(format!("br i1 {}, label %{}, label %{}", c, body_label, done));
        self.start(&body_label);
        let pos = self.t();
        self.emit(format!("{} = add i64 {}, 1", pos, i));
        let got = self.t();
        let p = self.item_ptr(items, &pos); // the list may have shrunk meanwhile
        self.emit(format!("{} = load i64, ptr {}", got, p));
        let item = self.from_slot(got, el);
        self.write_target(var, item, el)?;
        body(self, &step)?;
        self.start(&step);
        let (i2, nx) = (self.t(), self.t());
        self.emit(format!("{} = load i64, ptr {}", i2, ctr));
        self.emit(format!("{} = add i64 {}, 1", nx, i2));
        self.emit(format!("store i64 {}, ptr {}", nx, ctr));
        self.term(format!("br label %{}", cond));
        self.start(&done);
        Ok(())
    }

    /// A list with the key of every item, for sorting and for "with the largest".
    fn keys_of(&mut self, items: &str, el: &Ty, var: &E, key: &E) -> Res<(String, Ty)> {
        let kt = self.ty(key);
        let d = self.desc(&kt);
        let keys = self.call("ptr", "pl_list_of", &format!("ptr {}", d));
        let (key2, kt2, keys2) = (key.clone(), kt.clone(), keys.clone());
        self.each_item(items, el, var, &mut |g: &mut Gen, _nxt: &str| -> Res<()> {
            let k = g.expr(&key2)?;
            let s = g.to_slot(k, &kt2);
            g.call("void", "pl_list_add", &format!("ptr {}, i64 {}", keys2, s));
            Ok(())
        })?;
        Ok((keys, kt))
    }

    fn counter_loop(&mut self, prefix: &str, init: &str) -> String {
        let ctr = self.t().replace("%t", prefix);
        self.alloca(&ctr, "i64");
        self.emit(format!("store i64 {}, ptr {}", init, ctr));
        ctr
    }

    fn advance(&mut self, ctr: &str, step: i64, cond: &str) {
        let (i2, nx) = (self.t(), self.t());
        self.emit(format!("{} = load i64, ptr {}", i2, ctr));
        self.emit(format!("{} = add i64 {}, {}", nx, i2, step));
        self.emit(format!("store i64 {}, ptr {}", nx, ctr));
        self.term(format!("br label %{}", cond));
    }

    fn stmt(&mut self, s: &S) -> Res<()> {
        match &s.k {
            St::Assign { target, expr } => {
                let v = self.expr(expr)?;
                let et = self.ty(expr);
                self.write_target(target, v, &et)?;
            }
            St::AddTo { target, expr, kind, .. } => {
                let (t, vt) = (self.ty(target), self.ty(expr));
                let kind = kind.borrow().clone();
                if kind == "list" {
                    let lst = self.read_target(target)?;
                    let v = self.expr(expr)?;
                    let el = t.arg(0).clone();
                    let v = self.conv(v, &vt, &el);
                    let v = self.to_slot(v, &el);
                    self.call("void", "pl_list_add", &format!("ptr {}, i64 {}", lst, v));
                    return Ok(());
                }
                if kind == "text" && target.var().is_some() {
                    // grows in place while nothing else holds it
                    let v = self.expr_s(expr, false)?;
                    let cur = self.expr_s(target, false)?;
                    let r = self.call("ptr", "pl_text_append", &format!("ptr {}, ptr {}", cur, v));
                    self.write_target(target, r, &text_t())?;
                    return Ok(());
                }
                let cur = self.read_target(target)?;
                let v = self.expr(expr)?;
                if kind == "text" {
                    let r = self.call("ptr", "pl_text_glue", &format!("ptr {}, ptr {}", cur, v));
                    self.write_target(target, r, &text_t())?;
                    return Ok(());
                }
                let r = self.t();
                if t.name() == "Float" {
                    let v = self.conv(v, &vt, &float());
                    self.emit(format!("{} = fadd double {}, {}", r, cur, v));
                } else {
                    self.emit(format!("{} = add i64 {}, {}", r, cur, v));
                }
                self.write_target(target, r, &t)?;
            }
            St::Print(parts) => {
                let mut vals = Vec::new();
                for p in parts {
                    let pt = self.ty(p);
                    vals.push((pt, self.expr_s(p, false)?));
                }
                for (t, v) in vals {
                    self.out_value("out", v, &t);
                }
                self.call("void", "pl_out_print", "");
            }
            St::WriteFile { path, parts, append } => {
                let p = self.expr(path)?;
                let text = self.glued(parts)?;
                self.call("void", "pl_file_write", &format!("ptr {}, ptr {}, i32 {}", p, text, *append as i32));
            }
            St::DeleteFile(p) => {
                let v = self.expr(p)?;
                self.call("void", "pl_file_delete", &format!("ptr {}", v));
            }
            St::Reply { parts, buttons } => {
                let mut vals = Vec::new();
                for p in parts {
                    let pt = self.ty(p);
                    vals.push((pt, self.expr(p)?));
                }
                let mut bs = Vec::new();
                for b in buttons {
                    bs.push(self.expr(b)?);
                }
                for (t, v) in vals {
                    self.out_value("out", v, &t);
                }
                for b in bs {
                    self.call("void", "pl_button", &format!("ptr {}", b));
                }
                self.call("void", "pl_out_reply", "");
            }
            St::Repeat { count, body } => {
                let n = self.expr(count)?;
                let ctr = self.counter_loop("%rep", "0");
                let (cond, b, step, done) = (self.label("repeat.cond"), self.label("repeat.body"), self.label("repeat.next"), self.label("repeat.end"));
                self.start(&cond);
                let (i, c) = (self.t(), self.t());
                self.emit(format!("{} = load i64, ptr {}", i, ctr));
                self.emit(format!("{} = icmp slt i64 {}, {}", c, i, n));
                self.term(format!("br i1 {}, label %{}, label %{}", c, b, done));
                self.start(&b);
                self.lp(&step, &done, body)?;
                self.start(&step);
                self.advance(&ctr, 1, &cond);
                self.start(&done);
            }
            St::While { cond, body } => {
                let (c_l, b, done) = (self.label("while.cond"), self.label("while.body"), self.label("while.end"));
                self.start(&c_l);
                let c = self.expr(cond)?;
                self.term(format!("br i1 {}, label %{}, label %{}", c, b, done));
                self.start(&b);
                self.lp(&c_l, &done, body)?;
                self.term(format!("br label %{}", c_l));
                self.start(&done);
            }
            St::ForEach { var, items, body, kind } => {
                let mut it = self.expr(items)?;
                let kind = kind.borrow().clone();
                if kind == "text" {
                    it = self.call("ptr", "pl_letters", &format!("ptr {}", it));
                } else if kind == "map" {
                    it = self.call("ptr", "pl_map_keys", &format!("ptr {}", it));
                }
                let vt = self.ty(var);
                let ctr = self.counter_loop("%each", "0");
                let (cond, b, step, done) = (self.label("each.cond"), self.label("each.body"), self.label("each.next"), self.label("each.end"));
                self.start(&cond);
                let (i, n, c) = (self.t(), self.t(), self.t());
                self.emit(format!("{} = load i64, ptr {}", i, ctr));
                self.emit(format!("{} = call i64 @pl_list_len(ptr {})", n, it));
                self.emit(format!("{} = icmp slt i64 {}, {}", c, i, n));
                self.term(format!("br i1 {}, label %{}, label %{}", c, b, done));
                self.start(&b);
                let pos = self.t();
                self.emit(format!("{} = add i64 {}, 1", pos, i));
                let raw = self.item_at(&it, &i); // i is below the length, checked above
                let v = self.from_slot(raw, &vt);
                self.write_target(var, v, &vt)?;
                self.lp(&step, &done, body)?;
                self.start(&step);
                self.advance(&ctr, 1, &cond);
                self.start(&done);
            }
            St::ForRange { var, start, end, body, down } => {
                let a = self.expr(start)?;
                let b = self.expr(end)?;
                let ctr = self.counter_loop("%count", &a);
                let stepv = if *down { -1 } else { 1 };
                let (cond, bl, step, done) = (self.label("count.cond"), self.label("count.body"), self.label("count.next"), self.label("count.end"));
                self.start(&cond);
                let (i, c) = (self.t(), self.t());
                self.emit(format!("{} = load i64, ptr {}", i, ctr));
                self.emit(format!("{} = icmp {} i64 {}, {}", c, if *down { "sge" } else { "sle" }, i, b));
                self.term(format!("br i1 {}, label %{}, label %{}", c, bl, done));
                self.start(&bl);
                self.write_target(var, i, &crate::types::int())?;
                self.lp(&step, &done, body)?;
                self.start(&step);
                self.advance(&ctr, stepv, &cond);
                self.start(&done);
            }
            St::If { cond, then, else_ } => {
                let c = self.expr(cond)?;
                let (yes, no, done) = (self.label("if.then"), self.label("if.else"), self.label("if.end"));
                self.term(format!("br i1 {}, label %{}, label %{}", c, yes, no));
                self.start(&yes);
                let t = then.borrow().clone();
                self.block(&t)?;
                self.term(format!("br label %{}", done));
                self.start(&no);
                let e = else_.borrow().clone().unwrap_or_default();
                self.block(&e)?;
                self.term(format!("br label %{}", done));
                self.start(&done);
            }
            St::Break | St::Continue => {
                let (cont, brk, depth) = self.f.loops.last().cloned().unwrap();
                self.leave_tries(depth);
                let target = if matches!(s.k, St::Break) { brk } else { cont };
                self.term(format!("br label %{}", target));
            }
            St::Try { body, handler } => {
                let jb = self.call("ptr", "pl_try_push", "");
                let (r, c) = (self.t(), self.t());
                self.emit(format!("{} = call i32 @_setjmp(ptr {}) returns_twice", r, jb));
                self.emit(format!("{} = icmp eq i32 {}, 0", c, r));
                let (b, failed, done) = (self.label("try.body"), self.label("try.failed"), self.label("try.end"));
                self.term(format!("br i1 {}, label %{}, label %{}", c, b, failed));
                self.start(&b);
                self.f.tries += 1;
                let bd = body.borrow().clone();
                self.block(&bd)?;
                self.f.tries -= 1;
                self.call("void", "pl_try_pop", "");
                self.term(format!("br label %{}", done));
                self.start(&failed);
                let h = handler.borrow().clone().unwrap_or_default();
                self.block(&h)?;
                self.term(format!("br label %{}", done));
                self.start(&done);
            }
            St::Eval(e) => {
                self.expr(e)?;
            }
            St::Fail(parts) => {
                let g = self.glued(parts)?;
                self.call("void", "pl_raise", &format!("ptr {}", g));
                self.term("unreachable");
            }
            St::Exit => {
                self.call("void", "pl_exit", "");
                self.term("unreachable");
            }
            St::Return(e) => {
                let mut value = None;
                let mut rt = None;
                if let Some(e) = e {
                    let sig = self.sig.clone().unwrap();
                    let r = self.types.ret_type(&sig);
                    let v = self.expr(e)?;
                    let et = self.ty(e);
                    value = Some(self.conv(v, &et, &r));
                    rt = Some(r);
                }
                self.leave_tries(0);
                match value {
                    None => self.term("ret void"),
                    Some(v) => self.term(format!("ret {} {}", llt(rt.as_ref().unwrap()), v)),
                }
            }
            St::RemoveValue { items, value } => {
                let lst = self.expr(items)?;
                let el = self.ty(items).arg(0).clone();
                let v = self.expr(value)?;
                let vt = self.ty(value);
                let v = self.conv(v, &vt, &el);
                let v = self.to_slot(v, &el);
                let d = self.desc(&el);
                self.call("void", "pl_list_remove_value", &format!("ptr {}, i64 {}, ptr {}", lst, v, d));
            }
            St::RemoveAt { items, index } => {
                let l = self.expr(items)?;
                let i = self.expr(index)?;
                self.call("void", "pl_list_remove_at", &format!("ptr {}, i64 {}", l, i));
            }
            St::RemoveKey { table, key } => {
                let m = self.expr(table)?;
                let k = self.expr(key)?;
                let kt = self.ty(key);
                let k = self.to_slot(k, &kt);
                self.call("void", "pl_map_remove", &format!("ptr {}, i64 {}", m, k));
            }
            St::Sort { items, var, key, descending } => {
                let lst = self.expr(items)?;
                let el = self.ty(items).arg(0).clone();
                match key {
                    None => {
                        let d = self.desc(&el);
                        self.call("void", "pl_list_sort", &format!("ptr {}, ptr {}, i32 {}", lst, d, *descending as i32));
                    }
                    Some(key) => {
                        let (keys, kt) = self.keys_of(&lst, &el, var.as_ref().unwrap(), key)?;
                        let d = self.desc(&kt);
                        self.call("void", "pl_list_sort_by", &format!("ptr {}, ptr {}, ptr {}, i32 {}", lst, keys, d, *descending as i32));
                    }
                }
            }
            St::Reverse(items) => {
                let l = self.expr(items)?;
                self.call("void", "pl_list_reverse", &format!("ptr {}", l));
            }
            St::Ask { prompt, target } => {
                let pt = self.ty(prompt);
                let mut p = self.expr(prompt)?;
                if pt.name() != "Text" {
                    let s = self.to_slot(p, &pt);
                    let d = self.desc(&pt);
                    p = self.call("ptr", "pl_to_text", &format!("i64 {}, ptr {}", s, d));
                }
                let t = self.ty(target);
                let (func, ret) = match t.name() {
                    "Text" => ("pl_ask_text", "ptr"),
                    "Int" => ("pl_ask_int", "i64"),
                    _ => ("pl_ask_float", "double"),
                };
                let r = self.call(ret, func, &format!("ptr {}", p));
                self.write_target(target, r, &t)?;
            }
            St::CallStmt(c) => {
                self.call_fn(c)?;
            }
            St::FuncDef(_) | St::Handler { .. } | St::Persist(_) => {}
        }
        Ok(())
    }

    // ── functions and the module ──

    fn function(&mut self, sig: &Sig) -> Res<String> {
        let params = self.types.param_types(sig);
        let ret = if sig.returns { Some(self.types.ret_type(sig)) } else { None };
        let ps: Vec<String> = sig.params.iter().zip(params.iter()).map(|(n, t)| format!("{} %p.{}", llt(t), san(n))).collect();
        let header = format!(
            "define internal {} @plain.{}({})",
            ret.as_ref().map(llt).unwrap_or("void"),
            sig.name,
            ps.join(", ")
        );
        self.f = Fn::new(header);
        self.f.volatile = walk(&sig.body.borrow()).iter().any(|s| matches!(s.k, St::Try { .. }));
        self.sig = Some(sig.clone());
        let local_types: IndexMap<String, Ty> =
            self.types.variables("local").into_iter().filter(|(_, f, _)| f == &sig.name).map(|(n, _, t)| (n, t)).collect();
        for (name, t) in &local_types {
            self.alloca(&format!("%l.{}", san(name)), llt(t));
        }
        for (name, t) in sig.params.iter().zip(params.iter()) {
            self.emit(format!("store {} %p.{}, ptr %l.{}", llt(t), san(name), san(name)));
        }
        for (name, t) in &local_types {
            if !sig.params.contains(name) {
                let z = self.zero(t);
                self.emit(format!("store {} {}, ptr %l.{}", llt(t), z, san(name)));
            }
        }
        let b = sig.body.borrow().clone();
        self.block(&b)?;
        match &ret {
            None => self.term("ret void"),
            Some(ret) => {
                if let Some(rt) = local_types.get("result") {
                    let r = self.t();
                    self.emit(format!("{} = load {}, ptr %l.result", r, llt(rt)));
                    let v = self.conv(r, rt, ret);
                    self.term(format!("ret {} {}", llt(ret), v));
                } else {
                    let z = self.zero(ret);
                    self.term(format!("ret {} {}", llt(ret), z));
                }
            }
        }
        Ok(self.f.text())
    }

    fn main(&mut self) -> Res<String> {
        self.f = Fn::new("define i32 @main(i32 %argc, ptr %argv)".into());
        let top: Vec<S> = self.prog.iter().filter(|s| !matches!(s.k, St::FuncDef(_))).cloned().collect();
        self.f.volatile = walk(&top).iter().any(|s| matches!(s.k, St::Try { .. }));
        self.sig = None;
        self.emit("call void @pl_init(i32 %argc, ptr %argv)");
        let kinds: Vec<Kind> = self.types.kinds().values().cloned().collect();
        if !kinds.is_empty() {
            let mut rows = Vec::new();
            for k in &kinds {
                let fields = self.types.kind_fields(k);
                let mut names = Vec::new();
                for (f, _) in &fields {
                    names.push(format!("ptr {}", self.string(&k.field_display[f])));
                }
                let mut descs = Vec::new();
                for (_, t) in &fields {
                    descs.push(format!("ptr {}", self.desc(t)));
                }
                let id = k.id.get();
                self.extra.push(format!("@pl.kind{}.fields = private constant [{} x ptr] [{}]", id, fields.len(), names.join(", ")));
                self.extra.push(format!("@pl.kind{}.descs = private constant [{} x ptr] [{}]", id, fields.len(), descs.join(", ")));
                let name = self.string(&k.name);
                rows.push(format!(
                    "{{ptr, i32, ptr, ptr}} {{ptr {}, i32 {}, ptr @pl.kind{}.fields, ptr @pl.kind{}.descs}}",
                    name,
                    fields.len(),
                    id,
                    id
                ));
            }
            self.extra.push(format!("@pl.kinds = private constant [{} x {{ptr, i32, ptr, ptr}}] [{}]", rows.len(), rows.join(", ")));
            self.emit(format!("call void @pl_register_kinds(ptr @pl.kinds, i32 {})", rows.len()));
        }
        let fname = self.string(&basename(&self.file));
        self.emit(format!("store ptr {}, ptr @pl_file", fname));
        for (name, _, t) in self.globals.clone() {
            let local = self.main_locals.contains(&name);
            let place = if local { format!("%m.{}", san(&name)) } else { format!("@g.{}", san(&name)) };
            if local {
                self.alloca(&place, llt(&t));
            }
            let z = self.zero(&t);
            self.emit(format!("store {} {}, ptr {}", llt(&t), z, place));
        }
        let setup: Vec<S> = self.prog.iter().filter(|s| !matches!(s.k, St::Handler { .. } | St::FuncDef(_))).cloned().collect();
        self.block(&setup)?;
        let handlers: Vec<S> = self.prog.iter().filter(|s| matches!(s.k, St::Handler { .. })).cloned().collect();
        if !handlers.is_empty() {
            self.bot_loop(&handlers)?;
        }
        self.term("ret i32 0");
        Ok(self.f.text())
    }

    fn bot_loop(&mut self, handlers: &[S]) -> Res<()> {
        let users = self.types.variables("user");
        let mut names = Vec::new();
        let mut descs = Vec::new();
        for (name, _, _) in &users {
            names.push(format!("ptr {}", self.string(&name["their.".len()..])));
        }
        for (_, _, t) in &users {
            descs.push(format!("ptr {}", self.desc(t)));
        }
        self.extra.push(format!("@pl.user_names = private constant [{} x ptr] [{}]", names.len(), names.join(", ")));
        self.extra.push(format!("@pl.user_descs = private constant [{} x ptr] [{}]", descs.len(), descs.join(", ")));
        self.emit(format!("call void @pl_bot_start(i32 {}, ptr @pl.user_names, ptr @pl.user_descs)", descs.len()));
        let persist = self.prog.iter().find(|s| matches!(s.k, St::Persist(_))).cloned();
        if let Some(p) = persist {
            let mut rows = Vec::new();
            for (name, _, t) in self.globals.clone() {
                let s = self.string(&name);
                let d = self.desc(&t);
                rows.push(format!("{{ptr, ptr, ptr}} {{ptr {}, ptr {}, ptr @g.{}}}", s, d, san(&name)));
            }
            self.extra.push(format!("@pl.persist = private constant [{} x {{ptr, ptr, ptr}}] [{}]", rows.len(), rows.join(", ")));
            let path = match &p.k {
                St::Persist(e) => self.expr(e)?,
                _ => unreachable!(),
            };
            self.emit(format!("call void @pl_persist(ptr {}, ptr @pl.persist, i32 {})", path, rows.len()));
        }
        let (poll, stop) = (self.label("bot.poll"), self.label("bot.stop"));
        self.start(&poll);
        let got = self.truthy("pl_bot_next", "");
        let first = self.label("rule");
        self.term(format!("br i1 {}, label %{}, label %{}", got, first, stop));
        self.start(&first);
        for h in handlers {
            // the first rule that matches wins
            self.emit(format!("store i32 {}, ptr @pl_line", h.line));
            let (cond, body) = match &h.k {
                St::Handler { cond, body } => (cond, body.borrow().clone()),
                _ => unreachable!(),
            };
            let c = self.expr(cond)?;
            let (b, nxt) = (self.label("rule.body"), self.label("rule"));
            self.term(format!("br i1 {}, label %{}, label %{}", c, b, nxt));
            self.start(&b);
            self.block(&body)?;
            self.term(format!("br label %{}", poll));
            self.start(&nxt);
        }
        self.term(format!("br label %{}", poll));
        self.start(&stop);
        Ok(())
    }

    fn module(&mut self) -> Res<String> {
        let mut fns = Vec::new();
        for s in self.prog.clone() {
            if let St::FuncDef(sig) = &s.k {
                fns.push(self.function(sig)?);
            }
        }
        let main = self.main()?;
        let mut out = vec!["; generated by engc from Plain source; link with runtime/*.c and -lgc".to_string(), String::new()];
        for (name, _, t) in &self.globals {
            if self.main_locals.contains(name) {
                continue;
            }
            let init = match t.name() {
                "Int" => "0",
                "Float" => "0.0",
                "Bool" => "false",
                _ => "null",
            };
            out.push(format!("@g.{} = internal global {} {}", san(name), llt(t), init));
        }
        for (s, g) in &self.strings {
            let mut data = s.as_bytes().to_vec();
            data.push(0);
            let enc: String = data
                .iter()
                .map(|&b| if (32..127).contains(&b) && b != b'"' && b != b'\\' { (b as char).to_string() } else { format!("\\{:02X}", b) })
                .collect();
            out.push(format!("{} = private unnamed_addr constant [{} x i8] c\"{}\"", g, data.len(), enc));
        }
        out.extend(self.extra.iter().cloned());
        out.push(String::new());
        out.push(DECLS.trim_end_matches('\n').to_string());
        out.push(String::new());
        out.extend(fns);
        out.push(main);
        out.push(String::new());
        out.push("!0 = !{!\"branch_weights\", i32 2000, i32 1}".into());
        out.push(String::new());
        Ok(out.join("\n"))
    }
}

pub fn emit(prog: &[S], types: &Types, file: &str) -> Res<String> {
    Gen::new(prog, types, file).module()
}

// ───────────── building ─────────────

fn which(tool: &str) -> bool {
    std::env::var_os("PATH").is_some_and(|p| std::env::split_paths(&p).any(|d| d.join(tool).is_file()))
}

fn hash(parts: &[&str]) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    for p in parts {
        for b in p.bytes().chain(std::iter::once(0)) {
            h ^= b as u64;
            h = h.wrapping_mul(0x100000001b3);
        }
    }
    format!("{:016x}", h)
}

fn run(cmd: &mut Command) -> Res<()> {
    let status = cmd.status().map_err(|e| EngError::new(format!("I couldn't run {:?}: {}", cmd.get_program(), e), 0, ""))?;
    if !status.success() {
        return Err(EngError::new("The C compiler (clang) failed while building the program.", 0, ""));
    }
    Ok(())
}

/// The compiled runtime, cached in the temp folder by a hash of its sources.
fn runtime_objects(bot: bool, net: bool, lto: bool) -> Res<Vec<PathBuf>> {
    let mut parts: Vec<&str> = RUNTIME.iter().flat_map(|(n, s)| [*n, *s]).collect();
    if lto {
        parts.push("-flto");
    }
    let cache = std::env::temp_dir().join(format!("plain-runtime-rs-{}", hash(&parts)));
    std::fs::create_dir_all(&cache).map_err(|e| EngError::new(e.to_string(), 0, ""))?;
    for (name, src) in RUNTIME {
        let p = cache.join(name);
        if !p.exists() {
            std::fs::write(&p, src).map_err(|e| EngError::new(e.to_string(), 0, ""))?;
        }
    }
    let mut sources: Vec<&str> = CORE_SOURCES.to_vec();
    if net || bot {
        sources.push("net.c");
    }
    if bot {
        sources.push("telegram.c");
    }
    let mut objs = Vec::new();
    for name in sources {
        let obj = cache.join(format!("{}.o", &name[..name.len() - 2]));
        if !obj.exists() {
            let tmp = cache.join(format!("{}.{}.tmp", &name[..name.len() - 2], std::process::id()));
            let mut cmd = Command::new("clang");
            cmd.arg("-O2");
            if lto {
                cmd.arg("-flto");
            }
            cmd.args(["-c", "-DPLAIN_TELEGRAM"]).arg(cache.join(name)).arg("-o").arg(&tmp);
            run(&mut cmd)?;
            std::fs::rename(&tmp, &obj).map_err(|e| EngError::new(e.to_string(), 0, ""))?;
        }
        objs.push(obj);
    }
    Ok(objs)
}

pub fn temp_dir(tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let d = std::env::temp_dir().join(format!("plain-{}-{}-{}", tag, std::process::id(), nanos));
    let _ = std::fs::create_dir_all(&d);
    d
}

pub fn build(prog: &[S], types: &Types, file: &str, output: &Path) -> Res<()> {
    let ir = emit(prog, types, file)?;
    let bot = is_bot(prog);
    let lto = which("ld.lld");
    let dir = temp_dir("build");
    let ll = dir.join("out.ll");
    std::fs::write(&ll, &ir).map_err(|e| EngError::new(e.to_string(), 0, ""))?;
    let net = ir.contains("call ptr @pl_fetch(") || ir.contains("call ptr @pl_post(");
    let objs = runtime_objects(bot, net, lto)?;
    let mut cmd = Command::new("clang");
    cmd.arg("-O3");
    if lto {
        cmd.args(["-flto", "-fuse-ld=lld"]);
    }
    cmd.arg("-Wno-override-module").arg(&ll).args(&objs).arg("-o").arg(output).args(["-lgc", "-lm"]);
    if bot || net {
        cmd.arg("-lcurl");
    }
    let r = run(&mut cmd);
    let _ = std::fs::remove_dir_all(&dir);
    r
}
