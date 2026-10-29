//! Type inference: type variables, unification and a few deferred decisions. Mirrors plainc/types.py.
//!
//! Nobody writes types in Plain. Every value gets a type variable; sentences add equations between
//! them. Some decisions wait until more is known ("add X to Y" depends on whether Y is a number, a
//! list or a text), so they are kept as deferred constraints and solved in rounds.

use std::cell::Cell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use indexmap::IndexMap;

use crate::core::*;
use crate::parser::Funcs;
use crate::syntax::{EngError, Res};

#[derive(Debug)]
pub struct Con {
    pub name: String,
    pub args: Vec<Ty>,
}

#[derive(Clone, Debug)]
pub enum Ty {
    V(usize),
    C(Rc<Con>),
}

impl Ty {
    /// A known type's constructor; panics on a variable (only for fully known types).
    pub fn c(&self) -> &Con {
        match self {
            Ty::C(c) => c,
            Ty::V(_) => panic!("type variable where a known type was expected"),
        }
    }
    pub fn name(&self) -> &str {
        &self.c().name
    }
    pub fn arg(&self, i: usize) -> &Ty {
        &self.c().args[i]
    }
}

fn mk(name: &str, args: Vec<Ty>) -> Ty {
    Ty::C(Rc::new(Con { name: name.to_string(), args }))
}
pub fn int() -> Ty {
    mk("Int", vec![])
}
pub fn float() -> Ty {
    mk("Float", vec![])
}
pub fn text_t() -> Ty {
    mk("Text", vec![])
}
pub fn boolean() -> Ty {
    mk("Bool", vec![])
}
fn nothing_t() -> Ty {
    mk("Nothing", vec![])
}
fn list_t(t: Ty) -> Ty {
    mk("List", vec![t])
}
fn map_t(k: Ty, v: Ty) -> Ty {
    mk("Map", vec![k, v])
}
pub fn kind_con(k: &KindInfo) -> Ty {
    mk(&format!("Kind:{}", k.name), vec![])
}

fn numeric_name(n: &str) -> bool {
    n == "Int" || n == "Float"
}

#[derive(Default, Clone)]
struct TVar {
    r: Option<Ty>,
    int: bool, // number kinds that flowed in before the type was known
    float: bool,
}

struct Clash;

type Msg = Rc<dyn Fn(&Checker) -> String>;
type Msg2 = Rc<dyn Fn(&Checker, &Ty, &Ty) -> String>;
type Step = Box<dyn FnMut(&mut Checker, bool) -> Res<bool>>;

fn m(s: &str) -> Msg {
    let s = s.to_string();
    Rc::new(move |_| s.clone())
}

struct Constraint {
    id: u64,
    step: Option<Step>,
    line: u32,
    file: String,
    weak: bool, // its fallback is only a guess: use it after every other decision
}

pub struct Checker {
    sigs: Vec<Sig>,
    pub kinds: IndexMap<String, Kind>,
    tv: Vec<TVar>,
    vars: IndexMap<(String, String, String), Ty>, // (scope, func, name) -> type
    kind_fields: IndexMap<String, IndexMap<String, Ty>>,
    params: HashMap<String, Vec<Ty>>,
    ret: HashMap<String, Ty>,
    generic: HashSet<String>, // functions checked again for every use, with that use's types
    active: HashMap<String, Sig>, // generic function -> the copy being checked (for recursion)
    instances: Vec<Sig>,
    deferred: Vec<Constraint>,
    next_constraint: u64,
    line: u32,
    file: String,
    func: Option<Sig>,
    in_handler: bool,
    loops: u32,
    direct_bot: HashSet<String>,
    calls: Vec<(Option<String>, Sig, bool, u32, String)>,
    persist: Option<S>,
    handlers: bool,
    types: HashMap<usize, Ty>, // expression id -> type
}

impl Checker {
    fn create(funcs: &Funcs, generic: HashSet<String>) -> Checker {
        let kinds = funcs.kinds.by_name.clone();
        let mut c = Checker {
            sigs: funcs.sigs.clone(),
            kinds: kinds.clone(),
            tv: Vec::new(),
            vars: IndexMap::new(),
            kind_fields: IndexMap::new(),
            params: HashMap::new(),
            ret: HashMap::new(),
            generic,
            active: HashMap::new(),
            instances: Vec::new(),
            deferred: Vec::new(),
            next_constraint: 0,
            line: 0,
            file: String::new(),
            func: None,
            in_handler: false,
            loops: 0,
            direct_bot: HashSet::new(),
            calls: Vec::new(),
            persist: None,
            handlers: false,
            types: HashMap::new(),
        };
        for k in kinds.values() {
            let fields: IndexMap<String, Ty> = k.fields.iter().map(|f| (f.clone(), c.new())).collect();
            c.kind_fields.insert(k.name.clone(), fields);
        }
        if let Some(fields) = c.kind_fields.get("moment").cloned() {
            for (f, t) in fields {
                let _ = c.unify(&t, &if f == "weekday" { text_t() } else { int() });
            }
        }
        for k in kinds.values() {
            for (f, spec) in k.field_specs.borrow().iter() {
                let t = c.kind_fields[&k.name][f].clone();
                let s = c.from_spec(spec);
                let _ = c.unify(&t, &s);
            }
        }
        for sig in funcs.sigs.clone() {
            c.signature(&sig);
        }
        c
    }

    fn signature(&mut self, sig: &Sig) {
        let ps: Vec<Ty> = sig.params.iter().map(|_| self.new()).collect();
        let r = if sig.returns { self.new() } else { nothing_t() };
        for (name, t) in sig.params.iter().zip(ps.iter()) {
            if let Some(k) = sig.param_kinds.get(name) {
                let _ = self.unify(t, &kind_con(k));
            }
        }
        self.params.insert(sig.name.clone(), ps);
        self.ret.insert(sig.name.clone(), r);
    }

    // ── type variables ──

    fn new(&mut self) -> Ty {
        self.tv.push(TVar::default());
        Ty::V(self.tv.len() - 1)
    }

    fn prune(&self, t: &Ty) -> Ty {
        let mut t = t.clone();
        while let Ty::V(i) = t {
            match &self.tv[i].r {
                Some(r) => t = r.clone(),
                None => break,
            }
        }
        t
    }

    pub fn deep(&self, t: &Ty) -> Ty {
        match self.prune(t) {
            Ty::V(_) => int(),
            Ty::C(c) => {
                if c.args.is_empty() {
                    Ty::C(c)
                } else {
                    mk(&c.name, c.args.iter().map(|a| self.deep(a)).collect())
                }
            }
        }
    }

    fn con(&self, t: &Ty) -> Option<Rc<Con>> {
        match self.prune(t) {
            Ty::C(c) => Some(c),
            Ty::V(_) => None,
        }
    }

    fn is_name(&self, t: &Ty, name: &str) -> bool {
        self.con(t).is_some_and(|c| c.name == name)
    }

    fn has_tvar(&self, t: &Ty) -> bool {
        match self.prune(t) {
            Ty::V(_) => true,
            Ty::C(c) => c.args.iter().any(|a| self.has_tvar(a)),
        }
    }

    fn occurs(&self, v: usize, t: &Ty) -> bool {
        match self.prune(t) {
            Ty::V(i) => i == v,
            Ty::C(c) => c.args.iter().any(|a| self.occurs(v, a)),
        }
    }

