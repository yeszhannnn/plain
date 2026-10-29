//! Sentences -> Core IR. Mirrors plainc/parser.py.
//!
//! Every parse function returns all successful parses (list-of-successes, memoized per sentence),
//! the same answer an Earley parser gives over the ambiguous grammar: if a sentence has two different
//! readings, both come back, and the compiler refuses to guess.

use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use indexmap::IndexMap;

use crate::core::*;
use crate::pyfmt::close_match;
use crate::syntax::*;

fn binop(kind: &str) -> Option<(&'static str, u8)> {
    Some(match kind {
        "PLUS" => ("+", 1),
        "MINUS" => ("-", 1),
        "MUL" | "TIMES" => ("*", 2),
        "DIV" => ("/", 2),
        "MOD" => ("%", 2),
        "POW" => ("^", 3),
        _ => return None,
    })
}

fn cmpop(kind: &str) -> Option<&'static str> {
    Some(match kind {
        "GT" => ">",
        "LT" => "<",
        "GE" => ">=",
        "LE" => "<=",
        "NE" => "!=",
        "IS" => "==",
        "CONTAINS" => "contains",
        "STARTS" => "starts",
        _ => return None,
    })
}
const CMPOPS: [&str; 8] = ["GT", "LT", "GE", "LE", "NE", "IS", "CONTAINS", "STARTS"];

fn prefix_builtin(kind: &str) -> Option<Option<&'static str>> {
    Some(Some(match kind {
        "LENGTH" => "length",
        "SUM" => "sum",
        "AVERAGE" => "average",
        "LARGEST" => "largest",
        "SMALLEST" => "smallest",
        "SQRT" => "sqrt",
        "ABS" => "abs",
        "WORDSOF" => "words",
        "LINESOF" => "lines",
        "LETTERSOF" => "letters",
        "CONTENTS" => return Some(None),
        "SIN" => "sin",
        "COS" => "cos",
        "TAN" => "tan",
        "LOG" => "log",
        "CODE_OF" => "code",
        "CHAR_OF_CODE" => "char",
        "ENV" => "env",
        _ => return None,
    }))
}

fn nullary_builtin(kind: &str) -> Option<&'static str> {
    Some(match kind {
        "CURRENT_TIME" => "now",
        "TIMESTAMP" => "timestamp",
        "MILLIS" => "millis",
        "EXIT_CODE" => "exit_code",
        _ => return None,
    })
}

fn postfix_builtin(kind: &str) -> Option<&'static str> {
    Some(match kind {
        "SQUARED" => "squared",
        "ROUNDED" => "round",
        "ROUNDUP" => "round_up",
        "ROUNDDOWN" => "round_down",
        "UPPER" => "upper",
        "LOWER" => "lower",
        "TRIMMED" => "trim",
        "ASNUMBER" => "to_int",
        "ASDECIMAL" => "to_float",
        "ASTEXT" => "to_text",
        _ => return None,
    })
}

fn spec_word(w: &str) -> Option<Spec> {
    Some(match w {
        "number" | "numbers" => Spec::Int,
        "decimal" | "decimals" => Spec::Float,
        "text" | "texts" | "word" | "words" => Spec::Text,
        _ => return None,
    })
}

pub const MOMENT_FIELDS: [&str; 7] = ["year", "month", "day", "hour", "minute", "second", "weekday"];
const INDEX_WORDS: [&str; 3] = ["item", "letter", "character"];
const SLICE_WORDS: [&str; 3] = ["items", "letters", "characters"];
const VALUE_MARKERS: [&str; 4] = ["find", "calculate", "compute", "get"];
const PARAM_INTRO: [&str; 3] = ["a", "an", "some"];
const PARAM_SELF: [&str; 2] = ["someone", "somebody"];
const PLACEHOLDER: &str = "zzparam";

fn sl(toks: &[Tok], a: usize, b: usize) -> &[Tok] {
    let b = b.min(toks.len());
    if a >= b {
        &toks[0..0]
    } else {
        &toks[a..b]
    }
}

fn from(toks: &[Tok], a: usize) -> &[Tok] {
    sl(toks, a, toks.len())
}

fn unique<T>(items: Vec<T>, key: impl Fn(&T) -> String) -> Vec<T> {
    let mut seen = HashSet::new();
    items.into_iter().filter(|it| seen.insert(key(it))).collect()
}

fn ustr(items: Vec<E>) -> Vec<E> {
    unique(items, |e| e.to_string())
}

/// Indexes of tokens of the given kinds that are not inside brackets.
pub fn top_positions(toks: &[Tok], kinds: &[&str]) -> Vec<usize> {
    let mut out = Vec::new();
    let mut depth = 0i32;
    for (i, t) in toks.iter().enumerate() {
        if t.kind == "LP" {
            depth += 1;
        } else if t.kind == "RP" {
            depth -= 1;
        } else if depth == 0 && kinds.contains(&t.kind.as_str()) {
            out.push(i);
        }
    }
    out
}

pub fn split_top<'t>(toks: &'t [Tok], kinds: &[&str]) -> Vec<&'t [Tok]> {
    let mut parts = Vec::new();
    let mut start = 0;
    for k in top_positions(toks, kinds) {
        parts.push(&toks[start..k]);
        start = k + 1;
    }
    parts.push(&toks[start..]);
    parts
}

fn strip_commas(mut toks: &[Tok]) -> &[Tok] {
    while !toks.is_empty() && toks[0].kind == "COMMA" {
        toks = &toks[1..];
    }
    while !toks.is_empty() && toks[toks.len() - 1].kind == "COMMA" {
        toks = &toks[..toks.len() - 1];
    }
    toks
}

fn is_word(tok: Option<&Tok>, values: &[&str]) -> bool {
    tok.is_some_and(|t| t.is_word(values))
}

fn stmt_key(s: &S) -> String {
    s.dump("").join("\n")
}

fn english_stmt(s: &S) -> String {
    match &s.k {
        St::Assign { target, expr } => format!("set {} to {}", en(target), en(expr)),
        St::AddTo { target, expr, .. } => format!("add {} to {}", en(expr), en(target)),
        St::Print(parts) => format!("say {}", parts.iter().map(|p| en(p)).collect::<Vec<_>>().join(" and then ")),
        St::Repeat { count, body } if body.borrow().len() == 1 => {
            format!("{}, {} times", english_stmt(&body.borrow()[0]), english(count, false))
        }
        St::CallStmt(c) => en(c),
        _ => stmt_key(s),
    }
}

fn ambiguity_text(readings: &[String]) -> String {
    let mut lines = vec![format!("This sentence can mean {} different things:", readings.len())];
    for (i, r) in readings.iter().enumerate() {
        lines.push(format!("  {}) {}", i + 1, r));
    }
    lines.push("Please add brackets or rewrite it so that only one reading is possible.".into());
    lines.join("\n")
}

#[derive(Clone)]
enum Target {
    Var(Tok),
    Key(Tok, E),
    Index(E),
    Field(E),
}

fn describe(t: &Target) -> String {
    match t {
        Target::Var(tok) => tok.text.clone(),
        Target::Index(e) | Target::Field(e) => en(e),
        Target::Key(tok, key) => format!("{} of {}", tok.text, en(key)),
    }
}

fn lit_match(el: &Pat, tok: &Tok) -> bool {
    match el {
        Pat::Lit { kind, value } => {
            if &tok.kind != kind {
                return false;
            }
            if kind == "WORD" || kind == "NUM" {
                &tok.value == value
            } else {
                true
            }
        }
        Pat::Param(_) => false,
    }
}

// ───────────── kinds of things ─────────────

const KIND_INTRO: [&str; 4] = ["a", "an", "every", "each"];

pub fn is_kind_decl(text: &str) -> bool {
    let words: Vec<String> = text.split_whitespace().map(|w| w.to_lowercase()).collect();
    words.len() >= 4 && KIND_INTRO.contains(&words[0].as_str()) && (words[2] == "has" || words[2] == "have")
}

#[derive(Default)]
pub struct Kinds {
    pub by_name: IndexMap<String, Kind>,
    pub fields: IndexMap<String, Vec<String>>,
    pub by_header: HashMap<(String, u32), Kind>,
}

impl Kinds {
    pub fn add(&mut self, kind: KindInfo) {
        kind.id.set(self.by_name.len());
        let kind = Rc::new(kind);
        self.by_name.insert(kind.name.clone(), kind.clone());
        self.by_header.insert((kind.file.clone(), kind.line), kind.clone());
        for key in &kind.fields {
            self.fields.insert(key.clone(), key.split('_').map(String::from).collect());
        }
    }

    /// Field names (longest first) whose words start at toks[i].
    pub fn match_field(&self, toks: &[Tok], i: usize) -> Vec<(String, usize)> {
        let mut out = Vec::new();
        for (key, words) in &self.fields {
            let n = words.len();
            if (0..n).all(|k| i + k < toks.len() && toks[i + k].is_word(&[words[k].as_str()])) {
                out.push((key.clone(), i + n));
            }
        }
        out.sort_by_key(|c| std::cmp::Reverse(c.1));
        out
    }
}

fn parse_kind_decl(sentence: &Sentence, lx: &Lexicon, kinds: &Kinds) -> Res<KindInfo> {
    let fail = |msg: String| Err(EngError::new(msg, sentence.line, &sentence.file));
    let mut toks = canonical(&sentence.text, sentence.line, &sentence.file, lx)?;
    if !toks.is_empty() && toks[0].is_word(&["every", "each"]) {
        toks.remove(0);
    }
    if toks.len() < 3 || toks[0].kind != "WORD" || !toks[1].is_word(&["has", "have"]) {
        return fail("Write it like: a book has a title, an author and a year.".into());
    }
    let name = toks[0].value.clone();
    if kinds.by_name.contains_key(&name) {
        return fail(format!("\"{}\" is already described above.", name));
    }
    let mut fields: Vec<String> = Vec::new();
    let mut display = IndexMap::new();
    let mut specs = Vec::new();
    let rest = &toks[2..];
    let all = split_top(rest, &["COMMA", "AND"]);
    let n = all.len();
    let parts: Vec<&[Tok]> =
        all.into_iter().enumerate().filter(|(k, p)| !p.is_empty() || !(*k > 0 && !rest.is_empty() && *k < n - 1)).map(|(_, p)| p).collect();
    for part in parts {
        let mut part = part;
        let mut spec = None;
        if !part.is_empty() && part[part.len() - 1].kind == "RP" && part.iter().any(|t| t.kind == "LP") {
            let lp = part.iter().rposition(|t| t.kind == "LP").unwrap();
            spec = Some(part[lp + 1..part.len() - 1].to_vec());
            part = &part[..lp];
        }
        if part.len() > 1 && part[0].is_word(&["some"]) {
            part = &part[1..];
        }
        if part.is_empty() || part.iter().any(|t| t.kind != "WORD") {
            return fail(format!(
                "\"{}\" can't be the name of a field: use plain words that are not part of the language.",
                show(part)
            ));
        }
        let key = part.iter().map(|t| t.value.clone()).collect::<Vec<_>>().join("_");
        if fields.contains(&key) {
            return fail(format!("A {} already has a {}.", name, part.iter().map(|t| t.text.clone()).collect::<Vec<_>>().join(" ")));
        }
        fields.push(key.clone());
        display.insert(key.clone(), part.iter().map(|t| t.text.clone()).collect::<Vec<_>>().join(" "));
        if let Some(s) = spec {
            specs.push((key, s));
        }
    }
    Ok(KindInfo {
        name,
        display: toks[0].text.clone(),
        fields,
        field_display: display,
        id: Cell::new(0),
        line: sentence.line,
        file: sentence.file.clone(),
        raw_specs: specs,
        field_specs: RefCell::new(IndexMap::new()),
    })
}