    fn unify(&mut self, a: &Ty, b: &Ty) -> Result<(), Clash> {
        let (mut a, mut b) = (self.prune(a), self.prune(b));
        match (&a, &b) {
            (Ty::V(x), Ty::V(y)) if x == y => return Ok(()),
            (Ty::C(x), Ty::C(y)) if Rc::ptr_eq(x, y) => return Ok(()),
            _ => {}
        }
        if matches!(b, Ty::V(_)) && !matches!(a, Ty::V(_)) {
            std::mem::swap(&mut a, &mut b);
        }
        if let Ty::V(av) = a {
            if self.occurs(av, &b) {
                return Err(Clash);
            }
            match &b {
                Ty::V(bv) => {
                    let (i, f) = (self.tv[av].int, self.tv[av].float);
                    self.tv[*bv].int |= i;
                    self.tv[*bv].float |= f;
                }
                Ty::C(c) => {
                    let tv = &self.tv[av];
                    if (tv.int || tv.float) && (!numeric_name(&c.name) || (tv.float && c.name == "Int")) {
                        return Err(Clash);
                    }
                }
            }
            self.tv[av].r = Some(b);
            return Ok(());
        }
        let (ca, cb) = (a.c(), b.c());
        if ca.name != cb.name || ca.args.len() != cb.args.len() {
            return Err(Clash);
        }
        let pairs: Vec<(Ty, Ty)> = ca.args.iter().cloned().zip(cb.args.iter().cloned()).collect();
        for (x, y) in pairs {
            self.unify(&x, &y)?;
        }
        Ok(())
    }

    pub fn words(&self, t: &Ty, plural: bool) -> String {
        let c = match self.prune(t) {
            Ty::V(i) => {
                let tv = &self.tv[i];
                if tv.int || tv.float {
                    return self.words(&if tv.float { float() } else { int() }, plural);
                }
                return if plural { "things".into() } else { "something".into() };
            }
            Ty::C(c) => c,
        };
        let p = |a: &str, b: &str| if plural { a.to_string() } else { b.to_string() };
        match c.name.as_str() {
            "Int" => p("whole numbers", "a whole number"),
            "Float" => p("decimal numbers", "a decimal number"),
            "Text" => p("texts", "text"),
            "Bool" => p("yes/no values", "a yes/no value"),
            "Nothing" => "nothing".into(),
            n if n.starts_with("Kind:") => {
                if plural {
                    format!("{}s", &n[5..])
                } else {
                    format!("a {}", &n[5..])
                }
            }
            "List" => {
                if plural {
                    "lists".into()
                } else {
                    format!("a list of {}", self.words(&c.args[0], true))
                }
            }
            _ => {
                if plural {
                    "tables".into()
                } else {
                    format!("a table from {} to {}", self.words(&c.args[0], true), self.words(&c.args[1], true))
                }
            }
        }
    }

    fn wc(&self, c: &Con, plural: bool) -> String {
        self.words(&Ty::C(Rc::new(Con { name: c.name.clone(), args: c.args.clone() })), plural)
    }

    // ── helpers ──

    fn fail<T>(&self, msg: impl Into<String>) -> Res<T> {
        Err(EngError::new(msg, self.line, &self.file))
    }

    fn eq(&mut self, a: &Ty, b: &Ty, msg: Msg) -> Res<()> {
        match self.unify(a, b) {
            Ok(()) => Ok(()),
            Err(Clash) => self.fail(msg(self)),
        }
    }

    fn defer(&mut self, step: Step, weak: bool) {
        self.next_constraint += 1;
        self.deferred.push(Constraint { id: self.next_constraint, step: Some(step), line: self.line, file: self.file.clone(), weak });
    }

    fn var_type(&mut self, v: &VarRef) -> Ty {
        let key = (v.scope.clone(), v.func.clone(), v.name.clone());
        if let Some(t) = self.vars.get(&key) {
            return t.clone();
        }
        let t = self.new();
        self.vars.insert(key, t.clone());
        t
    }

    /// A value of type src is stored where dst is expected. Int may go into Float.
    fn flow(&mut self, src: Ty, dst: Ty, what: Msg2) -> Res<()> {
        fn step(c: &mut Checker, src: &Ty, dst: &Ty, what: &Msg2, fin: bool) -> Res<bool> {
            let (s, d) = (c.prune(src), c.prune(dst));
            let msg = |s: Ty, d: Ty, w: Msg2| -> Msg { Rc::new(move |c: &Checker| w(c, &s, &d)) };
            match (&s, &d) {
                (Ty::C(sc), Ty::C(dc)) => {
                    if !(sc.name == "Int" && dc.name == "Float") {
                        c.eq(&s, &d, msg(s.clone(), d.clone(), what.clone()))?;
                    }
                    Ok(true)
                }
                (Ty::C(sc), Ty::V(dv)) => {
                    if numeric_name(&sc.name) {
                        if sc.name == "Int" {
                            c.tv[*dv].int = true;
                        } else {
                            c.tv[*dv].float = true;
                        }
                    } else {
                        c.eq(&d, &s, msg(s.clone(), d.clone(), what.clone()))?;
                    }
                    Ok(true)
                }
                (Ty::V(_), Ty::C(dc)) => {
                    if dc.name == "Float" && !fin {
                        return Ok(false);
                    }
                    if dc.name != "Float" {
                        c.eq(&s, &d, msg(s.clone(), d.clone(), what.clone()))?;
                    }
                    Ok(true)
                }
                _ => {
                    if fin {
                        c.eq(&s, &d, msg(s.clone(), d.clone(), what.clone()))?;
                        return Ok(true);
                    }
                    Ok(false)
                }
            }
        }
        if !step(self, &src, &dst, &what, false)? {
            self.defer(Box::new(move |c, fin| step(c, &src, &dst, &what, fin)), false);
        }
        Ok(())
    }

    fn numeric(&mut self, t: Ty, what: Msg) -> Res<()> {
        fn step(c: &mut Checker, t: &Ty, what: &Msg, fin: bool) -> Res<bool> {
            match c.con(t) {
                None => {
                    if fin {
                        c.eq(t, &int(), what.clone())?;
                        return Ok(true);
                    }
                    Ok(false)
                }
                Some(k) => {
                    if !numeric_name(&k.name) {
                        return c.fail(what(c));
                    }
                    Ok(true)
                }
            }
        }
        if !step(self, &t, &what, false)? {
            self.defer(Box::new(move |c, fin| step(c, &t, &what, fin)), false);
        }
        Ok(())
    }

    /// Run decide(con) once t is known; at the end use default() if it never becomes known.
    fn when_known(
        &mut self,
        t: Ty,
        decide: impl Fn(&mut Checker, Rc<Con>) -> Res<()> + 'static,
        default: Option<Box<dyn Fn(&mut Checker) -> Res<()>>>,
        weak: bool,
    ) -> Res<()> {
        let decide = Rc::new(decide);
        let default = default.map(Rc::new);
        let step = move |c: &mut Checker, fin: bool| -> Res<bool> {
            if let Some(k) = c.con(&t) {
                decide(c, k)?;
                return Ok(true);
            }
            if fin {
                match &default {
                    None => return Ok(false),
                    Some(d) => {
                        d(c)?;
                        return Ok(true);
                    }
                }
            }
            Ok(false)
        };
        if !step(self, false)? {
            self.defer(Box::new(step), weak);
        }
        Ok(())
    }

    fn bot_word(&mut self, what: &str) -> Res<()> {
        if let Some(f) = &self.func {
            self.direct_bot.insert(f.name.clone());
        } else if !self.in_handler {
            return self.fail(format!("\"{}\" only exists inside a \"when someone sends ...\" rule.", what));
        }
        Ok(())
    }

    pub fn ty(&self, e: &Expr) -> Ty {
        self.types[&e.id].clone()
    }

    // ── expressions ──

    fn expr(&mut self, e: &E) -> Res<Ty> {
        let t = self.expr_type(e)?;
        self.types.insert(e.id, t.clone());
        Ok(t)
    }

    fn expr_type(&mut self, e: &E) -> Res<Ty> {
        match &e.k {
            Ex::Num(_) => Ok(int()),
            Ex::Dec(_) => Ok(float()),
            Ex::Text(_) => Ok(text_t()),
            Ex::Truth => Ok(boolean()),
            Ex::Var(v) => {
                if v.scope == "user" {
                    if let Some(f) = &self.func {
                        self.direct_bot.insert(f.name.clone());
                    } else if !self.in_handler {
                        return self.fail(format!("\"{}\" only exists inside a \"when someone sends ...\" rule.", v.display));
                    }
                }
                Ok(self.var_type(v))
            }
            Ex::Msg | Ex::Name => {
                self.bot_word(&en(e))?;
                Ok(text_t())
            }
            Ex::MsgNum => {
                self.bot_word(&en(e))?;
                Ok(int())
            }
            Ex::IsNum => Ok(boolean()),
            Ex::Random(lo, hi) => {
                for x in [lo, hi] {
                    let t = self.expr(x)?;
                    self.eq(&t, &int(), m("A random number needs whole numbers on both ends."))?;
                }
                Ok(int())
            }
            Ex::OneOf(items) => {
                let r = self.new();
                for x in items {
                    let t = self.expr(x)?;
                    let e2 = e.clone();
                    self.flow(t, r.clone(), Rc::new(move |_, _, _| format!("All choices in \"{}\" must be the same kind.", en(&e2))))?;
                }
                Ok(r)
            }
            Ex::RandomItem(items) => {
                let r = self.new();
                let t = self.expr(items)?;
                let i2 = items.clone();
                self.eq(&t, &list_t(r.clone()), Rc::new(move |_| format!("\"{}\" is not a list to pick from.", en(&i2))))?;
                Ok(r)
            }
            Ex::Concat(parts) => {
                for x in parts {
                    self.expr(x)?;
                }
                Ok(text_t())
            }
            Ex::ListLit(items) => {
                let el = self.new();
                for x in items {
                    let t = self.expr(x)?;
                    let e2 = e.clone();
                    self.flow(
                        t,
                        el.clone(),
                        Rc::new(move |c, s, d| {
                            format!(
                                "All items of a list must be the same kind, but \"{}\" mixes {} and {}.",
                                en(&e2),
                                c.words(s, true),
                                c.words(d, true)
                            )
                        }),
                    )?;
                }
                Ok(list_t(el))
            }
            Ex::EmptyMap => {
                let (k, v) = (self.new(), self.new());
                self.when_known(
                    k.clone(),
                    |c, k| {
                        if k.name == "Int" || k.name == "Text" {
                            Ok(())
                        } else {
                            c.fail("A table can only be looked up by text or whole numbers.")
                        }
                    },
                    None,
                    false,
                )?;
                Ok(map_t(k, v))
            }
            Ex::Bin(_, l, r) => {
                let (lt, rt) = (self.expr(l)?, self.expr(r)?);
                let res = self.new();
                self.arith(e.clone(), lt, rt, res.clone())?;
                Ok(res)
            }
            Ex::Cmp { .. } => {
                self.compare(e)?;
                Ok(boolean())
            }
            Ex::Logic(_, args) => {
                for a in args {
                    let t = self.expr(a)?;
                    let a2 = a.clone();
                    self.eq(&t, &boolean(), Rc::new(move |_| format!("\"{}\" is not a yes/no question.", en(&a2))))?;
                }
                Ok(boolean())
            }
            Ex::Index { .. } => self.index(e, false),
            Ex::Slice(cont, a, b) => {
                let c = self.expr(cont)?;
                for x in [a, b] {
                    let t = self.expr(x)?;
                    self.eq(&t, &int(), m("Item positions must be whole numbers."))?;
                }
                let r = self.new();
                let (r2, cont2) = (r.clone(), cont.clone());
                self.when_known(
                    c,
                    move |ch, k| {
                        if k.name == "List" || k.name == "Text" {
                            ch.eq(&r2, &Ty::C(k), m(""))
                        } else {
                            ch.fail(format!(
                                "I can only take items of a list or letters of a text, and \"{}\" is {}.",
                                en(&cont2),
                                ch.wc(&k, false)
                            ))
                        }
                    },
                    None,
                    false,
                )?;
                Ok(r)
            }
            Ex::Lookup { table, key, .. } => {
                let (k, v) = (self.new(), self.new());
                let tt = self.expr(table)?;
                let (t2, e2) = (table.clone(), e.clone());
                self.eq(
                    &tt,
                    &map_t(k.clone(), v.clone()),
                    Rc::new(move |c| {
                        format!(
                            "\"{}\" is {}, not a table, so \"{}\" does not work.",
                            en(&t2),
                            c.words(&c.ty(&t2), false),
                            en(&e2)
                        )
                    }),
                )?;
                let kt = self.expr(key)?;
                let (t3, k3, key3) = (table.clone(), k.clone(), key.clone());
                self.eq(
                    &kt,
                    &k,
                    Rc::new(move |c| {
                        format!(
                            "The table \"{}\" is looked up by {}, not by {}.",
                            en(&t3),
                            c.words(&k3, true),
                            c.words(&c.ty(&key3), true)
                        )
                    }),
                )?;
                Ok(v)
            }
            Ex::Call { .. } => self.call(e),
            Ex::Builtin(..) => self.builtin(e),
            Ex::New(kind, inits) => {
                let fields = self.kind_fields[&kind.name].clone();
                for (key, x) in inits {
                    let t = self.expr(x)?;
                    let (kind2, key2) = (kind.clone(), key.clone());
                    self.flow(
                        t,
                        fields[key].clone(),
                        Rc::new(move |c, s, d| {
                            format!(
                                "The {} of a {} holds {}, so it can't be {}.",
                                kind2.field_display[&key2],
                                kind2.name,
                                c.words(d, false),
                                c.words(s, false)
                            )
                        }),
                    )?;
                }
                Ok(kind_con(kind))
            }
            Ex::Field { .. } => self.field(e),
            Ex::ErrorText => Ok(text_t()),
            Ex::FromJson(x, spec) => {
                let t = self.expr(x)?;
                let words = spec_words(spec);
                self.eq(&t, &text_t(), Rc::new(move |_| format!("Only text can be read as {}.", words)))?;
                Ok(self.from_spec(spec))
            }
            Ex::Comprehension { var, items, expr, cond } => {
                let el = self.each_item(var, items)?;
                if let Some(c) = cond {
                    let t = self.expr(c)?;
                    let c2 = c.clone();
                    self.eq(&t, &boolean(), Rc::new(move |_| format!("\"{}\" is not a yes/no question.", en(&c2))))?;
                }
                Ok(list_t(match expr {
                    Some(x) => self.expr(x)?,
                    None => el,
                }))
            }
            Ex::BestBy { var, items, key, .. } => {
                let el = self.each_item(var, items)?;
                let kt = self.expr(key)?;
                self.sortable(kt)?;
                Ok(el)
            }
            Ex::Nothing => {
                let t = self.new();
                self.when_known(
                    t.clone(),
                    |c, k| {
                        if k.name.starts_with("Kind:") {
                            Ok(())
                        } else {
                            c.fail(format!(
                                "Only things of a kind you described (like \"a book has a title\") can be nothing, not {}.",
                                c.wc(&k, false)
                            ))
                        }
                    },
                    None,
                    false,
                )?;
                Ok(t)
            }
        }
    }

    fn from_spec(&self, spec: &Spec) -> Ty {
        match spec {
            Spec::List(s) => list_t(self.from_spec(s)),
            Spec::Table(s) => map_t(text_t(), self.from_spec(s)),
            Spec::Kind(k) => kind_con(k),
            Spec::Int => int(),
            Spec::Float => float(),
            Spec::Text => text_t(),
            Spec::Bool => boolean(),
        }
    }

    /// The type of the items of a list that var goes through.
    fn each_item(&mut self, var: &E, items: &E) -> Res<Ty> {
        let el = self.new();
        let it = self.expr(items)?;
        let (items2, it2) = (items.clone(), it.clone());
        self.eq(
            &it,
            &list_t(el.clone()),
            Rc::new(move |c| format!("I can only go through a list here, and \"{}\" is {}.", en(&items2), c.words(&it2, false))),
        )?;
        let vt = self.target_type(var)?;
        let display = var.var().map(|v| v.display.clone()).unwrap_or_default();
        self.eq(&vt, &el, Rc::new(move |_| format!("\"{}\" already holds something else.", display)))?;
        Ok(el)
    }