/// A written type: number, decimal, text, yes/no, a book, a list of books, a table of numbers.
fn spec_of(toks: &[Tok], kinds: &Kinds) -> Option<Spec> {
    let toks: Vec<Tok> = toks.iter().filter(|t| !t.is_word(&["a", "an"])).cloned().collect();
    if toks.is_empty() {
        return None;
    }
    let t = &toks[0];
    if t.kind == "LISTOF" {
        return spec_of(&toks[1..], kinds).map(|s| Spec::List(Box::new(s)));
    }
    if t.is_word(&["list"]) && toks.len() > 1 && toks[1].is_word(&["of"]) {
        return spec_of(&toks[2..], kinds).map(|s| Spec::List(Box::new(s)));
    }
    if t.is_word(&["table"]) && toks.len() > 1 && toks[1].is_word(&["of"]) {
        return spec_of(&toks[2..], kinds).map(|s| Spec::Table(Box::new(s)));
    }
    if toks.len() != 1 && !(toks.len() == 3 && toks[1].text == "/") {
        return None;
    }
    let words: String = toks.iter().map(|x| x.text.to_lowercase()).collect();
    if ["yes/no", "boolean", "truth"].contains(&words.as_str()) {
        return Some(Spec::Bool);
    }
    if toks.len() != 1 {
        return None;
    }
    let w = t.text.to_lowercase();
    if let Some(s) = spec_word(&w) {
        return Some(s);
    }
    let name = if w.ends_with('s') && kinds.by_name.contains_key(&w[..w.len() - 1]) { w[..w.len() - 1].to_string() } else { w };
    kinds.by_name.get(&name).map(|k| Spec::Kind(k.clone()))
}

/// Turn the written field types into specs; a field named like a kind holds that kind.
fn resolve_field_specs(kinds: &Kinds) -> Res<()> {
    for k in kinds.by_name.values() {
        for (key, raw) in &k.raw_specs {
            match spec_of(raw, kinds) {
                Some(spec) => {
                    k.field_specs.borrow_mut().insert(key.clone(), spec);
                }
                None => {
                    return Err(EngError::new(
                        format!(
                            "I don't know the type \"{}\". Use number, decimal, text, yes/no, the name of a kind, or \"a list of ...\" and \"a table of ...\".",
                            show(raw)
                        ),
                        k.line,
                        &k.file,
                    ))
                }
            }
        }
        for key in &k.fields {
            if !k.field_specs.borrow().contains_key(key) {
                if let Some(other) = kinds.by_name.get(key) {
                    k.field_specs.borrow_mut().insert(key.clone(), Spec::Kind(other.clone()));
                }
            }
        }
    }
    Ok(())
}

// ───────────── function headers ─────────────

/// Raw tokens of a "to ..." sentence: header words and the inline body (after the first comma).
fn header_split(text: &str, line: u32, file: &str) -> Res<(Vec<Tok>, Option<Vec<Tok>>)> {
    let raw = tokenize(text, line, file)?;
    let cut = raw.iter().position(|t| t.kind == "COMMA");
    Ok(match cut {
        Some(c) => (sl(&raw, 1, c).to_vec(), Some(raw[c + 1..].to_vec())),
        None => (from(&raw, 1).to_vec(), None),
    })
}

fn parse_header(sentence: &Sentence, lx: &Lexicon, number: usize, kinds: &Kinds) -> Res<FuncSig> {
    let (words, _) = header_split(&sentence.text, sentence.line, &sentence.file)?;
    let fail = |msg: String| Err(EngError::new(msg, sentence.line, &sentence.file));
    let mut params: Vec<String> = Vec::new();
    let mut parts: Vec<Tok> = Vec::new();
    let mut k = 0usize;
    while k < words.len() {
        let w = &words[k];
        let (name, step) = if w.kind == "WORD" && PARAM_INTRO.contains(&w.value.as_str()) && k + 1 < words.len() && words[k + 1].kind == "WORD" {
            (words[k + 1].clone(), 2)
        } else if w.kind == "WORD" && PARAM_SELF.contains(&w.value.as_str()) {
            (w.clone(), 1)
        } else {
            parts.push(w.clone());
            k += 1;
            continue;
        };
        if apply_lexicon(&[Tok::word(&name.value, &name.text)], lx)[0].kind != "WORD" {
            return fail(format!("\"{}\" is a word the language already uses. Pick another name for this value.", name.text));
        }
        if params.contains(&name.value) {
            return fail(format!("\"{}\" is used twice in this definition. Give each value its own name.", name.text));
        }
        parts.push(Tok::word(&format!("{}{}", PLACEHOLDER, params.len()), &name.text));
        params.push(name.value.clone());
        k += step;
    }
    let mut toks = apply_lexicon(&normalize(&parts, lx), lx);
    let mut returns = false;
    if !toks.is_empty() && toks[0].is_word(&VALUE_MARKERS) {
        toks.remove(0);
        returns = true;
    } else if toks.len() > 1 && toks[0].is_word(&["work"]) && toks[1].is_word(&["out"]) {
        toks.drain(0..2);
        returns = true;
    }
    let mut pattern = Vec::new();
    for t in &toks {
        if t.kind == "WORD" && t.value.starts_with(PLACEHOLDER) {
            let n: usize = t.value[PLACEHOLDER.len()..].parse().unwrap();
            pattern.push(Pat::Param(params[n].clone()));
        } else {
            let value = if t.kind == "WORD" || t.kind == "NUM" { t.value.clone() } else { t.text.clone() };
            pattern.push(Pat::Lit { kind: t.kind.clone(), value });
        }
    }
    let lits: Vec<&Pat> = pattern.iter().filter(|p| matches!(p, Pat::Lit { .. })).collect();
    if lits.is_empty() {
        return fail("A definition needs at least one word besides its values, for example: to greet someone.".into());
    }
    let lead = if matches!(pattern[0], Pat::Lit { .. }) { Some(&pattern[0]) } else { pattern.get(1) };
    let ok = matches!(lead, Some(Pat::Lit { kind, .. }) if kind == "WORD");
    if !ok {
        let what = match lead {
            Some(Pat::Lit { value, .. }) => value.clone(),
            _ => "?".into(),
        };
        return fail(format!(
            "This definition starts with \"{}\", which is already part of the language. Start it with a word of your own, for example: to find the area of a width and a height.",
            what
        ));
    }
    if matches!(pattern[0], Pat::Param(_)) && !returns {
        return fail("A command has to start with a word, for example: to greet someone.".into());
    }
    let joined = lits
        .iter()
        .map(|p| match p {
            Pat::Lit { value, .. } => value.clone(),
            _ => unreachable!(),
        })
        .collect::<Vec<_>>()
        .join("_");
    let name: String = joined.chars().map(|c| if c.is_alphanumeric() { c } else { '_' }).collect::<String>() + &format!("_{}", number);
    let display = words.iter().map(|w| w.text.clone()).collect::<Vec<_>>().join(" ");
    let param_kinds = params.iter().filter_map(|p| kinds.by_name.get(p).map(|k| (p.clone(), k.clone()))).collect();
    Ok(FuncSig {
        name,
        display,
        pattern,
        params,
        returns,
        line: sentence.line,
        file: sentence.file.clone(),
        body: body(vec![]),
        uses_bot: Cell::new(false),
        param_kinds,
        origin: None,
    })
}

#[derive(Default)]
pub struct Funcs {
    pub sigs: Vec<Sig>,
    pub by_header: HashMap<(String, u32), Sig>,
    pub kinds: Kinds,
}

impl Funcs {
    fn add(&mut self, sig: FuncSig) {
        let sig = Rc::new(sig);
        self.by_header.insert((sig.file.clone(), sig.line), sig.clone());
        self.sigs.push(sig);
    }

    fn prefix(&self, tok: &Tok) -> Vec<Sig> {
        self.sigs.iter().filter(|s| s.returns && matches!(s.pattern[0], Pat::Lit { .. }) && lit_match(&s.pattern[0], tok)).cloned().collect()
    }

    fn postfix(&self, tok: &Tok) -> Vec<Sig> {
        self.sigs
            .iter()
            .filter(|s| s.returns && matches!(s.pattern[0], Pat::Param(_)) && s.pattern.len() > 1 && lit_match(&s.pattern[1], tok))
            .cloned()
            .collect()
    }

    fn commands(&self, tok: &Tok) -> Vec<Sig> {
        self.sigs.iter().filter(|s| !s.returns && lit_match(&s.pattern[0], tok)).cloned().collect()
    }

    fn first_words(&self) -> HashSet<String> {
        self.sigs
            .iter()
            .filter_map(|s| match &s.pattern[0] {
                Pat::Lit { value, .. } => Some(value.clone()),
                _ => None,
            })
            .collect()
    }
}

// ───────────── the parser ─────────────

pub enum Node {
    Stmt(S),
    End,
    Else(Option<S>),
    OnError(Option<S>),
    Empty,
}

type Cands = Vec<(E, usize)>;

struct Parser<'a> {
    lx: &'a Lexicon,
    funcs: &'a Funcs,
    kinds: &'a Kinds,
    tables: HashSet<String>,
    globals: IndexMap<String, String>,
    user_vars: IndexMap<String, String>,
    func: Option<Sig>,
    locals: IndexMap<String, String>,
    visible: HashSet<String>,
    last_var: Option<String>,
    saved_last: Option<String>,
    line: u32,
    file: String,
    memo: HashMap<(u8, usize, usize, usize), Cands>,
}

const BEST: [(&str, bool); 7] =
    [("largest", true), ("highest", true), ("biggest", true), ("most", true), ("smallest", false), ("lowest", false), ("least", false)];

impl<'a> Parser<'a> {
    fn err<T>(&self, msg: impl Into<String>) -> Res<T> {
        Err(EngError::new(msg, self.line, &self.file))
    }

    fn at(&self, k: St) -> S {
        Rc::new(Stmt { line: self.line, file: self.file.clone(), k })
    }

    // ── names ──