    fn sortable(&mut self, t: Ty) -> Res<()> {
        self.when_known(
            t,
            |c, k| {
                if ["Int", "Float", "Text", "Bool"].contains(&k.name.as_str()) {
                    Ok(())
                } else {
                    c.fail(format!(
                        "I can't put {} in order. Sort by a number or a text instead, for example by a field.",
                        c.wc(&k, true)
                    ))
                }
            },
            None,
            false,
        )
    }

    fn field(&mut self, e: &E) -> Res<Ty> {
        let (obj, fld, display) = match &e.k {
            Ex::Field { obj, field, display, .. } => (obj.clone(), field.clone(), display.clone()),
            _ => unreachable!(),
        };
        let o = self.expr(&obj)?;
        let r = self.new();
        let (e2, r2) = (e.clone(), r.clone());
        self.when_known(
            o,
            move |c, k| {
                if !k.name.starts_with("Kind:") {
                    return c.fail(format!("\"{}\" is {}, so it has no {}.", en(&obj), c.wc(&k, false), display));
                }
                let kname = k.name[5..].to_string();
                let fields = c.kind_fields[&kname].clone();
                if !fields.contains_key(&fld) {
                    let kind = &c.kinds[&kname];
                    let names: Vec<String> = fields.keys().map(|f| kind.field_display[f].clone()).collect();
                    return c.fail(format!("A {} has no {}. It has: {}.", kname, display, names.join(", ")));
                }
                if let Ex::Field { kind, .. } = &e2.k {
                    *kind.borrow_mut() = Some(c.kinds[&kname].clone());
                }
                let ft = fields[&fld].clone();
                let (display2, kname2, ft2, r3) = (display.clone(), kname.clone(), ft.clone(), r2.clone());
                c.eq(
                    &r2,
                    &ft,
                    Rc::new(move |c| {
                        format!("The {} of a {} is {}, not {}.", display2, kname2, c.words(&ft2, false), c.words(&r3, false))
                    }),
                )
            },
            None,
            false,
        )?;
        Ok(r)
    }

    fn arith(&mut self, e: E, l: Ty, r: Ty, res: Ty) -> Res<()> {
        let what: Msg = {
            let (e, l, r) = (e.clone(), l.clone(), r.clone());
            Rc::new(move |c: &Checker| {
                let glue = if c.is_name(&l, "Text") || c.is_name(&r, "Text") { " To glue texts together use \"and then\"." } else { "" };
                format!(
                    "I can only do arithmetic with numbers, but \"{}\" uses {} and {}.{}",
                    en(&e),
                    c.words(&l, false),
                    c.words(&r, false),
                    glue
                )
            })
        };
        fn step(c: &mut Checker, l: &Ty, r: &Ty, res: &Ty, what: &Msg, fin: bool) -> Res<bool> {
            let (a, b) = (c.con(l), c.con(r));
            for k in [&a, &b].into_iter().flatten() {
                if !numeric_name(&k.name) {
                    return c.fail(what(c));
                }
            }
            if let (Some(a), Some(b)) = (&a, &b) {
                let both_int = a.name == "Int" && b.name == "Int";
                c.eq(res, &if both_int { int() } else { float() }, what.clone())?;
                return Ok(true);
            }
            if !fin {
                return Ok(false);
            }
            let known = a.or(b).map(Ty::C);
            for t in [l, r] {
                if c.con(t).is_none() {
                    c.eq(t, &known.clone().unwrap_or_else(int), what.clone())?;
                }
            }
            step(c, l, r, res, what, false)
        }
        if !step(self, &l, &r, &res, &what, false)? {
            self.defer(Box::new(move |c, fin| step(c, &l, &r, &res, &what, fin)), false);
        }
        Ok(())
    }

    fn compare(&mut self, e: &E) -> Res<()> {
        let (op, left, right, kind) = match &e.k {
            Ex::Cmp { op, left, right, .. } => (op.clone(), left.clone(), right.clone(), ()),
            _ => unreachable!(),
        };
        let _ = kind;
        let l = self.expr(&left)?;
        let r = self.expr(&right)?;
        let bad: Msg = {
            let (l, r, e) = (l.clone(), r.clone(), e.clone());
            Rc::new(move |c: &Checker| format!("I can't compare {} with {} in \"{}\".", c.words(&l, false), c.words(&r, false), en(&e)))
        };
        if op == "starts" || op == "~=" {
            let e2 = e.clone();
            self.eq(&l, &text_t(), Rc::new(move |_| format!("Only text can start with something, in \"{}\".", en(&e2))))?;
            self.eq(&r, &text_t(), bad)?;
            return Ok(());
        }
        if op == "contains" {
            let decide = {
                let (e, r, bad, left) = (e.clone(), r.clone(), bad.clone(), left.clone());
                Rc::new(move |c: &mut Checker, k: Rc<Con>| -> Res<()> {
                    let set = |s: &str| {
                        if let Ex::Cmp { kind, .. } = &e.k {
                            *kind.borrow_mut() = s.to_string();
                        }
                    };
                    match k.name.as_str() {
                        "Text" => {
                            set("text");
                            c.eq(&r, &text_t(), bad.clone())
                        }
                        "List" => {
                            set("list");
                            c.same_kind(r.clone(), k.args[0].clone(), bad.clone())
                        }
                        "Map" => {
                            set("map");
                            c.eq(&r, &k.args[0], bad.clone())
                        }
                        _ => c.fail(format!(
                            "\"{}\" is {}; only texts, lists and tables can contain something.",
                            en(&left),
                            c.wc(&k, false)
                        )),
                    }
                })
            };
            let d1 = decide.clone();
            let (l2, bad2) = (l.clone(), bad.clone());
            let default: Box<dyn Fn(&mut Checker) -> Res<()>> = Box::new(move |c| {
                c.eq(&l2, &text_t(), bad2.clone())?;
                decide(c, Rc::new(Con { name: "Text".into(), args: vec![] }))
            });
            self.when_known(l, move |c, k| d1(c, k), Some(default), false)?;
            return Ok(());
        }
        if [">", "<", ">=", "<="].contains(&op.as_str()) {
            let e2 = e.clone();
            self.when_known(
                l.clone(),
                move |c, k| {
                    if ["Int", "Float", "Text"].contains(&k.name.as_str()) {
                        Ok(())
                    } else {
                        c.fail(format!("Only numbers and texts can be bigger or smaller, in \"{}\".", en(&e2)))
                    }
                },
                None,
                false,
            )?;
            return self.same_kind(l, r, bad);
        }
        self.same_kind(l, r, bad)
    }

    /// Equal types, except that whole and decimal numbers may be compared.
    fn same_kind(&mut self, a: Ty, b: Ty, msg: Msg) -> Res<()> {
        fn step(c: &mut Checker, a: &Ty, b: &Ty, msg: &Msg, fin: bool) -> Res<bool> {
            let (x, y) = (c.con(a), c.con(b));
            if let (Some(x), Some(y)) = (&x, &y) {
                if !(numeric_name(&x.name) && numeric_name(&y.name)) {
                    c.eq(&Ty::C(x.clone()), &Ty::C(y.clone()), msg.clone())?;
                }
                return Ok(true);
            }
            if let Some(x) = &x {
                if !numeric_name(&x.name) {
                    c.eq(b, &Ty::C(x.clone()), msg.clone())?;
                    return Ok(true);
                }
            }
            if let Some(y) = &y {
                if !numeric_name(&y.name) {
                    c.eq(a, &Ty::C(y.clone()), msg.clone())?;
                    return Ok(true);
                }
            }
            if fin {
                c.eq(a, b, msg.clone())?;
                return Ok(true);
            }
            Ok(false)
        }
        if !step(self, &a, &b, &msg, false)? {
            self.defer(Box::new(move |c, fin| step(c, &a, &b, &msg, fin)), false);
        }
        Ok(())
    }

    fn index(&mut self, e: &E, target: bool) -> Res<Ty> {
        let (container, idx) = match &e.k {
            Ex::Index { container, index, .. } => (container.clone(), index.clone()),
            _ => unreachable!(),
        };
        let c = self.expr(&container)?;
        if let Some(i) = &idx {
            let t = self.expr(i)?;
            self.eq(&t, &int(), m("Item positions must be whole numbers."))?;
        }
        let r = self.new();
        let (e2, r2) = (e.clone(), r.clone());
        self.when_known(
            c,
            move |ch, k| {
                let set = |s: &str| {
                    if let Ex::Index { kind, .. } = &e2.k {
                        *kind.borrow_mut() = s.to_string();
                    }
                };
                match k.name.as_str() {
                    "List" => {
                        set("list");
                        ch.eq(&r2, &k.args[0], m(""))
                    }
                    "Text" => {
                        if target {
                            return ch.fail("Texts can't be changed letter by letter. Build a new text with \"and then\" instead.");
                        }
                        set("text");
                        ch.eq(&r2, &text_t(), m(""))
                    }
                    _ => ch.fail(format!(
                        "I can only take items of a list or letters of a text, and \"{}\" is {}.",
                        en(&container),
                        ch.wc(&k, false)
                    )),
                }
            },
            None,
            false,
        )?;
        Ok(r)
    }

    fn call(&mut self, e: &E) -> Res<Ty> {
        let (func, args) = match &e.k {
            Ex::Call { func, args } => (func, args),
            _ => unreachable!(),
        };
        let mut sig = func.borrow().clone();
        if self.generic.contains(&sig.name) {
            let inst = match self.active.get(&sig.name) {
                Some(i) => i.clone(),
                None => self.instantiate(&sig)?,
            };
            *func.borrow_mut() = inst.clone();
            sig = inst;
        }
        let ps = self.params[&sig.name].clone();
        for (a, p) in args.iter().zip(ps) {
            let t = self.expr(a)?;
            let display = sig.display.clone();
            self.flow(
                t,
                p,
                Rc::new(move |c, s, d| {
                    format!("\"{}\" got {} for a value that is {} elsewhere.", display, c.words(s, false), c.words(d, false))
                }),
            )?;
        }
        self.calls.push((self.func.as_ref().map(|f| f.name.clone()), sig.clone(), self.in_handler, self.line, self.file.clone()));
        Ok(self.ret[&sig.name].clone())
    }

    /// A copy of a generic function for one place where it is used.
    fn instantiate(&mut self, sig: &Sig) -> Res<Sig> {
        let name = format!("{}_v{}", sig.name, self.instances.len());
        let inst = Rc::new(FuncSig {
            name: name.clone(),
            display: sig.display.clone(),
            pattern: sig.pattern.clone(),
            params: sig.params.clone(),
            returns: sig.returns,
            line: sig.line,
            file: sig.file.clone(),
            body: body(clone_body(&sig.body.borrow(), &sig.name, &name)),
            uses_bot: Cell::new(false),
            param_kinds: sig.param_kinds.clone(),
            origin: Some(sig.clone()),
        });
        self.signature(&inst);
        self.instances.push(inst.clone());
        let saved = (self.func.take(), self.loops, self.line, self.file.clone(), self.in_handler);
        self.active.insert(sig.name.clone(), inst.clone());
        self.in_handler = false;
        self.function(&inst)?;
        self.active.remove(&sig.name);
        (self.func, self.loops, self.line, self.file, self.in_handler) = saved;
        Ok(inst)
    }

    fn builtin(&mut self, e: &E) -> Res<Ty> {
        let (n, args) = match &e.k {
            Ex::Builtin(n, args) => (n.as_str(), args),
            _ => unreachable!(),
        };
        let mut ts = Vec::new();
        for a in args {
            ts.push(self.expr(a)?);
        }
        let a0 = ts.first().cloned().unwrap_or_else(int);
        let sub = args.first().map(|a| english(a, false)).unwrap_or_default();
        let (e2, sub2, a02) = (e.clone(), sub.clone(), a0.clone());
        let num_msg: Msg = Rc::new(move |c| format!("\"{}\" needs a number, but \"{}\" is {}.", en(&e2), sub2, c.words(&a02, false)));
        let needs_text = || -> Msg {
            let (e, sub, a0) = (e.clone(), sub.clone(), a0.clone());
            Rc::new(move |c| format!("\"{}\" needs text, but \"{}\" is {}.", en(&e), sub, c.words(&a0, false)))
        };
        match n {
            "length" => {
                let sub = sub.clone();
                self.when_known(
                    a0,
                    move |c, k| {
                        if ["Text", "List", "Map"].contains(&k.name.as_str()) {
                            Ok(())
                        } else {
                            c.fail(format!("Only texts, lists and tables have a length, and \"{}\" is {}.", sub, c.wc(&k, false)))
                        }
                    },
                    None,
                    false,
                )?;
                Ok(int())
            }
            "sum" | "average" | "largest" | "smallest" => {
                let el = self.new();
                let (e3, sub3, a03) = (e.clone(), sub.clone(), a0.clone());
                self.eq(
                    &a0,
                    &list_t(el.clone()),
                    Rc::new(move |c| format!("\"{}\" needs a list, but \"{}\" is {}.", en(&e3), sub3, c.words(&a03, false))),
                )?;
                if n == "sum" || n == "average" {
                    let (sub4, el4) = (sub.clone(), el.clone());
                    self.numeric(
                        el.clone(),
                        Rc::new(move |c| format!("I can only add up numbers, and \"{}\" holds {}.", sub4, c.words(&el4, true))),
                    )?;
                }
                Ok(if n == "average" { float() } else { el })
            }
            "sqrt" => {
                self.numeric(a0, num_msg)?;
                Ok(float())
            }
            "abs" | "squared" => {
                self.numeric(a0.clone(), num_msg)?;
                Ok(a0)
            }
            "round" | "round_up" | "round_down" => {
                self.numeric(a0, num_msg)?;
                Ok(int())
            }
            "upper" | "lower" | "trim" => {
                self.eq(&a0, &text_t(), needs_text())?;
                Ok(text_t())
            }
            "words" | "lines" | "letters" | "split" => {
                for t in &ts {
                    self.eq(t, &text_t(), needs_text())?;
                }
                Ok(list_t(text_t()))
            }
            "join" => {
                let v = self.new();
                let (sub3, a03) = (sub.clone(), a0.clone());
                self.eq(&a0, &list_t(v), Rc::new(move |c| format!("Only lists can be joined, and \"{}\" is {}.", sub3, c.words(&a03, false))))?;
                self.eq(&ts[1], &text_t(), m("What goes between the items must be text."))?;
                Ok(text_t())
            }
            "replace" | "position" => {
                for t in &ts {
                    let e3 = e.clone();
                    self.eq(t, &text_t(), Rc::new(move |_| format!("\"{}\" works with texts only.", en(&e3))))?;
                }
                Ok(if n == "replace" { text_t() } else { int() })
            }
            "to_int" | "to_float" => {
                self.when_known(
                    a0,
                    |c, k| {
                        if ["Text", "Int", "Float"].contains(&k.name.as_str()) {
                            Ok(())
                        } else {
                            let w = c.wc(&k, false);
                            let mut ch = w.chars();
                            let cap = ch.next().map(|f| f.to_uppercase().collect::<String>() + &ch.as_str().to_lowercase()).unwrap_or_default();
                            c.fail(format!("{} can't be turned into a number.", cap))
                        }
                    },
                    None,
                    false,
                )?;
                Ok(if n == "to_int" { int() } else { float() })
            }
            "to_text" => Ok(text_t()),
            "is_number" => {
                let (sub3, a03) = (sub.clone(), a0.clone());
                self.eq(
                    &a0,
                    &text_t(),
                    Rc::new(move |c| format!("Only text can be checked for being a number, and \"{}\" is {}.", sub3, c.words(&a03, false))),
                )?;
                Ok(boolean())
            }
            "file_text" | "file_exists" => {
                self.eq(&a0, &text_t(), m("A file name must be text in quotes."))?;
                Ok(if n == "file_text" { text_t() } else { boolean() })
            }
            "arguments" => Ok(list_t(text_t())),
            "now" => Ok(kind_con(&self.kinds["moment"])),
            "timestamp" | "millis" | "exit_code" => Ok(int()),
            "sin" | "cos" | "tan" | "log" => {
                self.numeric(a0, num_msg)?;
                Ok(float())
            }
            "random_float" => {
                for t in ts {
                    self.numeric(t, m("A random decimal needs numbers on both ends."))?;
                }
                Ok(float())
            }
            "code" | "env" | "fetch" | "run_output" | "run" => {
                self.eq(&a0, &text_t(), needs_text())?;
                Ok(if n == "code" || n == "run" { int() } else { text_t() })
            }
            "char" => {
                self.eq(&a0, &int(), m("A letter code must be a whole number."))?;
                Ok(text_t())
            }
            "to_json" | "post" => {
                if n == "post" {
                    self.eq(&ts[1], &text_t(), m("An address must be text in quotes."))?;
                }
                Ok(text_t())
            }
            "pad" | "pad_left" | "repeat_text" | "decimals" => {
                if n == "repeat_text" {
                    let (sub3, a03) = (sub.clone(), a0.clone());
                    self.eq(&a0, &text_t(), Rc::new(move |c| format!("Only text can be repeated, and \"{}\" is {}.", sub3, c.words(&a03, false))))?;
                }
                if n == "decimals" {
                    self.numeric(a0, num_msg)?;
                }
                self.eq(&ts[1], &int(), m("The width or number of decimals must be a whole number."))?;
                Ok(text_t())
            }
            "wait" => {
                self.numeric(a0, m("How long to wait must be a number."))?;
                Ok(int())
            }
            "known" => {
                let v = self.new();
                let sub3 = sub.clone();
                self.eq(&a0, &map_t(ts[1].clone(), v), Rc::new(move |_| format!("\"{}\" is not a table.", sub3)))?;
                Ok(boolean())
            }
            _ => self.fail(format!("internal: unknown builtin {}", n)),
        }
    }