    fn scope_of(&self, name: &str) -> Option<&'static str> {
        if name.starts_with(USER) {
            return if self.user_vars.contains_key(name) { Some("user") } else { None };
        }
        if self.func.is_some() {
            if self.locals.contains_key(name) {
                return Some("local");
            }
            return if self.visible.contains(name) { Some("global") } else { None };
        }
        if self.globals.contains_key(name) {
            Some("global")
        } else {
            None
        }
    }

    fn make_var(&self, name: &str) -> E {
        let scope = self.scope_of(name).expect("known name");
        let display = match scope {
            "user" => &self.user_vars[name],
            "local" => &self.locals[name],
            _ => &self.globals[name],
        };
        let func = if scope == "local" { self.func.as_ref().unwrap().name.clone() } else { String::new() };
        ex(Ex::Var(VarRef { name: name.into(), display: display.clone(), scope: scope.into(), func }))
    }

    fn define(&mut self, tok: &Tok) -> E {
        let name = tok.value.clone();
        if self.scope_of(&name).is_none() {
            if name.starts_with(USER) {
                self.user_vars.insert(name.clone(), tok.text.clone());
            } else if self.func.is_some() {
                self.locals.insert(name.clone(), tok.text.clone());
            } else {
                self.globals.insert(name.clone(), tok.text.clone());
            }
        }
        self.last_var = Some(name.clone());
        self.make_var(&name)
    }

    fn enter_function(&mut self, sig: &Sig) {
        self.func = Some(sig.clone());
        self.locals = sig.params.iter().map(|p| (p.clone(), p.clone())).collect();
        self.visible = self.globals.keys().cloned().collect();
        self.saved_last = self.last_var.take();
    }

    fn exit_function(&mut self) {
        if self.func.is_some() {
            self.func = None;
            self.locals.clear();
            self.last_var = self.saved_last.clone();
        }
    }

    // ── expressions ──

    /// Values separated by commas, "and" or "or": one of "a", "b" and "c".
    fn item_list(&mut self, toks: &[Tok], mut i: usize) -> Res<(Vec<E>, usize)> {
        let mut items = Vec::new();
        loop {
            let got = self.primaries(toks, i)?;
            if got.is_empty() {
                break;
            }
            let mut best = got[0].clone();
            for c in &got {
                if c.1 > best.1 {
                    best = c.clone();
                }
            }
            items.push(best.0);
            i = best.1;
            if i < toks.len() && ["COMMA", "AND", "OR"].contains(&toks[i].kind.as_str()) {
                i += 1;
            } else {
                break;
            }
        }
        Ok((items, i))
    }

    fn atoms(&mut self, toks: &[Tok], i: usize) -> Res<Cands> {
        if i >= toks.len() {
            return Ok(vec![]);
        }
        let t = &toks[i];
        let k = t.kind.as_str();
        let one = |e: E| Ok(vec![(e, i + 1)]);
        match k {
            "NUM" => return one(num(t.num)),
            "DEC" => return one(ex(Ex::Dec(t.dec))),
            "MINUS" if t.sym && i + 1 < toks.len() && ["NUM", "DEC"].contains(&toks[i + 1].kind.as_str()) => {
                let n = &toks[i + 1];
                let e = if n.kind == "NUM" { num(n.num.wrapping_neg()) } else { ex(Ex::Dec(-n.dec)) };
                return Ok(vec![(e, i + 2)]);
            }
            "STR" => return one(text(&t.value)),
            "NEWLINE" => return one(text("\n")),
            "MSG" => return one(ex(Ex::Msg)),
            "NAME" => return one(ex(Ex::Name)),
            "MSGNUM" => return one(ex(Ex::MsgNum)),
            "ARGS" => return one(builtin("arguments", vec![])),
            "EMPTYLIST" => return one(ex(Ex::ListLit(vec![]))),
            "EMPTYMAP" => return one(ex(Ex::EmptyMap)),
            "NOTHING" => return one(ex(Ex::Nothing)),
            "ERROR" => return one(ex(Ex::ErrorText)),
            _ => {}
        }
        if let Some(n) = nullary_builtin(k) {
            return one(builtin(n, vec![]));
        }
        if k == "PI" {
            return one(ex(Ex::Dec(std::f64::consts::PI)));
        }
        if k == "RANDOM_DEC" {
            let mut res = Vec::new();
            if i + 1 < toks.len() && toks[i + 1].kind == "FROM" {
                for (lo, j) in self.exprs(toks, i + 2)? {
                    if j < toks.len() && (toks[j].kind == "TO" || toks[j].kind == "AND") {
                        for (hi, m) in self.tight(toks, j + 1)? {
                            res.push((builtin("random_float", vec![lo.clone(), hi]), m));
                        }
                    }
                }
            }
            if res.is_empty() {
                return self.err("Write it like: a random decimal from 0 to 1.");
            }
            return Ok(res);
        }
        if k == "POSITION" {
            let mut res = Vec::new();
            for (x, j) in self.exprs(toks, i + 1)? {
                if is_word(toks.get(j), &["in"]) {
                    for (t2, m) in self.tight(toks, j + 1)? {
                        res.push((builtin("position", vec![x.clone(), t2]), m));
                    }
                }
            }
            return Ok(res);
        }
        if k == "LISTOF" {
            let (items, j) = self.item_list(toks, i + 1)?;
            return Ok(if items.is_empty() { vec![] } else { vec![(ex(Ex::ListLit(items)), j)] });
        }
        if k == "ONEOF" {
            let (items, j) = self.item_list(toks, i + 1)?;
            if items.is_empty() {
                return self.err("Write it like: one of \"yes\", \"no\" and \"maybe\", or: one of the guests.");
            }
            let e = if items.len() == 1 { ex(Ex::RandomItem(items[0].clone())) } else { ex(Ex::OneOf(items)) };
            return Ok(vec![(e, j)]);
        }
        if k == "RANDOM" {
            let ok = i + 1 < toks.len() && toks[i + 1].kind == "FROM";
            let lo = if ok { self.atoms(toks, i + 2)? } else { vec![] };
            if !lo.is_empty() && lo[0].1 < toks.len() && (toks[lo[0].1].kind == "TO" || toks[lo[0].1].kind == "AND") {
                let hi = self.atoms(toks, lo[0].1 + 1)?;
                if !hi.is_empty() {
                    return Ok(vec![(ex(Ex::Random(lo[0].0.clone(), hi[0].0.clone())), hi[0].1)]);
                }
            }
            return self.err("Write it like: a random number from 1 to 6.");
        }
        if k == "REM" {
            let mut res = Vec::new();
            for (a, j) in self.exprs(toks, i + 1)? {
                if j < toks.len() && toks[j].kind == "DIV" {
                    for (b, m) in self.tight(toks, j + 1)? {
                        res.push((bin("%", a.clone(), b), m));
                    }
                }
            }
            if res.is_empty() {
                return self.err("Write it like: the remainder of n divided by 3.");
            }
            return Ok(res);
        }
        if let Some(name) = prefix_builtin(k) {
            return Ok(self
                .tight(toks, i + 1)?
                .into_iter()
                .map(|(x, j)| (match name {
                    None => x,
                    Some(n) => builtin(n, vec![x]),
                }, j))
                .collect());
        }
        if k == "IT" {
            match self.last_var.clone() {
                None => return self.err("I don't know what \"it\" means here. Nothing has been set yet."),
                Some(v) => return one(self.make_var(&v)),
            }
        }
        if k == "LP" {
            let mut depth = 0i32;
            for j in i..toks.len() {
                depth += match toks[j].kind.as_str() {
                    "LP" => 1,
                    "RP" => -1,
                    _ => 0,
                };
                if depth == 0 {
                    let inner = &toks[i + 1..j];
                    let mut res: Cands = self.expr_full(inner)?.into_iter().map(|e| (e, j + 1)).collect();
                    let items = split_top(inner, &["COMMA", "AND"]);
                    if items.len() > 1 && items.iter().all(|it| !it.is_empty()) {
                        let mut parsed = Vec::new();
                        for it in &items {
                            parsed.push(ustr(self.expr_full(it)?));
                        }
                        if parsed.iter().all(|p| p.len() == 1) {
                            res.push((ex(Ex::ListLit(parsed.into_iter().map(|p| p[0].clone()).collect())), j + 1));
                        }
                    }
                    for e in self.whole_values(inner)? {
                        res.push((e, j + 1));
                    }
                    if inner.iter().any(|t| t.kind == "CONCAT") {
                        for (parts, m) in self.print_args(inner, 0)? {
                            if m == inner.len() && parts.len() > 1 {
                                res.push((ex(Ex::Concat(parts)), j + 1));
                            }
                        }
                    }
                    return Ok(res);
                }
            }
            return self.err("A bracket is opened but never closed.");
        }
        if k != "WORD" {
            return Ok(vec![]);
        }

        let mut res: Cands = Vec::new();
        for sig in self.funcs.prefix(t) {
            for (a, j) in self.matches(&sig.pattern, toks, i, vec![], true)? {
                res.push((call(sig.clone(), a), j));
            }
        }
        if t.value.starts_with(USER) && !self.user_vars.contains_key(&t.value) {
            self.user_vars.insert(t.value.clone(), t.text.clone()); // every user starts with zero / empty
        }
        let known = self.scope_of(&t.value).is_some();
        if known {
            res.push((self.make_var(&t.value), i + 1));
        }
        let nxt = toks.get(i + 1);
        if t.value == "new" {
            if let Some(n) = nxt {
                if n.kind == "WORD" {
                    if let Some(kind) = self.kinds.by_name.get(&n.value) {
                        res.extend(self.new_object(kind.clone(), toks, i + 2)?);
                    }
                }
            }
        }
        if !self.tables.contains(&t.value) {
            for (key, j) in self.kinds.match_field(toks, i) {
                if is_word(toks.get(j), &["of"]) {
                    let display = toks[i..j].iter().map(|x| x.text.clone()).collect::<Vec<_>>().join(" ");
                    for (o, m) in self.tight(toks, j + 1)? {
                        res.push((field(o, &key, &display), m));
                    }
                }
            }
        }
        if t.value.contains('.') && !t.value.starts_with(USER) {
            let (base, fld) = t.value.split_once('.').unwrap();
            if self.scope_of(base).is_some() && self.kinds.fields.contains_key(fld) {
                res.push((field(self.make_var(base), fld, &fld.replace('_', " ")), i + 1));
            }
        }
        if INDEX_WORDS.contains(&t.value.as_str()) {
            for (n, j) in self.exprs(toks, i + 1)? {
                if is_word(toks.get(j), &["of"]) {
                    for (c, m) in self.tight(toks, j + 1)? {
                        res.push((index(c, Some(n.clone())), m));
                    }
                }
            }
        } else if SLICE_WORDS.contains(&t.value.as_str()) {
            for (a, j) in self.exprs(toks, i + 1)? {
                if j < toks.len() && toks[j].kind == "TO" {
                    for (b, m) in self.exprs(toks, j + 1)? {
                        if is_word(toks.get(m), &["of"]) {
                            for (c, p) in self.tight(toks, m + 1)? {
                                res.push((ex(Ex::Slice(c, a.clone(), b.clone())), p));
                            }
                        }
                    }
                }
            }
        } else if (t.value == "first" || t.value == "last")
            && nxt.is_some_and(|n| n.is_word(&["item", "letter", "character", "one"]))
            && is_word(toks.get(i + 2), &["of"])
        {
            for (c, m) in self.tight(toks, i + 3)? {
                let idx = if t.value == "first" { Some(num(1)) } else { None };
                res.push((index(c, idx), m));
            }
        } else if t.value == "file" && nxt.is_some() {
            for (p, j) in self.tight(toks, i + 1)? {
                res.push((builtin("file_text", vec![p]), j));
            }
        }
        if known {
            return Ok(res);
        }
        if t.value.contains('.') {
            if res.is_empty() {
                return self.err(format!("I don't know \"{}\" yet. Set it first, for example: set {} to 0.", t.text, t.text));
            }
            return Ok(res);
        }
        if res.iter().any(|(e, _)| matches!(e.k, Ex::Call { .. })) {
            return Ok(res);
        }
        res.push((text(&t.text), i + 1)); // an unknown single word is just text
        Ok(res)
    }

    /// "X of Y" is an entry of table X if X was made a table, or if no field or function uses that word.
    fn may_be_table(&self, name: &str) -> bool {
        if self.tables.contains(name) {
            return true;
        }
        !self.kinds.fields.contains_key(name) && !self.funcs.first_words().contains(name)
    }

    /// What JSON text turns into: a book, a list of books, a table of numbers, a list of texts...
    fn type_spec(&self, toks: &[Tok], i: usize) -> Vec<(Spec, usize)> {
        if i >= toks.len() {
            return vec![];
        }
        let t = &toks[i];
        if t.kind == "LISTOF" {
            return self.type_spec(toks, i + 1).into_iter().map(|(s, j)| (Spec::List(Box::new(s)), j)).collect();
        }
        if t.is_word(&["table"]) && is_word(toks.get(i + 1), &["of"]) {
            return self.type_spec(toks, i + 2).into_iter().map(|(s, j)| (Spec::Table(Box::new(s)), j)).collect();
        }
        if t.kind == "WORD" {
            if let Some(s) = spec_word(&t.value) {
                return vec![(s, i + 1)];
            }
            let v = &t.value;
            let name = if v.ends_with('s') && self.kinds.by_name.contains_key(&v[..v.len() - 1]) { &v[..v.len() - 1] } else { v.as_str() };
            if let Some(k) = self.kinds.by_name.get(name) {
                return vec![(Spec::Kind(k.clone()), i + 1)];
            }
        }
        vec![]
    }

    /// a new book [with title "Dune", author "Frank Herbert" and year 1965]
    fn new_object(&mut self, kind: Kind, toks: &[Tok], i: usize) -> Res<Cands> {
        let mut res = vec![(ex(Ex::New(kind.clone(), vec![])), i)];
        if !is_word(toks.get(i), &["with"]) {
            return Ok(res);
        }
        res.extend(self.inits(&kind, toks, i + 1, vec![])?);
        Ok(res)
    }

    fn inits(&mut self, kind: &Kind, toks: &[Tok], j: usize, done: Vec<(String, E)>) -> Res<Cands> {
        let mut out = Vec::new();
        for (key, k) in self.kinds.match_field(toks, j) {
            if !kind.fields.contains(&key) || done.iter().any(|(d, _)| d == &key) {
                continue;
            }
            for (e, m) in self.exprs(toks, k)? {
                let mut now = done.clone();
                now.push((key.clone(), e));
                out.push((ex(Ex::New(kind.clone(), now.clone())), m));
                if m < toks.len() && (toks[m].kind == "COMMA" || toks[m].kind == "AND") {
                    let nxt = if toks[m].kind == "COMMA" && m + 1 < toks.len() && toks[m + 1].kind == "AND" { m + 2 } else { m + 1 };
                    out.extend(self.inits(kind, toks, nxt, now)?);
                }
            }
        }
        Ok(out)
    }

    fn postfixes(&mut self, a: &E, toks: &[Tok], j: usize) -> Res<Cands> {
        if j >= toks.len() {
            return Ok(vec![]);
        }
        let t = &toks[j];
        let k = t.kind.as_str();
        if let Some(n) = postfix_builtin(k) {
            return Ok(vec![(builtin(n, vec![a.clone()]), j + 1)]);
        }
        if k == "JOINED_TOGETHER" {
            return Ok(vec![(builtin("join", vec![a.clone(), text("")]), j + 1)]);
        }
        if k == "ASJSON" {
            return Ok(vec![(builtin("to_json", vec![a.clone()]), j + 1)]);
        }
        if k == "PADDED" || k == "PADDED_LEFT" {
            let name = if k == "PADDED" { "pad" } else { "pad_left" };
            return Ok(self.tight(toks, j + 1)?.into_iter().map(|(w, m)| (builtin(name, vec![a.clone(), w]), m)).collect());
        }
        if t.is_word(&["as"]) {
            return Ok(self.type_spec(toks, j + 1).into_iter().map(|(spec, m)| (ex(Ex::FromJson(a.clone(), spec)), m)).collect());
        }
        if t.is_word(&["repeated"]) {
            return Ok(self
                .exprs(toks, j + 1)?
                .into_iter()
                .filter(|(_, m)| *m < toks.len() && toks[*m].kind == "TIMES")
                .map(|(n, m)| (builtin("repeat_text", vec![a.clone(), n]), m + 1))
                .collect());
        }
        if k == "JOINED" {
            return Ok(self.primaries(toks, j + 1)?.into_iter().map(|(s, m)| (builtin("join", vec![a.clone(), s]), m)).collect());
        }
        if k == "SPLIT" {
            return Ok(self.primaries(toks, j + 1)?.into_iter().map(|(s, m)| (builtin("split", vec![a.clone(), s]), m)).collect());
        }
        let mut res = Vec::new();
        if t.is_word(&["with"]) {
            for (x, m) in self.exprs(toks, j + 1)? {
                if m < toks.len() && toks[m].kind == "REPLACED" {
                    for (y, p) in self.primaries(toks, m + 1)? {
                        res.push((builtin("replace", vec![a.clone(), x.clone(), y]), p));
                    }
                }
                if is_word(toks.get(m), &["decimals", "decimal"]) {
                    res.push((builtin("decimals", vec![a.clone(), x.clone()]), m + 1));
                }
            }
        }
        for sig in self.funcs.postfix(t) {
            for (args, m) in self.matches(&sig.pattern[1..], toks, j, vec![a.clone()], true)? {
                res.push((call(sig.clone(), args), m));
            }
        }
        Ok(res)
    }

    fn key(tag: u8, toks: &[Tok], i: usize) -> (u8, usize, usize, usize) {
        (tag, toks.as_ptr() as usize, toks.len(), i)
    }

    /// The value right after "of": an atom, plus table lookups. Endings like "rounded" or
    /// "joined with" belong to the whole phrase, not to this value.
    fn tight(&mut self, toks: &[Tok], i: usize) -> Res<Cands> {
        let key = Self::key(b't', toks, i);
        if let Some(hit) = self.memo.get(&key) {
            return Ok(hit.clone());
        }
        let mut res = Vec::new();
        let mut todo: std::collections::VecDeque<(E, usize)> = self.atoms(toks, i)?.into();
        while let Some((a, j)) = todo.pop_front() {
            res.push((a.clone(), j));
            if let Ex::Var(v) = &a.k {
                if j < toks.len() && toks[j].is_word(&["of"]) && self.may_be_table(&v.name) {
                    for (k, m) in self.tight(toks, j + 1)? {
                        todo.push_back((lookup(a.clone(), k, false), m));
                    }
                }
            }
        }
        self.memo.insert(key, res.clone());
        Ok(res)
    }

    fn primaries(&mut self, toks: &[Tok], i: usize) -> Res<Cands> {
        let key = Self::key(b'p', toks, i);
        if let Some(hit) = self.memo.get(&key) {
            return Ok(hit.clone());
        }
        let mut res = Vec::new();
        let mut todo: std::collections::VecDeque<(E, usize)> = self.tight(toks, i)?.into();
        while let Some((a, j)) = todo.pop_front() {
            res.push((a.clone(), j));
            todo.extend(self.postfixes(&a, toks, j)?);
        }
        self.memo.insert(key, res.clone());
        Ok(res)
    }

    fn matches(&mut self, pattern: &[Pat], toks: &[Tok], i: usize, args: Vec<E>, tight_last: bool) -> Res<Vec<(Vec<E>, usize)>> {
        if pattern.is_empty() {
            return Ok(vec![(args, i)]);
        }
        match &pattern[0] {
            Pat::Lit { .. } => {
                if i < toks.len() && lit_match(&pattern[0], &toks[i]) {
                    return self.matches(&pattern[1..], toks, i + 1, args, tight_last);
                }
                Ok(vec![])
            }
            Pat::Param(_) => {
                let cands = if pattern.len() == 1 && tight_last { self.tight(toks, i)? } else { self.exprs(toks, i)? };
                let mut res = Vec::new();
                for (e, j) in cands {
                    let mut a = args.clone();
                    a.push(e);
                    res.extend(self.matches(&pattern[1..], toks, j, a, tight_last)?);
                }
                Ok(res)
            }
        }
    }

    /// All operand/operator chains starting at i: (operands, operators, end).
    #[allow(clippy::type_complexity)]
    fn seqs(&mut self, toks: &[Tok], i: usize) -> Res<Vec<(Vec<E>, Vec<(&'static str, u8, bool)>, usize)>> {
        let mut res = Vec::new();
        for (a, j) in self.primaries(toks, i)? {
            res.push((vec![a.clone()], vec![], j));
            if j < toks.len() {
                if let Some((op, prec)) = binop(&toks[j].kind) {
                    let sym = toks[j].sym;
                    for (more, ops, k) in self.seqs(toks, j + 1)? {
                        let mut o = vec![a.clone()];
                        o.extend(more);
                        let mut p = vec![(op, prec, sym)];
                        p.extend(ops);
                        res.push((o, p, k));
                    }
                }
            }
        }
        Ok(res)
    }

    /// All bracketings allowed by the precedence rules. Symbols (+ * /) follow school maths. Word
    /// operators of the same strength read left to right ("to the power of" reads right to left).
    /// Spoken English has no precedence between "plus" and "times", so "1 plus 2 times 3" keeps both
    /// bracketings and becomes an ambiguity error.
    fn trees(operands: &[E], ops: &[(&'static str, u8, bool)]) -> Vec<E> {
        type Tree = (E, Option<(u8, bool)>);
        fn build(i: usize, j: usize, operands: &[E], ops: &[(&'static str, u8, bool)], memo: &mut HashMap<(usize, usize), Vec<Tree>>) -> Vec<Tree> {
            if let Some(hit) = memo.get(&(i, j)) {
                return hit.clone();
            }
            if i == j {
                return vec![(operands[i].clone(), None)];
            }
            let constrained = |a: (u8, bool), b: (&str, u8, bool)| a.1 || b.2 || a.0 == b.1;
            let mut res = Vec::new();
            for k in i..j {
                let op = ops[k];
                let right_assoc = op.1 == 3;
                for (l, lt) in build(i, k, operands, ops, memo) {
                    if matches!(l.k, Ex::New(..)) {
                        continue; // a new thing is never a number: "with mass 2 times m" binds inside
                    }
                    if let Some(lt) = lt {
                        if constrained(lt, op) && (if right_assoc { lt.0 <= op.1 } else { lt.0 < op.1 }) {
                            continue;
                        }
                    }
                    for (r, rt) in build(k + 1, j, operands, ops, memo) {
                        if matches!(r.k, Ex::New(..)) {
                            continue;
                        }
                        if let Some(rt) = rt {
                            if constrained(rt, op) && (if right_assoc { rt.0 < op.1 } else { rt.0 <= op.1 }) {
                                continue;
                            }
                        }
                        res.push((bin(op.0, l.clone(), r), Some((op.1, op.2))));
                    }
                }
            }
            memo.insert((i, j), res.clone());
            res
        }
        let mut memo = HashMap::new();
        build(0, operands.len() - 1, operands, ops, &mut memo).into_iter().map(|(t, _)| t).collect()
    }

    fn exprs(&mut self, toks: &[Tok], i: usize) -> Res<Cands> {
        let key = Self::key(b'e', toks, i);
        if let Some(hit) = self.memo.get(&key) {
            return Ok(hit.clone());
        }
        let mut res = Vec::new();
        for (operands, ops, j) in self.seqs(toks, i)? {
            for t in Self::trees(&operands, &ops) {
                res.push((t, j));
            }
        }
        self.memo.insert(key, res.clone());
        Ok(res)
    }

    fn expr_full(&mut self, toks: &[Tok]) -> Res<Vec<E>> {
        Ok(self.exprs(toks, 0)?.into_iter().filter(|(_, j)| *j == toks.len()).map(|(e, _)| e).collect())
    }

    fn choose<T>(&self, cands: Vec<T>, what: &str, toks: &[Tok], render: impl Fn(&T) -> String, key: impl Fn(&T) -> String) -> Res<T> {
        let mut cands = unique(cands, key);
        if cands.is_empty() {
            return self.err(format!("I couldn't understand {}: \"{}\".", what, show(toks)));
        }
        if cands.len() > 1 {
            let readings: Vec<String> = cands.iter().map(render).collect();
            let mut e = EngError::new(ambiguity_text(&readings), self.line, &self.file);
            e.ambiguity = true;
            return Err(e);
        }
        Ok(cands.remove(0))
    }

    fn choose_e(&self, cands: Vec<E>, what: &str, toks: &[Tok]) -> Res<E> {
        self.choose(cands, what, toks, |e| en(e), |e| e.to_string())
    }

    fn one_expr(&mut self, toks: &[Tok], what: &str) -> Res<E> {
        let c = self.expr_full(toks)?;
        self.choose_e(c, what, toks)
    }

    // ── conditions ──

    fn conds(&mut self, toks: &[Tok]) -> Res<Vec<E>> {
        Ok(ustr(self.cond_or(toks)?))
    }

    fn flatten(op: &str, l: E, r: E) -> E {
        let mut args = vec![l];
        match &r.k {
            Ex::Logic(o, xs) if o == op => args.extend(xs.iter().cloned()),
            _ => args.push(r.clone()),
        }
        logic(op, args)
    }

    fn cond_or(&mut self, toks: &[Tok]) -> Res<Vec<E>> {
        let mut res = self.cond_and(toks)?;
        for k in top_positions(toks, &["OR"]) {
            for l in self.cond_and(&toks[..k])? {
                for r in self.cond_or(&toks[k + 1..])? {
                    res.push(Self::flatten("or", l.clone(), r));
                }
            }
        }
        Ok(res)
    }

    fn cond_and(&mut self, toks: &[Tok]) -> Res<Vec<E>> {
        let mut res = self.cond_atom(toks)?;
        for k in top_positions(toks, &["AND"]) {
            for l in self.cond_atom(&toks[..k])? {
                for r in self.cond_and(&toks[k + 1..])? {
                    res.push(Self::flatten("and", l.clone(), r));
                }
            }
        }
        Ok(res)
    }

    fn cond_atom(&mut self, toks: &[Tok]) -> Res<Vec<E>> {
        if toks.is_empty() {
            return Ok(vec![]);
        }
        if toks[0].kind == "NOT" {
            return Ok(self.cond_atom(&toks[1..])?.into_iter().map(|c| logic("not", vec![c])).collect());
        }
        let mut res = Vec::new();
        let last = toks[toks.len() - 1].kind.clone();
        let head = &toks[..toks.len() - 1];
        if last == "KNOWN" || last == "UNKNOWN" {
            for e in self.expr_full(head)? {
                if let Ex::Lookup { table, key, .. } = &e.k {
                    let known = builtin("known", vec![table.clone(), key.clone()]);
                    res.push(if last == "KNOWN" { known } else { logic("not", vec![known]) });
                }
            }
        }
        if last == "ISNUMBER" || last == "ISNOTNUMBER" {
            for e in self.expr_full(head)? {
                let test = builtin("is_number", vec![e]);
                res.push(if last == "ISNUMBER" { test } else { logic("not", vec![test]) });
            }
        }
        if last == "ISNOTHING" || last == "ISSOMETHING" {
            for e in self.expr_full(head)? {
                res.push(cmp(if last == "ISNOTHING" { "==" } else { "!=" }, e, ex(Ex::Nothing)));
            }
        }
        if last == "EXISTS" && toks[0].is_word(&["file"]) {
            for p in self.expr_full(sl(toks, 1, toks.len() - 1))? {
                res.push(builtin("file_exists", vec![p]));
            }
        }
        let cmps = top_positions(toks, &CMPOPS);
        for &k in &cmps {
            let op = cmpop(&toks[k].kind).unwrap();
            let ls = self.expr_full(&toks[..k])?;
            for l in ls {
                for r in self.expr_full(&toks[k + 1..])? {
                    res.push(cmp(op, l.clone(), r));
                }
            }
        }
        if cmps.is_empty() && !["KNOWN", "UNKNOWN", "ISNUMBER", "ISNOTNUMBER", "EXISTS", "ISNOTHING", "ISSOMETHING"].contains(&last.as_str()) {
            res.extend(self.expr_full(toks)?); // a yes/no value on its own: "if ready"
        }
        Ok(res)
    }

    fn one_cond(&mut self, toks: &[Tok]) -> Res<E> {
        let cands = self.conds(toks)?;
        if cands.is_empty() {
            return self.err(format!(
                "I couldn't understand the condition \"{}\". Try something like: if the score is more than 50.",
                show(toks)
            ));
        }
        if cands.len() > 1 {
            let readings: Vec<String> = cands.iter().map(|c| en(c)).collect();
            let mut e = EngError::new(ambiguity_text(&readings), self.line, &self.file);
            e.ambiguity = true;
            return Err(e);
        }
        Ok(cands[0].clone())
    }

    /// What a "when someone sends ..." rule waits for.
    fn message_cond(&mut self, toks: &[Tok]) -> Res<E> {
        let kinds: Vec<&str> = toks.iter().map(|t| t.kind.as_str()).collect();
        if kinds == ["ANYTHING"] || kinds == ["MSG"] {
            return Ok(ex(Ex::Truth));
        }
        if kinds == ["WORD"] && toks[0].value == "number" {
            return Ok(ex(Ex::IsNum));
        }
        let mut toks = toks;
        if !toks.is_empty() && (toks[0].kind == "ANYTHING" || toks[0].kind == "MSG") {
            toks = &toks[1..];
        }
        let tests = |op: &str, texts: Vec<E>| -> E {
            let t: Vec<E> = texts.into_iter().map(|x| cmp(op, ex(Ex::Msg), x)).collect();
            if t.len() == 1 {
                t[0].clone()
            } else {
                logic("or", t)
            }
        };
        if !toks.is_empty() && (toks[0].kind == "CONTAINS" || toks[0].kind == "STARTS") {
            let op = cmpop(&toks[0].kind).unwrap();
            let (texts, j) = self.item_list(toks, 1)?;
            if j == toks.len() && !texts.is_empty() && texts.iter().all(|x| matches!(x.k, Ex::Text(_))) {
                return Ok(tests(op, texts));
            }
        } else if !toks.is_empty() && toks[0].kind == "STR" {
            let (texts, j) = self.item_list(toks, 0)?;
            if j == toks.len() && texts.iter().all(|x| matches!(x.k, Ex::Text(_))) {
                return Ok(tests("~=", texts));
            }
        }
        self.err("I don't understand which messages this rule is for. For example: when someone sends \"/start\", when someone sends a number, when someone sends a message that contains \"price\", when someone sends anything else.")
    }

    // ── values and targets ──

    fn print_args(&mut self, toks: &[Tok], i: usize) -> Res<Vec<(Vec<E>, usize)>> {
        let mut res = Vec::new();
        for (e, j) in self.exprs(toks, i)? {
            res.push((vec![e.clone()], j));
            if j < toks.len() && toks[j].kind == "CONCAT" {
                for (more, k) in self.print_args(toks, j + 1)? {
                    let mut v = vec![e.clone()];
                    v.extend(more);
                    res.push((v, k));
                }
                // the last part may be a whole phrase: ... and then the title of b for every b in books
                for w in self.whole_values(&toks[j + 1..])? {
                    res.push((vec![e.clone(), w], toks.len()));
                }
            }
        }
        Ok(res)
    }

    /// Expressions that take a whole phrase: every X in L where ..., E for every X in L,
    /// X in L with the largest K.
    fn whole_values(&mut self, toks: &[Tok]) -> Res<Vec<E>> {
        let mut res = Vec::new();
        let n = toks.len();
        let wheres = top_positions(toks, &["WHERE"]);
        // every book in the books where the year of book is more than 1960
        if n > 5 && toks[0].is_word(&["every", "each"]) && toks[1].kind == "WORD" && toks[2].is_word(&["in"]) && !wheres.is_empty() {
            let w = wheres[0];
            let items = self.expr_full(sl(toks, 3, w))?;
            if !items.is_empty() {
                let var = self.define(&toks[1]);
                for it in &items {
                    for c in self.conds(from(toks, w + 1))? {
                        res.push(ex(Ex::Comprehension { var: var.clone(), items: it.clone(), expr: None, cond: Some(c) }));
                    }
                }
            }
        }
        // the title of book for every book in the books [where ...]
        let fe = top_positions(toks, &["FOREACH"]);
        if !fe.is_empty() && fe[0] > 0 && fe[0] + 3 < n && toks[fe[0] + 1].kind == "WORD" && toks[fe[0] + 2].is_word(&["in"]) {
            let p = fe[0];
            let end = wheres.iter().copied().find(|&w| w > p).unwrap_or(n);
            let items = self.expr_full(sl(toks, p + 3, end))?;
            if !items.is_empty() {
                let var = self.define(&toks[p + 1]);
                let conds: Vec<Option<E>> = if end < n { self.conds(from(toks, end + 1))?.into_iter().map(Some).collect() } else { vec![None] };
                for it in &items {
                    for e in self.expr_full(&toks[..p])? {
                        for c in &conds {
                            res.push(ex(Ex::Comprehension { var: var.clone(), items: it.clone(), expr: Some(e.clone()), cond: c.clone() }));
                        }
                    }
                }
            }
        }
        // the book in the books with the largest year
        if n > 5 && toks[0].kind == "WORD" && toks[1].is_word(&["in"]) {
            for p in 3..n - 2 {
                if toks[p].is_word(&["with"]) && toks[p + 1].kind == "WORD" {
                    if let Some(&(_, largest)) = BEST.iter().find(|(w, _)| *w == toks[p + 1].value) {
                        let items = self.expr_full(sl(toks, 2, p))?;
                        if !items.is_empty() {
                            let var = self.define(&toks[0]);
                            for it in &items {
                                for k in self.key_of(&var, &toks[p + 2..])? {
                                    res.push(ex(Ex::BestBy { var: var.clone(), items: it.clone(), key: k, largest }));
                                }
                            }
                        }
                    }
                }
            }
        }
        Ok(res)
    }

    /// A sort key: a field name ("by year") or an expression about the item ("by the length of each").
    fn key_of(&mut self, var: &E, toks: &[Tok]) -> Res<Vec<E>> {
        let fields: Vec<String> = self.kinds.match_field(toks, 0).into_iter().filter(|(_, j)| *j == toks.len()).map(|(k, _)| k).collect();
        if !fields.is_empty() {
            let display = toks.iter().map(|t| t.text.clone()).collect::<Vec<_>>().join(" ");
            return Ok(vec![field(var.clone(), &fields[0], &display)]);
        }
        self.expr_full(toks)
    }

    fn value_cands(&mut self, toks: &[Tok]) -> Res<Vec<E>> {
        let mut cands: Vec<E> = self
            .print_args(toks, 0)?
            .into_iter()
            .filter(|(_, j)| *j == toks.len())
            .map(|(parts, _)| if parts.len() == 1 { parts[0].clone() } else { ex(Ex::Concat(parts)) })
            .collect();
        cands.extend(self.whole_values(toks)?);
        let items = split_top(toks, &["COMMA", "AND"]);
        if items.len() > 1 && items.iter().all(|it| !it.is_empty()) {
            let mut parsed = Vec::new();
            for it in &items {
                parsed.push(ustr(self.expr_full(it)?));
            }
            if parsed.iter().all(|p| p.len() == 1) {
                cands.push(ex(Ex::ListLit(parsed.into_iter().map(|p| p[0].clone()).collect())));
            }
        }
        let last = toks[toks.len() - 1].kind.as_str();
        if !top_positions(toks, &CMPOPS).is_empty()
            || ["KNOWN", "UNKNOWN", "ISNUMBER", "ISNOTNUMBER", "EXISTS", "ISNOTHING", "ISSOMETHING"].contains(&last)
        {
            for c in self.conds(toks)? {
                if !matches!(c.k, Ex::Num(_) | Ex::Dec(_) | Ex::Text(_) | Ex::Var(_)) {
                    cands.push(c);
                }
            }
        }
        Ok(cands)
    }

    fn value(&mut self, toks: &[Tok]) -> Res<E> {
        if toks.is_empty() {
            return self.err("Something is missing after the name: what should it be?");
        }
        let c = self.value_cands(toks)?;
        self.choose_e(c, "the value", toks)
    }

    /// What can be set: a name, item N of a list, or an entry of a table.
    fn targets(&mut self, toks: &[Tok]) -> Res<Vec<Target>> {
        let mut res = Vec::new();
        if toks.len() == 1 && toks[0].kind == "WORD" && toks[0].value.contains('.') && !toks[0].value.starts_with(USER) {
            let (base, fld) = toks[0].value.split_once('.').unwrap();
            if self.scope_of(base).is_some() && self.kinds.fields.contains_key(fld) {
                return Ok(vec![Target::Field(field(self.make_var(base), fld, &fld.replace('_', " ")))]);
            }
        }
        if toks.len() == 1 && (toks[0].kind == "WORD" || toks[0].kind == "IT") {
            let mut t = toks[0].clone();
            if t.kind == "IT" {
                match self.last_var.clone() {
                    None => return self.err("I don't know what \"it\" means here. Nothing has been set yet."),
                    Some(v) => {
                        let display = self.make_var(&v).var().unwrap().display.clone();
                        t = Tok::word(&v, &display);
                    }
                }
            }
            res.push(Target::Var(t));
        }
        if !toks.is_empty()
            && toks[0].kind == "WORD"
            && toks.len() > 2
            && toks[1].is_word(&["of"])
            && !["item", "letter", "character", "first", "last", "file"].contains(&toks[0].value.as_str())
            && self.may_be_table(&toks[0].value)
        {
            for key in self.expr_full(&toks[2..])? {
                res.push(Target::Key(toks[0].clone(), key));
            }
        }
        if !toks.is_empty() && toks[0].kind == "WORD" && !self.kinds.match_field(toks, 0).is_empty() {
            for e in self.expr_full(toks)? {
                if matches!(e.k, Ex::Field { .. }) {
                    res.push(Target::Field(e));
                }
            }
        }
        if !toks.is_empty() && toks[0].is_word(&["item", "letter", "character", "first", "last"]) {
            for e in self.expr_full(toks)? {
                if matches!(e.k, Ex::Index { .. }) {
                    res.push(Target::Index(e));
                }
            }
        }
        Ok(res)
    }

    /// Turn a chosen target into statements; a new table is created on first use.
    fn make_target(&mut self, target: Target, value_stmt: impl FnOnce(&Self, E) -> S) -> S {
        match target {
            Target::Var(tok) => {
                let v = self.define(&tok);
                value_stmt(self, v)
            }
            Target::Index(e) | Target::Field(e) => value_stmt(self, e),
            Target::Key(tok, key) => {
                self.tables.insert(tok.value.clone());
                let table = if self.scope_of(&tok.value).is_none() {
                    // a new table: every name starts out empty, so the first entry just goes in
                    self.define(&tok)
                } else {
                    self.make_var(&tok.value)
                };
                value_stmt(self, lookup(table, key, false))
            }
        }
    }

    // ── statements ──

    fn inline(&mut self, toks: &[Tok]) -> Res<S> {
        match self.sentence(toks)? {
            Node::Stmt(s) => Ok(s),
            _ => self.err(format!("I couldn't understand this sentence: \"{}\".", show(toks))),
        }
    }

    /// Parse one sentence. Returns a statement, a block header, End or a marker.
    fn sentence(&mut self, toks: &[Tok]) -> Res<Node> {
        self.memo.clear();
        if toks.is_empty() {
            return Ok(Node::Empty);
        }
        let k = toks[0].kind.as_str();
        let n = toks.len();
        if k == "END" && n == 1 {
            return Ok(Node::End);
        }
        if k == "ON_MESSAGE" {
            let cut = (0..n).find(|&i| toks[i].kind == "COMMA" && !(i + 1 < n && toks[i + 1].kind == "STR"));
            let cond = self.message_cond(sl(toks, 1, cut.unwrap_or(n)))?;
            let body = match cut {
                None => vec![],
                Some(c) => vec![self.inline(strip_commas(&toks[c + 1..]))?],
            };
            return Ok(Node::Stmt(self.at(St::Handler { cond, body: RefCell::new(body) })));
        }
        if k == "OTHERWISE" {
            let rest = strip_commas(&toks[1..]);
            if rest.is_empty() {
                return Ok(Node::Else(None));
            }
            return Ok(Node::Else(Some(self.inline(rest)?)));
        }
        if k == "TRY" && n == 1 {
            return Ok(Node::Stmt(self.at(St::Try { body: body(vec![]), handler: RefCell::new(None) })));
        }
        if k == "TRY_TO" {
            let pos = toks.iter().position(|t| t.kind == "ON_ERROR");
            let mut b = sl(toks, 1, pos.unwrap_or(n));
            while !b.is_empty() && (b[b.len() - 1].kind == "COMMA" || b[b.len() - 1].kind == "AND") {
                b = &b[..b.len() - 1];
            }
            let handler = match pos {
                Some(p) => Some(vec![self.inline(strip_commas(&toks[p + 1..]))?]),
                None => None,
            };
            let first = self.inline(strip_commas(b))?;
            return Ok(Node::Stmt(self.at(St::Try { body: body(vec![first]), handler: RefCell::new(handler) })));
        }
        if k == "ON_ERROR" {
            let rest = strip_commas(&toks[1..]);
            if rest.is_empty() {
                return Ok(Node::OnError(None));
            }
            return Ok(Node::OnError(Some(self.inline(rest)?)));
        }
        if k == "FAIL" {
            let c: Vec<Vec<E>> = self.print_args(toks, 1)?.into_iter().filter(|(_, j)| *j == n).map(|(p, _)| p).collect();
            let parts = self.choose(c, "the error message", &toks[1..], |ps| join_str(ps), |ps| join_str(ps))?;
            return Ok(Node::Stmt(self.at(St::Fail(parts))));
        }
        if k == "IF" || k == "WHILE" {
            let cut = top_positions(toks, &["COMMA", "THEN"]).first().copied();
            let make = |p: &Self, cond: E, b: Vec<S>| -> S {
                if k == "IF" {
                    p.at(St::If { cond, then: body(b), else_: RefCell::new(None) })
                } else {
                    p.at(St::While { cond, body: body(b) })
                }
            };
            return Ok(Node::Stmt(match cut {
                None => {
                    let cond = self.one_cond(&toks[1..])?;
                    make(self, cond, vec![])
                }
                Some(c) => {
                    let cond = self.one_cond(sl(toks, 1, c))?;
                    let b = self.inline(strip_commas(&toks[c + 1..]))?;
                    make(self, cond, vec![b])
                }
            }));
        }
        if k == "FOREACH" {
            return Ok(Node::Stmt(self.for_each(toks)?));
        }
        if let Some(guard) = (1..n).find(|&i| toks[i].kind == "IF") {
            let b = self.inline(strip_commas(&toks[..guard]))?;
            let cond = self.one_cond(&toks[guard + 1..])?;
            return Ok(Node::Stmt(self.at(St::If { cond, then: body(vec![b]), else_: RefCell::new(None) })));
        }
        if k == "REPEAT" {
            return Ok(Node::Stmt(self.repeat(toks)?));
        }
        if k == "KEEP" {
            let stop = toks.iter().position(|t| t.kind == "UNTIL" || t.kind == "WHILE");
            let stop = match stop {
                None => return self.err("Say when to stop, for example: keep doubling x until it is more than 100."),
                Some(s) => s,
            };
            let b = self.simple(strip_commas(sl(toks, 1, stop)))?;
            let mut cond = self.one_cond(&toks[stop + 1..])?;
            if toks[stop].kind == "UNTIL" {
                cond = logic("not", vec![cond]);
            }
            return Ok(Node::Stmt(self.at(St::While { cond, body: body(vec![b]) })));
        }
        if n == 1 && (k == "BREAK" || k == "CONTINUE" || k == "EXIT") {
            let s = match k {
                "BREAK" => St::Break,
                "CONTINUE" => St::Continue,
                _ => St::Exit,
            };
            return Ok(Node::Stmt(self.at(s)));
        }
        if k == "RETURN" {
            let v = if n > 1 { Some(self.value(&toks[1..])?) } else { None };
            return Ok(Node::Stmt(self.at(St::Return(v))));
        }
        Ok(Node::Stmt(self.simple(toks)?))
    }

    fn repeat(&mut self, toks: &[Tok]) -> Res<S> {
        let stop = toks.iter().position(|t| t.kind == "UNTIL" || t.kind == "WHILE");
        if stop == Some(1) {
            let cut = top_positions(toks, &["COMMA"]).first().copied();
            let mut cond = self.one_cond(sl(toks, 2, cut.unwrap_or(toks.len())))?;
            if toks[1].kind == "UNTIL" {
                cond = logic("not", vec![cond]);
            }
            let rest = match cut {
                Some(c) => strip_commas(&toks[c + 1..]),
                None => &toks[0..0],
            };
            let b = if rest.is_empty() { vec![] } else { vec![self.inline(rest)?] };
            return Ok(self.at(St::While { cond, body: body(b) }));
        }
        let t = match toks.iter().position(|t| t.kind == "TIMES") {
            None => return self.err("Say how many times, for example: repeat 3 times, or: do the following until x is 10."),
            Some(t) => t,
        };
        let count = self.one_expr(sl(toks, 1, t), "how many times to repeat")?;
        let rest = strip_commas(&toks[t + 1..]);
        let b = if rest.is_empty() { vec![] } else { vec![self.inline(rest)?] };
        Ok(self.at(St::Repeat { count, body: body(b) }))
    }

    fn for_each(&mut self, toks: &[Tok]) -> Res<S> {
        let cut = top_positions(toks, &["COMMA"]).first().copied();
        let head = sl(toks, 0, cut.unwrap_or(toks.len()));
        let usage = "Write it like: for every guest in the guests, or: for every n from 1 to 10.";
        if head.len() < 4 || head[1].kind != "WORD" {
            return self.err(usage);
        }
        enum K {
            Each(E),
            Range(E, E, bool),
        }
        let kind = if head[2].is_word(&["in"]) {
            let mut c = self.expr_full(&head[3..])?;
            c.extend(self.whole_values(&head[3..])?);
            K::Each(self.choose_e(c, "what to go through", &head[3..])?)
        } else if head[2].kind == "FROM" {
            let to = top_positions(head, &["TO"]);
            if to.is_empty() {
                return self.err("Write it like: for every n from 1 to 10.");
            }
            let said_down = head[to[0] - 1].is_word(&["down"]);
            let start = self.one_expr(sl(head, 3, to[0] - said_down as usize), "where to start")?;
            let end = self.one_expr(&head[to[0] + 1..], "where to stop")?;
            let written = matches!((&start.k, &end.k), (Ex::Num(a), Ex::Num(b)) if a > b);
            K::Range(start, end, said_down || written)
        } else {
            return self.err(usage);
        };
        let var = self.define(&head[1]);
        let b = match cut {
            Some(c) => vec![self.inline(strip_commas(&toks[c + 1..]))?],
            None => vec![],
        };
        Ok(match kind {
            K::Each(items) => self.at(St::ForEach { var, items, body: body(b), kind: RefCell::new(String::new()) }),
            K::Range(start, end, down) => self.at(St::ForRange { var, start, end, body: body(b), down }),
        })
    }

    fn simple(&mut self, toks: &[Tok]) -> Res<S> {
        let k = toks[0].kind.clone();
        let k = k.as_str();
        let n = toks.len();
        match k {
            "PRINT" => return self.print_stmt(toks),
            "REPLY" => return self.reply_stmt(toks),
            "SET" => return Ok(self.set_stmt(&toks[1..], "TO")?.unwrap()),
            "ADD" => return self.add_stmt(toks),
            _ => {}
        }
        if k == "SUB" && n >= 4 {
            let mut cands = Vec::new();
            for pos in top_positions(toks, &["FROM"]) {
                for target in self.targets(&toks[pos + 1..])? {
                    for e in self.expr_full(sl(toks, 1, pos))? {
                        cands.push((target.clone(), e));
                    }
                }
            }
            let (target, e) = self.choose(
                cands,
                "what to subtract from what",
                toks,
                |c| format!("subtract {} from {}", en(&c.1), describe(&c.0)),
                |c| format!("{}|{}", describe(&c.0), c.1),
            )?;
            return Ok(self.numeric_update(target, "-", e));
        }
        if (k == "INC" || k == "DEC") && n >= 2 {
            let by = top_positions(toks, &["BY"]);
            let target_toks = match by.last() {
                Some(&b) => sl(toks, 1, b),
                None => &toks[1..],
            };
            let e = match by.last() {
                Some(&b) => self.one_expr(&toks[b + 1..], "the amount")?,
                None => num(1),
            };
            let ts = self.targets(target_toks)?;
            let target = self.choose(ts, "what to change", target_toks, describe, describe)?;
            return Ok(self.numeric_update(target, if k == "INC" { "+" } else { "-" }, e));
        }
        if (k == "DOUBLE" || k == "HALVE") && n >= 2 {
            let ts = self.targets(&toks[1..])?;
            let target = self.choose(ts, "what to change", &toks[1..], describe, describe)?;
            return Ok(self.numeric_update(target, if k == "DOUBLE" { "*" } else { "/" }, num(2)));
        }
        if k == "REMOVE" {
            return self.remove_stmt(toks);
        }
        if k == "REVERSE" {
            let e = self.one_expr(&toks[1..], "the list")?;
            return Ok(self.at(St::Reverse(e)));
        }
        if k == "SORT" {
            let mut rest = &toks[1..];
            let mut descending = false;
            if !rest.is_empty() && (rest[rest.len() - 1].kind == "DESCENDING" || rest[rest.len() - 1].kind == "ASCENDING") {
                descending = rest[rest.len() - 1].kind == "DESCENDING";
                rest = &rest[..rest.len() - 1];
            }
            let by = top_positions(rest, &["BY"]);
            if by.is_empty() {
                let items = self.one_expr(rest, "the list")?;
                return Ok(self.at(St::Sort { items, var: None, key: None, descending }));
            }
            let items = self.one_expr(&rest[..by[0]], "the list")?;
            let each = self.define(&Tok::word("each", "each"));
            let keys = self.key_of(&each, &rest[by[0] + 1..])?;
            let key = self.choose_e(keys, "what to sort by", &rest[by[0] + 1..])?;
            return Ok(self.at(St::Sort { items, var: Some(each), key: Some(key), descending }));
        }
        if k == "ASK" {
            let pos = top_positions(toks, &["ANSWER_AS"]);
            if pos.is_empty() || *pos.last().unwrap() != n - 2 || toks[n - 1].kind != "WORD" {
                return self.err("Write it like: ask \"How old are you?\" and remember the answer as age.");
            }
            let p = *pos.last().unwrap();
            let c: Vec<E> = self
                .print_args(toks, 1)?
                .into_iter()
                .filter(|(_, j)| *j == p)
                .map(|(ps, _)| if ps.len() == 1 { ps[0].clone() } else { ex(Ex::Concat(ps)) })
                .collect();
            let prompt = self.choose_e(c, "the question", sl(toks, 1, p))?;
            let target = self.define(&toks[n - 1]);
            return Ok(self.at(St::Ask { prompt, target }));
        }
        if k == "FETCH" {
            if n < 4 || !toks[n - 2].is_word(&["into", "as"]) || toks[n - 1].kind != "WORD" {
                return self.err("Write it like: fetch \"https://example.com/data.json\" into page.");
            }
            let url = self.one_expr(&toks[1..n - 2], "the address")?;
            let target = self.define(&toks[n - 1]);
            return Ok(self.at(St::Assign { target, expr: builtin("fetch", vec![url]) }));
        }
        if k == "SEND" {
            let ask = top_positions(toks, &["ANSWER_AS"]);
            let end = ask.last().copied().unwrap_or(n);
            let to = top_positions(&toks[..end], &["TO"]);
            if to.is_empty() {
                return self.err("Write it like: send the order to \"https://example.com/orders\" and remember the reply as r.");
            }
            let t = *to.last().unwrap();
            let b = self.one_expr(sl(toks, 1, t), "what to send")?;
            let url = self.one_expr(sl(toks, t + 1, end), "the address")?;
            let post = builtin("post", vec![b, url]);
            if !ask.is_empty() {
                if *ask.last().unwrap() != n - 2 || toks[n - 1].kind != "WORD" {
                    return self.err("Write it like: send data to \"https://...\" and remember the reply as reply.");
                }
                let target = self.define(&toks[n - 1]);
                return Ok(self.at(St::Assign { target, expr: post }));
            }
            return Ok(self.at(St::Eval(post)));
        }
        if k == "WAIT" {
            if n >= 3 && toks[n - 1].is_word(&["second", "seconds", "millisecond", "milliseconds"]) {
                let ms = if toks[n - 1].value.starts_with("milli") { 1 } else { 1000 };
                let amount = self.one_expr(&toks[1..n - 1], "how long to wait")?;
                return Ok(self.at(St::Eval(builtin("wait", vec![amount, num(ms)]))));
            }
            return self.err("Write it like: wait 2 seconds, or: wait 300 milliseconds.");
        }
        if k == "RUN" {
            let ask = top_positions(toks, &["ANSWER_AS"]);
            if let Some(&a) = ask.last() {
                if a != n - 2 || toks[n - 1].kind != "WORD" {
                    return self.err("Write it like: run \"ls -l\" and remember the output as listing.");
                }
                let cmd = self.one_expr(sl(toks, 1, a), "the command")?;
                let target = self.define(&toks[n - 1]);
                return Ok(self.at(St::Assign { target, expr: builtin("run_output", vec![cmd]) }));
            }
            let cmd = self.one_expr(&toks[1..], "the command")?;
            return Ok(self.at(St::Eval(builtin("run", vec![cmd]))));
        }
        if k == "READ" {
            if n < 4 || !toks[n - 2].is_word(&["into", "as"]) || toks[n - 1].kind != "WORD" {
                return self.err("Write it like: read the file \"notes.txt\" into notes.");
            }
            let e = self.one_expr(&toks[1..n - 2], "what to read")?;
            let target = self.define(&toks[n - 1]);
            return Ok(self.at(St::Assign { target, expr: e }));
        }
        if k == "PERSIST" {
            let e = self.one_expr(&toks[1..], "the file name")?;
            return Ok(self.at(St::Persist(e)));
        }
        if k == "USE" {
            return self.err("\"use\" only works at the start of a line, outside of any group.");
        }
        if (k == "WORD" || k == "IT") && n > 2 {
            if let Some(s) = self.try_is(toks)? {
                return Ok(s);
            }
        }
        let mut calls = Vec::new();
        if k == "WORD" {
            for sig in self.funcs.commands(&toks[0]) {
                for (args, j) in self.matches(&sig.pattern, toks, 0, vec![], false)? {
                    if j == n {
                        calls.push(self.at(St::CallStmt(call(sig.clone(), args))));
                    }
                }
            }
        }
        if !calls.is_empty() {
            return self.choose(calls, "this command", toks, english_stmt, stmt_key);
        }
        if k == "WORD" && !self.funcs.commands(&toks[0]).is_empty() {
            let forms = self.funcs.commands(&toks[0]).iter().map(|s| format!("\"{}\"", s.display)).collect::<Vec<_>>().join("; ");
            return self.err(format!(
                "This doesn't match the command {}. Words in a command's first line are typed exactly as written; a value you pass in is introduced there with \"a\" or \"some\".",
                forms
            ));
        }
        if k == "WORD" {
            let mut pool = self.lx.single_forms.clone();
            let mut fw: Vec<String> = self.funcs.first_words().into_iter().collect();
            fw.sort();
            pool.extend(fw);
            let hint = match close_match(&toks[0].value, &pool, 0.7) {
                Some(c) => format!(" Did you mean \"{}\"?", c),
                None => String::new(),
            };
            return self.err(format!("I don't know the word \"{}\".{}", toks[0].text, hint));
        }
        self.err(format!("I couldn't understand this sentence: \"{}\".", show(toks)))
    }

    fn set_stmt(&mut self, toks: &[Tok], sep: &str) -> Res<Option<S>> {
        let mut cands = Vec::new();
        for pos in top_positions(toks, &[sep]) {
            for target in self.targets(&toks[..pos])? {
                if pos + 1 < toks.len() {
                    let vals = self.value_cands(&toks[pos + 1..])?;
                    for v in ustr(vals) {
                        cands.push((target.clone(), v));
                    }
                }
            }
        }
        if cands.is_empty() {
            if sep == "TO" {
                return self.err("Write it like: set age to 18.");
            }
            return Ok(None);
        }
        let (target, v) = self.choose(
            cands,
            "this",
            toks,
            |c| format!("set {} to {}", describe(&c.0), en(&c.1)),
            |c| format!("{}|{}", describe(&c.0), c.1),
        )?;
        if let Target::Var(tok) = &target {
            if matches!(v.k, Ex::EmptyMap) {
                self.tables.insert(tok.value.clone());
            }
        }
        Ok(Some(self.make_target(target, |p, t| p.at(St::Assign { target: t, expr: v }))))
    }

    fn try_is(&mut self, toks: &[Tok]) -> Res<Option<S>> {
        if top_positions(toks, &["IS"]).is_empty() {
            return Ok(None);
        }
        self.set_stmt(toks, "IS")
    }

    /// increase / decrease / double / halve: names and table entries start at zero.
    fn numeric_update(&mut self, target: Target, op: &str, e: E) -> S {
        if let Target::Var(tok) = &target {
            let var = self.define(tok);
            let name = var.var().unwrap().name.clone();
            let current = self.make_var(&name);
            return self.at(St::Assign { target: var, expr: bin(op, current, e) });
        }
        let op = op.to_string();
        self.make_target(target, move |p, t| {
            let current = match &t.k {
                Ex::Lookup { table, key, .. } => lookup(table.clone(), key.clone(), true),
                _ => t.clone(),
            };
            p.at(St::Assign { target: t, expr: bin(&op, current, e) })
        })
    }

    fn add_stmt(&mut self, toks: &[Tok]) -> Res<S> {
        enum C {
            File(E, E),
            Add(Target, E),
        }
        let mut cands = Vec::new();
        for pos in top_positions(toks, &["TO"]) {
            let what = sl(toks, 1, pos);
            let mut values = self.expr_full(what)?;
            if what.iter().any(|t| t.kind == "CONCAT") {
                for (parts, m) in self.print_args(what, 0)? {
                    if m == what.len() && parts.len() > 1 {
                        values.push(ex(Ex::Concat(parts)));
                    }
                }
            }
            let rest = &toks[pos + 1..];
            if !rest.is_empty() && rest[0].is_word(&["file"]) {
                for p in self.expr_full(&rest[1..])? {
                    for v in &values {
                        cands.push(C::File(p.clone(), v.clone()));
                    }
                }
            }
            for target in self.targets(rest)? {
                for v in &values {
                    cands.push(C::Add(target.clone(), v.clone()));
                }
            }
        }
        if cands.is_empty() {
            return self.err("Write it like: add 5 to the score, or: add \"Eve\" to the guests.");
        }
        let c = self.choose(
            cands,
            "what to add to what",
            toks,
            |c| match c {
                C::Add(t, v) => format!("add {} to {}", en(v), describe(t)),
                C::File(p, v) => format!("add {} to the file {}", en(v), en(p)),
            },
            |c| match c {
                C::Add(t, v) => format!("add|{}|{}", describe(t), v),
                C::File(p, v) => format!("file|{}|{}", p, v),
            },
        )?;
        match c {
            C::File(p, v) => Ok(self.at(St::WriteFile { path: p, parts: vec![v], append: true })),
            C::Add(Target::Var(tok), v) => {
                let create = self.scope_of(&tok.value).is_none();
                let target = self.define(&tok);
                Ok(self.at(St::AddTo { target, expr: v, create, kind: RefCell::new(String::new()) }))
            }
            C::Add(t, v) => {
                Ok(self.make_target(t, |p, tg| p.at(St::AddTo { target: tg, expr: v, create: false, kind: RefCell::new(String::new()) })))
            }
        }
    }

    fn remove_stmt(&mut self, toks: &[Tok]) -> Res<S> {
        let rest = &toks[1..];
        if !rest.is_empty() && rest[0].is_word(&["file"]) {
            let p = self.one_expr(&rest[1..], "the file name")?;
            return Ok(self.at(St::DeleteFile(p)));
        }
        let mut cands = Vec::new();
        for pos in top_positions(rest, &["FROM"]) {
            let (what, src) = (&rest[..pos], &rest[pos + 1..]);
            for items in self.expr_full(src)? {
                if !what.is_empty() && what[0].is_word(&INDEX_WORDS) {
                    for i in self.expr_full(&what[1..])? {
                        cands.push(self.at(St::RemoveAt { items: items.clone(), index: i }));
                    }
                }
                for v in self.expr_full(what)? {
                    cands.push(self.at(St::RemoveValue { items: items.clone(), value: v }));
                }
            }
        }
        for e in self.expr_full(rest)? {
            if let Ex::Lookup { table, key, .. } = &e.k {
                cands.push(self.at(St::RemoveKey { table: table.clone(), key: key.clone() }));
            }
        }
        self.choose(cands, "what to remove", toks, stmt_key, stmt_key)
    }

    fn print_stmt(&mut self, toks: &[Tok]) -> Res<S> {
        let n = toks.len();
        let to_file: Vec<usize> = top_positions(toks, &["TO"]).into_iter().filter(|&p| p + 1 < n && toks[p + 1].is_word(&["file"])).collect();
        if let Some(&p) = to_file.last() {
            let c: Vec<Vec<E>> = self.print_args(toks, 1)?.into_iter().filter(|(_, j)| *j == p).map(|(ps, _)| ps).collect();
            let parts = self.choose(c, "what to write", sl(toks, 1, p), |ps| join_str(ps), |ps| join_str(ps))?;
            let path = self.one_expr(&toks[p + 2..], "the file name")?;
            return Ok(self.at(St::WriteFile { path, parts, append: false }));
        }
        let mut cands = Vec::new();
        for (parts, j) in self.print_args(toks, 1)? {
            if j == n {
                cands.push(self.at(St::Print(parts)));
            } else {
                for (cnt, k) in self.exprs(toks, j)? {
                    if k == n - 1 && toks[k].kind == "TIMES" {
                        let p = self.at(St::Print(parts.clone()));
                        cands.push(self.at(St::Repeat { count: cnt, body: body(vec![p]) }));
                    }
                }
            }
        }
        for w in self.whole_values(&toks[1..])? {
            cands.push(self.at(St::Print(vec![w])));
        }
        if cands.is_empty() {
            return self.err(format!(
                "I couldn't understand what to {}: \"{}\". Put text with several words in quotes.",
                toks[0].value,
                show(toks)
            ));
        }
        self.choose(cands, "this", toks, english_stmt, stmt_key)
    }

    fn reply_stmt(&mut self, toks: &[Tok]) -> Res<S> {
        let b = toks.iter().position(|t| t.kind == "BUTTONS").unwrap_or(toks.len());
        let mut buttons = Vec::new();
        if b < toks.len() {
            let (bs, j) = self.item_list(toks, b + 1)?;
            if bs.is_empty() || j != toks.len() {
                return self.err("Write buttons like this: reply \"Pick one\" with buttons \"yes\", \"no\" and \"maybe\".");
            }
            buttons = bs;
        }
        let head = &toks[..b];
        let mut c: Vec<Vec<E>> = self.print_args(head, 1)?.into_iter().filter(|(_, j)| *j == b).map(|(ps, _)| ps).collect();
        for w in self.whole_values(sl(toks, 1, b))? {
            c.push(vec![w]);
        }
        let parts = self.choose(
            c,
            "what to reply",
            head,
            |ps| format!("reply {}", ps.iter().map(|p| en(p)).collect::<Vec<_>>().join(" and then ")),
            |ps| join_str(ps),
        )?;
        Ok(self.at(St::Reply { parts, buttons }))
    }
}

fn join_str(ps: &[E]) -> String {
    ps.iter().map(|p| p.to_string()).collect::<Vec<_>>().join(", ")
}

// ───────────── blocks and files ─────────────

/// Where a statement goes while a program is being put together.
#[derive(Clone)]
enum Place {
    Root,
    Func(Sig),
    Then(S),
    Else(S),
    Body(S),
    Handler(S),
}

impl Place {
    fn with<T>(&self, root: &RefCell<Vec<S>>, f: impl FnOnce(&mut Vec<S>) -> T) -> T {
        match self {
            Place::Root => f(&mut root.borrow_mut()),
            Place::Func(sig) => f(&mut sig.body.borrow_mut()),
            Place::Then(s) => match &s.k {
                St::If { then, .. } => f(&mut then.borrow_mut()),
                _ => unreachable!(),
            },
            Place::Else(s) => match &s.k {
                St::If { else_, .. } => f(else_.borrow_mut().as_mut().unwrap()),
                _ => unreachable!(),
            },
            Place::Body(s) => match &s.k {
                St::Repeat { body, .. }
                | St::While { body, .. }
                | St::ForEach { body, .. }
                | St::ForRange { body, .. }
                | St::Handler { body, .. }
                | St::Try { body, .. } => f(&mut body.borrow_mut()),
                _ => unreachable!(),
            },
            Place::Handler(s) => match &s.k {
                St::Try { handler, .. } => f(handler.borrow_mut().as_mut().unwrap()),
                _ => unreachable!(),
            },
        }
    }
}

struct Frame {
    kind: &'static str,
    place: Place,
    single_line: bool,
}

/// The If that a following "otherwise" belongs to, following else-if chains.
fn open_if(list: &[S]) -> Option<S> {
    let mut node = list.last()?.clone();
    loop {
        let next = match &node.k {
            St::If { else_, .. } => match else_.borrow().as_ref() {
                Some(e) if e.len() == 1 && matches!(e[0].k, St::If { .. }) => Some(e[0].clone()),
                _ => None,
            },
            _ => None,
        };
        match next {
            Some(n) => node = n,
            None => break,
        }
    }
    match &node.k {
        St::If { else_, .. } if else_.borrow().is_none() => Some(node.clone()),
        _ => None,
    }
}

/// A statement whose body follows on the next lines.
fn is_header(node: &S) -> bool {
    match &node.k {
        St::If { then, .. } => then.borrow().is_empty(),
        St::Repeat { body, .. }
        | St::While { body, .. }
        | St::ForEach { body, .. }
        | St::ForRange { body, .. }
        | St::Handler { body, .. }
        | St::Try { body, .. } => body.borrow().is_empty(),
        _ => false,
    }
}

fn header_frame(node: &S, single_line: bool) -> Frame {
    match &node.k {
        St::If { .. } => Frame { kind: "then", place: Place::Then(node.clone()), single_line },
        St::Try { .. } => Frame { kind: "try", place: Place::Body(node.clone()), single_line },
        _ => Frame { kind: "block", place: Place::Body(node.clone()), single_line },
    }
}

fn starts_with_word(text: &str, words: &[&str]) -> bool {
    text.split_whitespace().next().is_some_and(|w| words.contains(&w.to_lowercase().as_str()))
}

fn dirname(path: &str) -> String {
    match path.rfind('/') {
        Some(0) => "/".into(),
        Some(i) => path[..i].to_string(),
        None => String::new(),
    }
}

fn join_path(dir: &str, name: &str) -> String {
    if name.starts_with('/') || dir.is_empty() {
        name.to_string()
    } else if dir.ends_with('/') {
        format!("{}{}", dir, name)
    } else {
        format!("{}/{}", dir, name)
    }
}

/// os.path.abspath: absolute, with "." and ".." folded away (symbolic links are not followed).
pub fn abspath(path: &str) -> String {
    let full = if path.starts_with('/') {
        path.to_string()
    } else {
        let cwd = std::env::current_dir().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
        join_path(&cwd, path)
    };
    let mut parts: Vec<&str> = Vec::new();
    for p in full.split('/') {
        match p {
            "" | "." => {}
            ".." => {
                parts.pop();
            }
            p => parts.push(p),
        }
    }
    format!("/{}", parts.join("/"))
}

/// All sentences of a file, with "use" lines replaced by the used file's sentences.
fn load_sentences(src: &str, file: &str, seen: &mut HashSet<String>, lx: &Lexicon) -> Res<Vec<Sentence>> {
    let mut out = Vec::new();
    for s in split_sentences(src, file)? {
        if !starts_with_word(&s.text, &["use"]) {
            out.push(s);
            continue;
        }
        let toks = canonical(&s.text, s.line, &s.file, lx)?;
        if toks.len() != 2 || toks[0].kind != "USE" || toks[1].kind != "STR" {
            return Err(EngError::new("Write it like: use \"helpers.eng\".", s.line, &s.file));
        }
        let path = if file.is_empty() { toks[1].value.clone() } else { join_path(&dirname(file), &toks[1].value) };
        let key = abspath(&path);
        if seen.contains(&key) {
            continue;
        }
        seen.insert(key);
        let used = std::fs::read_to_string(&path)
            .map_err(|_| EngError::new(format!("I can't find the file \"{}\".", toks[1].value), s.line, &s.file))?;
        out.extend(load_sentences(&used, &path, seen, lx)?);
    }
    Ok(out)
}

pub fn parse_program(src: &str, file: &str, lx: &mut Lexicon) -> Res<(Vec<S>, Funcs)> {
    let mut seen = HashSet::new();
    if !file.is_empty() {
        seen.insert(abspath(file));
    }
    let sentences = load_sentences(src, file, &mut seen, lx)?;
    let mut kinds = Kinds::default();
    for s in &sentences {
        if is_kind_decl(&s.text) {
            let k = parse_kind_decl(s, lx, &kinds)?;
            kinds.add(k);
        }
    }
    if !kinds.by_name.contains_key("moment") {
        // what "the current time" gives
        let fields: Vec<String> = MOMENT_FIELDS.iter().map(|f| f.to_string()).collect();
        kinds.add(KindInfo {
            name: "moment".into(),
            display: "moment".into(),
            field_display: fields.iter().map(|f| (f.clone(), f.clone())).collect(),
            fields,
            id: Cell::new(0),
            line: 0,
            file: String::new(),
            raw_specs: vec![],
            field_specs: RefCell::new(IndexMap::new()),
        });
    }
    resolve_field_specs(&kinds)?;
    lx.protected = kinds.fields.values().flatten().cloned().collect();
    let lx: &Lexicon = lx;
    let mut funcs = Funcs::default();
    for s in &sentences {
        if starts_with_word(&s.text, &["to"]) {
            let sig = parse_header(s, lx, funcs.sigs.len(), &kinds)?;
            funcs.add(sig);
        }
    }
    let root: RefCell<Vec<S>> = RefCell::new(Vec::new());
    {
        let mut p = Parser {
            lx,
            funcs: &funcs,
            kinds: &kinds,
            tables: HashSet::new(),
            globals: IndexMap::new(),
            user_vars: IndexMap::new(),
            func: None,
            locals: IndexMap::new(),
            visible: HashSet::new(),
            last_var: None,
            saved_last: None,
            line: 0,
            file: String::new(),
            memo: HashMap::new(),
        };
        let mut stack: Vec<Frame> = Vec::new();

        fn current(stack: &[Frame]) -> Place {
            stack.last().map(|f| f.place.clone()).unwrap_or(Place::Root)
        }

        for item in &sentences {
            p.line = item.line;
            p.file = item.file.clone();
            if is_kind_decl(&item.text) {
                stack.clear();
                p.exit_function();
                continue;
            }
            if starts_with_word(&item.text, &["to"]) {
                stack.clear();
                p.exit_function();
                let sig = funcs.by_header[&(item.file.clone(), item.line)].clone();
                root.borrow_mut().push(Rc::new(Stmt { line: 0, file: String::new(), k: St::FuncDef(sig.clone()) }));
                p.enter_function(&sig);
                let (_, inline) = header_split(&item.text, item.line, &item.file)?;
                match inline {
                    Some(b) if !b.is_empty() => {
                        p.memo.clear();
                        let toks = apply_lexicon(&normalize(&b, lx), lx);
                        let node = p.inline(&toks)?;
                        sig.body.borrow_mut().push(node.clone());
                        if is_header(&node) {
                            stack.push(Frame { kind: "function", place: Place::Func(sig.clone()), single_line: false });
                            stack.push(header_frame(&node, item.header_colon));
                        } else {
                            p.exit_function();
                        }
                    }
                    _ => stack.push(Frame { kind: "function", place: Place::Func(sig.clone()), single_line: item.header_colon }),
                }
                continue;
            }
            let toks = canonical(&item.text, item.line, &item.file, lx)?;
            if !toks.is_empty() && toks[0].kind == "ON_MESSAGE" {
                stack.clear();
                p.exit_function();
            }
            let node = p.sentence(&toks)?;
            let pop = |stack: &mut Vec<Frame>, p: &mut Parser| {
                if let Some(f) = stack.pop() {
                    if f.kind == "function" {
                        p.exit_function();
                    }
                }
            };
            match node {
                Node::Empty => continue,
                Node::End => {
                    if stack.is_empty() {
                        return Err(EngError::new("\"That's all\" closes a group, but no group is open here.", item.line, &item.file));
                    }
                    pop(&mut stack, &mut p);
                }
                Node::Else(stmt) => {
                    let mut prev = current(&stack).with(&root, |l| open_if(l));
                    if prev.is_none() && stack.last().is_some_and(|f| f.kind == "then") {
                        pop(&mut stack, &mut p); // "otherwise" ends the block of a multi-line "if"
                        prev = current(&stack).with(&root, |l| open_if(l));
                    }
                    let prev = match prev {
                        None => return Err(EngError::new("\"Otherwise\" needs an \"if\" sentence right before it.", item.line, &item.file)),
                        Some(p) => p,
                    };
                    if let St::If { else_, .. } = &prev.k {
                        *else_.borrow_mut() = Some(vec![]);
                    }
                    match stmt {
                        None => stack.push(Frame { kind: "else", place: Place::Else(prev.clone()), single_line: item.header_colon }),
                        Some(s) => {
                            Place::Else(prev.clone()).with(&root, |l| l.push(s.clone()));
                            if is_header(&s) {
                                // "otherwise, if X" with a block below
                                stack.push(header_frame(&s, item.header_colon));
                            }
                        }
                    }
                }
                Node::OnError(stmt) => {
                    if stack.last().is_some_and(|f| f.kind == "try") {
                        pop(&mut stack, &mut p); // "if something goes wrong" ends the block of "try the following"
                    }
                    let prev = current(&stack).with(&root, |l| l.last().cloned());
                    let ok = matches!(&prev, Some(s) if matches!(&s.k, St::Try { handler, .. } if handler.borrow().is_none()));
                    if !ok {
                        return Err(EngError::new(
                            "\"If something goes wrong\" needs \"try the following\" right before it.",
                            item.line,
                            &item.file,
                        ));
                    }
                    let prev = prev.unwrap();
                    if let St::Try { handler, .. } = &prev.k {
                        *handler.borrow_mut() = Some(vec![]);
                    }
                    match stmt {
                        None => stack.push(Frame { kind: "handler", place: Place::Handler(prev.clone()), single_line: item.header_colon }),
                        Some(s) => {
                            Place::Handler(prev.clone()).with(&root, |l| l.push(s.clone()));
                            if is_header(&s) {
                                stack.push(header_frame(&s, item.header_colon));
                            }
                        }
                    }
                }
                Node::Stmt(s) => {
                    if matches!(s.k, St::Persist(_)) && !stack.is_empty() {
                        return Err(EngError::new(
                            "\"Remember everything in\" must stand on its own, outside of any group.",
                            item.line,
                            &item.file,
                        ));
                    }
                    current(&stack).with(&root, |l| l.push(s.clone()));
                    // a same-line body ("repeat 5 times: say hi") holds one sentence
                    while stack.last().is_some_and(|f| f.single_line && f.place.with(&root, |l| !l.is_empty())) {
                        pop(&mut stack, &mut p);
                    }
                    if is_header(&s) {
                        stack.push(header_frame(&s, item.header_colon));
                    }
                }
            }
        }
    }
    funcs.kinds = kinds;
    Ok((root.into_inner(), funcs))
}