    // ── statements ──

    fn target_type(&mut self, t: &E) -> Res<Ty> {
        if let Ex::Index { .. } = &t.k {
            let ty = self.index(t, true)?;
            self.types.insert(t.id, ty.clone());
            return Ok(ty);
        }
        self.expr(t)
    }

    fn block(&mut self, body: &[S]) -> Res<()> {
        for s in body {
            if s.line > 0 {
                self.line = s.line;
                self.file = s.file.clone();
            }
            self.stmt(s)?;
        }
        Ok(())
    }

    fn lp(&mut self, body: &Body) -> Res<()> {
        self.loops += 1;
        let b = body.borrow().clone();
        self.block(&b)?;
        self.loops -= 1;
        Ok(())
    }

    fn yes_no(&mut self, cond: &E) -> Res<()> {
        let t = self.expr(cond)?;
        let c2 = cond.clone();
        self.eq(&t, &boolean(), Rc::new(move |_| format!("\"{}\" is not a yes/no question.", en(&c2))))
    }

    fn stmt(&mut self, s: &S) -> Res<()> {
        match &s.k {
            St::Assign { target, expr } => {
                let tt = self.target_type(target)?;
                let vt = self.expr(expr)?;
                let t2 = target.clone();
                self.flow(
                    vt,
                    tt,
                    Rc::new(move |c, a, b| {
                        let hint = if c.is_name(a, "Float") && c.is_name(b, "Int") {
                            " Write the first value as a decimal, for example 0.0."
                        } else {
                            ""
                        };
                        format!("\"{}\" holds {}, so it can't become {}.{}", en(&t2), c.words(b, false), c.words(a, false), hint)
                    }),
                )?;
            }
            St::AddTo { .. } => self.add_to(s)?,
            St::Print(parts) => {
                for p in parts {
                    self.expr(p)?;
                }
            }
            St::WriteFile { path, parts, .. } => {
                let t = self.expr(path)?;
                self.eq(&t, &text_t(), m("A file name must be text in quotes."))?;
                for p in parts {
                    self.expr(p)?;
                }
            }
            St::Reply { parts, buttons } => {
                self.bot_word("reply")?;
                for p in parts {
                    self.expr(p)?;
                }
                for b in buttons {
                    let t = self.expr(b)?;
                    self.eq(&t, &text_t(), m("Button labels must be text in quotes."))?;
                }
            }
            St::Repeat { count, body } => {
                let t = self.expr(count)?;
                self.eq(&t, &int(), m("The number of times must be a whole number."))?;
                self.lp(body)?;
            }
            St::While { cond, body } => {
                self.yes_no(cond)?;
                self.lp(body)?;
            }
            St::ForEach { var, items, body, .. } => {
                let it = self.expr(items)?;
                let vt = self.target_type(var)?;
                let (s2, items2) = (s.clone(), items.clone());
                let display = var.var().map(|v| v.display.clone()).unwrap_or_default();
                self.when_known(
                    it,
                    move |c, k| {
                        let set = |x: &str| {
                            if let St::ForEach { kind, .. } = &s2.k {
                                *kind.borrow_mut() = x.to_string();
                            }
                        };
                        let (d2, vt2) = (display.clone(), vt.clone());
                        let msg: Msg = Rc::new(move |c| format!("\"{}\" already holds {}.", d2, c.words(&vt2, false)));
                        match k.name.as_str() {
                            "List" => {
                                set("list");
                                c.eq(&vt, &k.args[0], msg)
                            }
                            "Text" => {
                                set("text");
                                c.eq(&vt, &text_t(), msg)
                            }
                            "Map" => {
                                set("map");
                                c.eq(&vt, &k.args[0], msg)
                            }
                            _ => c.fail(format!(
                                "I can only go through a list, a text or a table, and \"{}\" is {}.",
                                en(&items2),
                                c.wc(&k, false)
                            )),
                        }
                    },
                    None,
                    false,
                )?;
                self.lp(body)?;
            }
            St::ForRange { var, start, end, body, .. } => {
                let vt = self.target_type(var)?;
                let display = var.var().map(|v| v.display.clone()).unwrap_or_default();
                self.eq(&vt, &int(), Rc::new(move |_| format!("\"{}\" already holds something else.", display)))?;
                for x in [start, end] {
                    let t = self.expr(x)?;
                    self.eq(&t, &int(), m("Counting goes from one whole number to another."))?;
                }
                self.lp(body)?;
            }
            St::If { cond, then, else_ } => {
                self.yes_no(cond)?;
                let t = then.borrow().clone();
                self.block(&t)?;
                let e = else_.borrow().clone().unwrap_or_default();
                self.block(&e)?;
            }
            St::Break | St::Continue => {
                if self.loops == 0 {
                    let what = if matches!(s.k, St::Break) { "stop the loop" } else { "skip to the next one" };
                    return self.fail(format!("\"{}\" only works inside a repeat or \"for every\".", what));
                }
            }
            St::Exit => {}
            St::Return(e) => self.give_back(e)?,
            St::RemoveValue { items, value } => {
                let it = self.expr(items)?;
                let v = self.expr(value)?;
                let el = self.new();
                let (i2, it2) = (items.clone(), it.clone());
                self.eq(
                    &it,
                    &list_t(el.clone()),
                    Rc::new(move |c| format!("I can only remove things from a list, and \"{}\" is {}.", en(&i2), c.words(&it2, false))),
                )?;
                let (i3, el3, v3) = (items.clone(), el.clone(), v.clone());
                self.same_kind(
                    v,
                    el,
                    Rc::new(move |c| format!("\"{}\" holds {}, not {}.", en(&i3), c.words(&el3, true), c.words(&v3, true))),
                )?;
            }
            St::RemoveAt { items, index } => {
                let it = self.expr(items)?;
                let v = self.new();
                let i2 = items.clone();
                self.eq(&it, &list_t(v), Rc::new(move |_| format!("\"{}\" is not a list.", en(&i2))))?;
                let t = self.expr(index)?;
                self.eq(&t, &int(), m("Item positions must be whole numbers."))?;
            }
            St::RemoveKey { table, key } => {
                let tt = self.expr(table)?;
                let kt = self.expr(key)?;
                let v = self.new();
                let t2 = table.clone();
                self.eq(&tt, &map_t(kt, v), Rc::new(move |_| format!("\"{}\" is not a table.", en(&t2))))?;
            }
            St::DeleteFile(p) => {
                let t = self.expr(p)?;
                self.eq(&t, &text_t(), m("A file name must be text in quotes."))?;
            }
            St::Sort { items, var: Some(var), key: Some(key), .. } => {
                self.each_item(var, items)?;
                let kt = self.expr(key)?;
                self.sortable(kt)?;
            }
            St::Try { body, handler } => {
                let b = body.borrow().clone();
                self.block(&b)?;
                let h = handler.borrow().clone().unwrap_or_default();
                self.block(&h)?;
            }
            St::Eval(e) => {
                self.expr(e)?;
            }
            St::Fail(parts) => {
                for p in parts {
                    self.expr(p)?;
                }
            }
            St::Sort { items, .. } | St::Reverse(items) => {
                let el = self.new();
                let it = self.expr(items)?;
                let (i2, it2) = (items.clone(), it.clone());
                self.eq(
                    &it,
                    &list_t(el.clone()),
                    Rc::new(move |c| format!("Only lists can be sorted or reversed, and \"{}\" is {}.", en(&i2), c.words(&it2, false))),
                )?;
                if matches!(s.k, St::Sort { .. }) {
                    self.when_known(
                        el,
                        |c, k| {
                            if ["Int", "Float", "Text", "Bool"].contains(&k.name.as_str()) {
                                Ok(())
                            } else {
                                c.fail(format!("I can't sort {}.", c.wc(&k, true)))
                            }
                        },
                        None,
                        false,
                    )?;
                }
            }
            St::Ask { prompt, target } => {
                if self.in_handler {
                    return self.fail("\"ask\" waits for someone typing in a terminal, so it can't be used in a bot.");
                }
                self.expr(prompt)?;
                let t = self.target_type(target)?;
                let t2 = t.clone();
                self.when_known(
                    t,
                    |c, k| {
                        if ["Text", "Int", "Float"].contains(&k.name.as_str()) {
                            Ok(())
                        } else {
                            c.fail(format!("An answer can be text or a number, not {}.", c.wc(&k, false)))
                        }
                    },
                    Some(Box::new(move |c| c.eq(&t2, &text_t(), m("")))),
                    true,
                )?;
            }
            St::CallStmt(c) => {
                self.expr(c)?;
            }
            St::Persist(p) => {
                let t = self.expr(p)?;
                self.eq(&t, &text_t(), m("A file name must be text in quotes."))?;
                self.persist = Some(s.clone());
            }
            St::Handler { cond, body } => {
                self.handlers = true;
                self.in_handler = true;
                let t = self.expr(cond)?;
                self.eq(&t, &boolean(), m(""))?;
                let b = body.borrow().clone();
                self.block(&b)?;
                self.in_handler = false;
            }
            St::FuncDef(sig) => {
                if !self.generic.contains(&sig.name) {
                    self.function(sig)?;
                }
            }
        }
        Ok(())
    }

    fn add_to(&mut self, s: &S) -> Res<()> {
        let (target, expr, create) = match &s.k {
            St::AddTo { target, expr, create, .. } => (target.clone(), expr.clone(), *create),
            _ => unreachable!(),
        };
        let tt = self.target_type(&target)?;
        let vt = self.expr(&expr)?;
        let msg: Msg2 = {
            let t = target.clone();
            Rc::new(move |c, a, b| format!("I can't add {} to \"{}\", which is {}.", c.words(a, false), en(&t), c.words(b, false)))
        };
        fn as_kind(c: &mut Checker, s: &S, kind: &str, tt: &Ty, vt: &Ty, msg: &Msg2, target: &E) -> Res<()> {
            if let St::AddTo { kind: k, .. } = &s.k {
                *k.borrow_mut() = kind.to_string();
            }
            let fixed: Msg = {
                let (tt, vt, msg) = (tt.clone(), vt.clone(), msg.clone());
                Rc::new(move |c| msg(c, &vt, &tt))
            };
            match kind {
                "num" => {
                    c.numeric(vt.clone(), fixed)?;
                    c.flow(vt.clone(), tt.clone(), msg.clone())
                }
                "list" => {
                    let el = c.new();
                    c.eq(tt, &list_t(el.clone()), fixed)?;
                    let t2 = target.clone();
                    c.flow(
                        vt.clone(),
                        el,
                        Rc::new(move |c, a, b| {
                            format!("\"{}\" is a list of {}, so {} can't go into it.", en(&t2), c.words(b, true), c.words(a, false))
                        }),
                    )
                }
                _ => c.eq(vt, &text_t(), fixed),
            }
        }
        fn step(c: &mut Checker, s: &S, tt: &Ty, vt: &Ty, msg: &Msg2, target: &E, create: bool, fin: bool) -> Res<bool> {
            let t = c.prune(tt);
            match &t {
                Ty::C(k) => {
                    match k.name.as_str() {
                        "Int" | "Float" => as_kind(c, s, "num", tt, vt, msg, target)?,
                        "List" => as_kind(c, s, "list", tt, vt, msg, target)?,
                        "Text" => as_kind(c, s, "text", tt, vt, msg, target)?,
                        _ => return c.fail(msg(c, vt, tt)),
                    }
                    return Ok(true);
                }
                Ty::V(i) => {
                    if c.tv[*i].int || c.tv[*i].float {
                        as_kind(c, s, "num", tt, vt, msg, target)?;
                        return Ok(true);
                    }
                }
            }
            if let Some(v) = c.con(vt) {
                if numeric_name(&v.name) || create || fin {
                    as_kind(c, s, if numeric_name(&v.name) { "num" } else { "list" }, tt, vt, msg, target)?;
                    return Ok(true);
                }
            }
            if fin {
                as_kind(c, s, "num", tt, vt, msg, target)?;
                return Ok(true);
            }
            Ok(false)
        }
        if !step(self, s, &tt, &vt, &msg, &target, create, false)? {
            let s = s.clone();
            self.defer(Box::new(move |c, fin| step(c, &s, &tt, &vt, &msg, &target, create, fin)), false);
        }
        Ok(())
    }

    fn give_back(&mut self, e: &Option<E>) -> Res<()> {
        let func = match &self.func {
            None => return self.fail("\"give back\" only works inside a definition that starts with \"to find\"."),
            Some(f) => f.clone(),
        };
        if !func.returns {
            if e.is_some() {
                return self.fail(format!(
                    "\"{}\" is a command, so it can't give back a value. Start the definition with \"to find\" to make it give back a value.",
                    func.display
                ));
            }
            return Ok(());
        }
        let e = match e {
            None => return self.fail(format!("\"{}\" has to give back a value, for example: give back the total.", func.display)),
            Some(e) => e,
        };
        let t = self.expr(e)?;
        let display = func.display.clone();
        let r = self.ret[&func.name].clone();
        self.flow(
            t,
            r,
            Rc::new(move |c, a, b| format!("\"{}\" gives back {} here and {} elsewhere.", display, c.words(a, false), c.words(b, false))),
        )
    }

    fn function(&mut self, sig: &Sig) -> Res<()> {
        self.func = Some(sig.clone());
        let ps = self.params[&sig.name].clone();
        for (name, t) in sig.params.iter().zip(ps) {
            self.vars.insert(("local".into(), sig.name.clone(), name.clone()), t);
        }
        let saved = self.loops;
        self.loops = 0;
        let b = sig.body.borrow().clone();
        self.block(&b)?;
        self.loops = saved;
        let result = self.vars.get(&("local".to_string(), sig.name.clone(), "result".to_string())).cloned();
        if sig.returns {
            let gives = walk_no_funcs(&b).iter().any(|x| matches!(&x.k, St::Return(Some(_))));
            if let Some(result) = result {
                let display = sig.display.clone();
                let r = self.ret[&sig.name].clone();
                self.flow(
                    result,
                    r,
                    Rc::new(move |c, a, b| {
                        format!("The result of \"{}\" is {} but it gives back {}.", display, c.words(a, false), c.words(b, false))
                    }),
                )?;
            } else if !gives {
                return Err(EngError::new(
                    format!("\"{}\" never gives back a value. Add \"give back ...\" or \"the result is ...\".", sig.display),
                    sig.line,
                    &sig.file,
                ));
            }
        }
        self.func = None;
        Ok(())
    }

    // ── solving ──

    fn run_constraint(&mut self, id: u64, fin: bool) -> Res<bool> {
        let pos = self.deferred.iter().position(|c| c.id == id).unwrap();
        let mut step = self.deferred[pos].step.take().unwrap();
        let r = step(self, fin);
        let pos = self.deferred.iter().position(|c| c.id == id).unwrap();
        match r {
            Ok(true) => {
                self.deferred.remove(pos);
                Ok(true)
            }
            Ok(false) => {
                self.deferred[pos].step = Some(step);
                Ok(false)
            }
            Err(e) => Err(e),
        }
    }

    fn solve(&mut self) -> Res<()> {
        loop {
            let mut progress = true;
            while progress {
                progress = false;
                let ids: Vec<(u64, u32, String)> = self.deferred.iter().map(|c| (c.id, c.line, c.file.clone())).collect();
                for (id, line, file) in ids {
                    self.line = line;
                    self.file = file;
                    if self.run_constraint(id, false)? {
                        progress = true;
                    }
                }
            }
            // numbers whose kind was only seen through stored values
            let mut bound = false;
            for i in 0..self.tv.len() {
                if let Ty::V(p) = self.prune(&Ty::V(i)) {
                    if self.tv[p].int || self.tv[p].float {
                        self.tv[p].r = Some(if self.tv[p].float { float() } else { int() });
                        bound = true;
                    }
                }
            }
            if bound {
                continue;
            }
            if self.deferred.is_empty() {
                break;
            }
            let mut order: Vec<(u64, u32, String, bool)> = self.deferred.iter().map(|c| (c.id, c.line, c.file.clone(), c.weak)).collect();
            order.sort_by_key(|c| c.3);
            let mut done = false;
            for (id, line, file, _) in order {
                self.line = line;
                self.file = file;
                if self.run_constraint(id, true)? {
                    done = true;
                    break;
                }
            }
            if !done {
                let c = &self.deferred[0];
                return Err(EngError::new("I can't tell what kind of value this is. Give it a value first.", c.line, &c.file));
            }
        }
        for i in 0..self.tv.len() {
            if let Ty::V(p) = self.prune(&Ty::V(i)) {
                self.tv[p].r = Some(int());
            }
        }
        Ok(())
    }

    /// Solve what can be solved without guessing (used to find generic functions).
    fn settle(&mut self) -> Res<()> {
        let mut progress = true;
        while progress {
            progress = false;
            let ids: Vec<u64> = self.deferred.iter().map(|c| c.id).collect();
            for id in ids {
                if self.run_constraint(id, false)? {
                    progress = true;
                }
            }
        }
        Ok(())
    }

    fn check_bot_calls(&mut self) -> Res<()> {
        let mut uses = self.direct_bot.clone();
        let mut changed = true;
        while changed {
            changed = false;
            for (caller, callee, _, _, _) in &self.calls {
                if let Some(caller) = caller {
                    if uses.contains(&callee.name) && !uses.contains(caller) {
                        uses.insert(caller.clone());
                        changed = true;
                    }
                }
            }
        }
        for sig in self.sigs.iter().chain(self.instances.iter()) {
            sig.uses_bot.set(uses.contains(&sig.name));
        }
        for (caller, callee, in_handler, line, file) in &self.calls {
            if caller.is_none() && !in_handler && uses.contains(&callee.name) {
                return Err(EngError::new(
                    format!(
                        "\"{}\" answers Telegram messages, so it can only be used inside a \"when someone sends ...\" rule.",
                        callee.display
                    ),
                    *line,
                    file,
                ));
            }
        }
        if let Some(p) = &self.persist {
            if !self.handlers {
                return Err(EngError::new(
                    "\"Remember everything in\" works only in bots, programs with \"when someone sends ...\" rules.",
                    p.line,
                    &p.file,
                ));
            }
        }
        Ok(())
    }
}

fn walk_no_funcs(body: &[S]) -> Vec<S> {
    let mut out = Vec::new();
    walk_stmts(body, false, &mut out);
    out
}

/// The result of type checking, as the backends see it.
pub struct Types {
    c: Checker,
}

impl Types {
    pub fn of(&self, e: &Expr) -> Ty {
        self.c.deep(&self.c.ty(e))
    }

    pub fn kinds(&self) -> &IndexMap<String, Kind> {
        &self.c.kinds
    }

    pub fn kind_fields(&self, kind: &KindInfo) -> Vec<(String, Ty)> {
        kind.fields.iter().map(|f| (f.clone(), self.c.deep(&self.c.kind_fields[&kind.name][f]))).collect()
    }

    /// (name, function, type) of every variable of a scope, in the order they were first met.
    pub fn variables(&self, scope: &str) -> Vec<(String, String, Ty)> {
        self.c.vars.iter().filter(|(k, _)| k.0 == scope).map(|(k, t)| (k.2.clone(), k.1.clone(), self.c.deep(t))).collect()
    }

    pub fn param_types(&self, sig: &FuncSig) -> Vec<Ty> {
        self.c.params[&sig.name].iter().map(|t| self.c.deep(t)).collect()
    }

    pub fn ret_type(&self, sig: &FuncSig) -> Ty {
        self.c.deep(&self.c.ret[&sig.name])
    }

    /// Short type code the runtime uses for printing and saving: i f s b, L<item>, M<key><value>,
    /// K<kind number>. (a thing of a declared kind).
    pub fn desc(&self, t: &Ty) -> String {
        let c = t.c();
        if let Some(k) = c.name.strip_prefix("Kind:") {
            return format!("K{}.", self.c.kinds[k].id.get());
        }
        match c.name.as_str() {
            "Int" => "i".into(),
            "Float" => "f".into(),
            "Text" => "s".into(),
            "Bool" => "b".into(),
            "List" => format!("L{}", self.desc(&c.args[0])),
            _ => format!("M{}{}", self.desc(&c.args[0]), self.desc(&c.args[1])),
        }
    }
}

/// Functions whose values stay open after checking the body on its own: they work with any type.
fn find_generic(funcs: &Funcs) -> HashSet<String> {
    let mut generic = HashSet::new();
    for sig in &funcs.sigs {
        let mut scratch = Checker::create(funcs, HashSet::new());
        if scratch.function(sig).is_err() || scratch.settle().is_err() {
            continue;
        }
        let mut ts = scratch.params[&sig.name].clone();
        if sig.returns {
            ts.push(scratch.ret[&sig.name].clone());
        }
        if ts.iter().any(|t| scratch.has_tvar(t)) {
            generic.insert(sig.name.clone());
        }
    }
    generic
}

pub fn check(prog: &mut Vec<S>, funcs: &Funcs) -> Res<Types> {
    let generic = find_generic(funcs);
    let mut c = Checker::create(funcs, generic.clone());
    let setup: Vec<S> = prog.iter().filter(|s| !matches!(s.k, St::Handler { .. } | St::FuncDef(_))).cloned().collect();
    c.block(&setup)?;
    let defs: Vec<S> = prog.iter().filter(|s| matches!(s.k, St::FuncDef(_))).cloned().collect();
    c.block(&defs)?;
    let handlers: Vec<S> = prog.iter().filter(|s| matches!(s.k, St::Handler { .. })).cloned().collect();
    c.block(&handlers)?;
    c.solve()?;
    c.check_bot_calls()?;
    // backends see one function per generic use, with known types
    prog.retain(|s| !matches!(&s.k, St::FuncDef(sig) if generic.contains(&sig.name)));
    for i in &c.instances {
        prog.push(Rc::new(Stmt { line: 0, file: String::new(), k: St::FuncDef(i.clone()) }));
    }
    Ok(Types { c })
}
