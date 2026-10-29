//! From source text to canonical tokens: sentences, tokens, normalization, synonyms.
//! Mirrors plainc/syntax.py.

use std::collections::{HashMap, HashSet};

use indexmap::IndexMap;

use crate::pyfmt::float_repr;

pub const LEXICON_JSON: &str = include_str!("../../lexicon.json");

#[derive(Clone, Debug)]
pub struct EngError {
    pub message: String,
    pub line: u32,
    pub file: String,
    pub ambiguity: bool,
}

impl EngError {
    pub fn new(message: impl Into<String>, line: u32, file: &str) -> EngError {
        EngError { message: message.into(), line, file: file.to_string(), ambiguity: false }
    }

    pub fn report(&self, default_file: &str) -> String {
        let file = crate::pyfmt::basename(if self.file.is_empty() { default_file } else { &self.file });
        if self.line > 0 {
            format!("{}, line {}:\n{}", file, self.line, self.message)
        } else if !file.is_empty() {
            format!("{}:\n{}", file, self.message)
        } else {
            self.message.clone()
        }
    }
}

pub type Res<T> = Result<T, EngError>;

pub struct Lexicon {
    pub articles: HashSet<String>,
    pub filler: HashSet<String>,
    pub label_words: HashSet<String>,
    pub contractions: HashMap<String, String>,
    pub numbers: HashMap<String, i64>,
    pub repeat_words: HashMap<String, i64>,
    pub symbols: HashMap<String, String>,
    pub phrases: HashMap<Vec<String>, String>,
    pub max_phrase: usize,
    pub phrase_starts: HashSet<(String, String)>,
    pub print_forms: HashSet<String>,
    pub single_forms: Vec<String>,
    pub protected: HashSet<String>,
}

impl Lexicon {
    pub fn load() -> Lexicon {
        let data: serde_json::Value = serde_json::from_str(LEXICON_JSON).expect("lexicon.json");
        let strings = |k: &str| -> HashSet<String> {
            data[k].as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect()
        };
        let ints = |k: &str| -> HashMap<String, i64> {
            data[k].as_object().unwrap().iter().map(|(a, b)| (a.clone(), b.as_i64().unwrap())).collect()
        };
        let texts = |k: &str| -> HashMap<String, String> {
            data[k].as_object().unwrap().iter().map(|(a, b)| (a.clone(), b.as_str().unwrap().to_string())).collect()
        };
        let mut phrases: IndexMap<Vec<String>, String> = IndexMap::new();
        for (kw, forms) in data["words"].as_object().unwrap() {
            for form in forms.as_array().unwrap() {
                let key: Vec<String> = form.as_str().unwrap().split_whitespace().map(String::from).collect();
                phrases.insert(key, kw.clone());
            }
        }
        let max_phrase = phrases.keys().map(|p| p.len()).max().unwrap_or(1);
        let phrase_starts = phrases.keys().filter(|p| p.len() > 1).map(|p| (p[0].clone(), p[1].clone())).collect();
        let print_forms = phrases
            .iter()
            .filter(|(p, kw)| (kw.as_str() == "PRINT" || kw.as_str() == "REPLY") && p.len() == 1)
            .map(|(p, _)| p[0].clone())
            .collect();
        let mut single_forms: Vec<String> = phrases.keys().filter(|p| p.len() == 1).map(|p| p[0].clone()).collect();
        single_forms.sort();
        Lexicon {
            articles: strings("articles"),
            filler: strings("filler"),
            label_words: strings("label_words"),
            contractions: texts("contractions"),
            numbers: ints("numbers"),
            repeat_words: ints("repeat_words"),
            symbols: texts("symbols"),
            phrases: phrases.into_iter().collect(),
            max_phrase,
            phrase_starts,
            print_forms,
            single_forms,
            protected: HashSet::new(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct Tok {
    pub kind: String, // WORD NUM DEC STR COMMA LP RP SYM or a canonical keyword (PRINT, IS, ...)
    pub value: String, // lowercase word / string / keyword source text; digits for NUM
    pub num: i64,
    pub dec: f64,
    pub text: String, // original spelling
    pub sym: bool,    // operator written as a symbol (+ * >) rather than a word
}

impl Tok {
    pub fn new(kind: &str, value: &str, text: &str) -> Tok {
        Tok { kind: kind.into(), value: value.into(), num: 0, dec: 0.0, text: text.into(), sym: false }
    }
    pub fn word(value: &str, text: &str) -> Tok {
        Tok::new("WORD", value, text)
    }
    pub fn int(n: i64, text: &str) -> Tok {
        Tok { kind: "NUM".into(), value: n.to_string(), num: n, dec: 0.0, text: text.into(), sym: false }
    }
    pub fn is_word(&self, values: &[&str]) -> bool {
        self.kind == "WORD" && values.contains(&self.value.as_str())
    }
}

#[derive(Clone, Debug)]
pub struct Sentence {
    pub text: String,
    pub line: u32,
    pub file: String,
    pub header_colon: bool,
}

fn normalize_quotes(src: &str) -> String {
    src.replace(['\u{201c}', '\u{201d}'], "\"").replace(['\u{2018}', '\u{2019}'], "'")
}

fn is_apostrophe(s: &[char], i: usize) -> bool {
    i > 0 && i + 1 < s.len() && s[i - 1].is_alphanumeric() && s[i + 1].is_alphanumeric()
}

fn is_comment(line: &str) -> bool {
    let s = line.trim().to_lowercase();
    s.starts_with("note:") || s.starts_with('#')
}

const CONTINUE_WORDS: [&str; 6] = ["and", "or", "with", "of", "to", "by"];

fn continues(text: &str) -> bool {
    let t = text.trim_end();
    if t.is_empty() || t.ends_with(['.', ':', '"', '\'']) {
        return false;
    }
    if t.ends_with(',') {
        return true;
    }
    let lower = t.to_lowercase();
    let words: Vec<&str> = lower.split_whitespace().collect();
    let n = words.len();
    CONTINUE_WORDS.contains(&words[n - 1])
        || (n > 1 && ((words[n - 2] == "and" && words[n - 1] == "then") || (words[n - 2] == "followed" && words[n - 1] == "by")))
}

fn logical_lines(src: &str) -> Vec<(u32, String)> {
    let mut out: Vec<(u32, String)> = Vec::new();
    for (k, raw) in normalize_quotes(src).split('\n').enumerate() {
        if raw.trim().is_empty() || is_comment(raw) {
            continue;
        }
        let start = raw.trim().to_lowercase();
        if let Some(last) = out.last_mut() {
            if continues(&last.1) || start.starts_with("and then ") || start.starts_with("followed by ") {
                last.1 = format!("{} {}", last.1.trim_end(), raw.trim());
                continue;
            }
        }
        out.push((k as u32 + 1, raw.to_string()));
    }
    out
}

pub fn split_sentences(src: &str, file: &str) -> Res<Vec<Sentence>> {
    let mut out = Vec::new();
    for (lineno, raw) in logical_lines(src) {
        let raw: Vec<char> = raw.chars().collect();
        let mut buf = String::new();
        let mut quote: Option<char> = None;
        let mut i = 0;
        let push = |out: &mut Vec<Sentence>, buf: &str, colon: bool| {
            if !buf.trim().is_empty() {
                out.push(Sentence { text: buf.trim().to_string(), line: lineno, file: file.to_string(), header_colon: colon });
            }
        };
        while i < raw.len() {
            let ch = raw[i];
            if let Some(q) = quote {
                buf.push(ch);
                if ch == '\\' && i + 1 < raw.len() {
                    buf.push(raw[i + 1]);
                    i += 2;
                    continue;
                }
                if ch == q && !(q == '\'' && is_apostrophe(&raw, i)) {
                    quote = None;
                }
            } else if ch == '"' || (ch == '\'' && !(i > 0 && raw[i - 1].is_alphanumeric())) {
                quote = Some(ch);
                buf.push(ch);
            } else if ch == '.' && !(i > 0 && i + 1 < raw.len() && raw[i - 1].is_ascii_digit() && raw[i + 1].is_ascii_digit()) {
                push(&mut out, &buf, false);
                buf.clear();
            } else if ch == ':' {
                let rest: String = raw[i + 1..].iter().collect();
                let rest = rest.trim().trim_matches('.').trim().to_string();
                push(&mut out, &buf, !rest.is_empty());
                buf.clear();
            } else {
                buf.push(ch);
            }
            i += 1;
        }
        if quote.is_some() {
            return Err(EngError::new("A quote is opened but never closed.", lineno, file));
        }
        push(&mut out, &buf, false);
    }
    Ok(out)
}

fn escape(c: char) -> char {
    match c {
        'n' => '\n',
        't' => '\t',
        c => c,
    }
}

pub fn tokenize(text: &str, line: u32, file: &str) -> Res<Vec<Tok>> {
    let s: Vec<char> = text.chars().collect();
    let mut toks = Vec::new();
    let mut i = 0;
    let slice = |a: usize, b: usize| -> String { s[a..b].iter().collect() };
    while i < s.len() {
        let ch = s[i];
        if ch.is_whitespace() {
            i += 1;
        } else if ch == '"' || (ch == '\'' && !(i > 0 && s[i - 1].is_alphanumeric())) {
            let mut j = i + 1;
            let mut value = String::new();
            while j < s.len() && !(s[j] == ch && !(ch == '\'' && is_apostrophe(&s, j))) {
                if s[j] == '\\' && j + 1 < s.len() {
                    value.push(escape(s[j + 1]));
                    j += 2;
                    continue;
                }
                value.push(s[j]);
                j += 1;
            }
            toks.push(Tok::new("STR", &value, &slice(i, (j + 1).min(s.len()))));
            i = j + 1;
        } else if ch.is_ascii_digit() {
            let mut j = i;
            while j < s.len() && s[j].is_ascii_digit() {
                j += 1;
            }
            if j + 1 < s.len() && s[j] == '.' && s[j + 1].is_ascii_digit() {
                j += 1;
                while j < s.len() && s[j].is_ascii_digit() {
                    j += 1;
                }
                let t = slice(i, j);
                let x: f64 = t.parse().unwrap();
                toks.push(Tok { kind: "DEC".into(), value: float_repr(x), num: 0, dec: x, text: t, sym: false });
            } else {
                let t = slice(i, j);
                let n: i64 = t.parse().unwrap_or(i64::MAX);
                toks.push(Tok::int(n, &t));
            }
            i = j;
        } else if ch.is_alphabetic() || ch == '_' {
            let mut j = i;
            while j < s.len()
                && (s[j].is_alphanumeric() || s[j] == '_' || ((s[j] == '\'' || s[j] == '-') && j + 1 < s.len() && s[j + 1].is_alphabetic()))
            {
                j += 1;
            }
            let t = slice(i, j);
            toks.push(Tok::word(&t.to_lowercase(), &t));
            i = j;
        } else if i + 1 < s.len() && [">=", "<=", "!="].contains(&slice(i, i + 2).as_str()) {
            let t = slice(i, i + 2);
            toks.push(Tok::new("SYM", &t, &t));
            i += 2;
        } else if "+-*×/<>=%^".contains(ch) {
            toks.push(Tok::new("SYM", &ch.to_string(), &ch.to_string()));
            i += 1;
        } else if ch == ',' {
            toks.push(Tok::new("COMMA", ",", ","));
            i += 1;
        } else if ch == '(' || ch == ')' {
            toks.push(Tok::new(if ch == '(' { "LP" } else { "RP" }, &ch.to_string(), &ch.to_string()));
            i += 1;
        } else {
            return Err(EngError::new(
                format!("I don't understand the symbol \"{}\". Put it inside quotes if it is part of a text.", ch),
                line,
                file,
            ));
        }
    }
    Ok(toks)
}

/// Contractions, number words, possessives, articles and filler. Pure and deterministic.
pub fn normalize(toks: &[Tok], lx: &Lexicon) -> Vec<Tok> {
    let mut out: Vec<Tok> = Vec::new();
    for t in toks {
        if t.kind == "WORD" {
            if let Some(c) = lx.contractions.get(&t.value) {
                for w in c.split_whitespace() {
                    out.push(Tok::word(w, w));
                }
                continue;
            }
        }
        out.push(t.clone());
    }
    let toks = out;

    // number words: "twenty-one" -> 21, "twice" -> 2 times
    let mut out: Vec<Tok> = Vec::new();
    let mut i = 0;
    while i < toks.len() {
        let t = &toks[i];
        if t.kind == "WORD" {
            if let Some(&n) = lx.repeat_words.get(&t.value) {
                out.push(Tok::int(n, &t.text));
                out.push(Tok::word("times", "times"));
                i += 1;
                continue;
            }
        }
        let mut parts: Vec<String> = Vec::new();
        let mut j = i;
        while j < toks.len() && toks[j].kind == "WORD" {
            let pieces: Vec<&str> = toks[j].value.split('-').collect();
            if pieces == ["one"]
                && ((j + 1 < toks.len() && toks[j + 1].value == "of")
                    || (j > 0 && ["next", "this", "that", "each", "any"].contains(&toks[j - 1].value.as_str())))
            {
                break;
            }
            if pieces.iter().all(|p| lx.numbers.contains_key(*p)) {
                parts.extend(pieces.iter().map(|p| p.to_string()));
                j += 1;
            } else if toks[j].value == "and"
                && !parts.is_empty()
                && j + 1 < toks.len()
                && lx.numbers.contains_key(&toks[j + 1].value)
                && (parts.last().unwrap() == "hundred" || parts.last().unwrap() == "thousand")
            {
                j += 1;
            } else {
                break;
            }
        }
        if !parts.is_empty() {
            let (mut total, mut cur) = (0i64, 0i64);
            for p in &parts {
                let n = lx.numbers[p];
                if n == 100 {
                    cur = (if cur != 0 { cur } else { 1 }) * 100;
                } else if n == 1000 {
                    total += (if cur != 0 { cur } else { 1 }) * 1000;
                    cur = 0;
                } else {
                    cur += n;
                }
            }
            let text: Vec<&str> = toks[i..j].iter().map(|x| x.text.as_str()).collect();
            out.push(Tok::int(total + cur, &text.join(" ")));
            i = j;
        } else {
            out.push(t.clone());
            i += 1;
        }
    }
    let toks = out;

    // possessives: "the user's score" -> one name, user.score
    let mut out: Vec<Tok> = Vec::new();
    let mut i = 0;
    while i < toks.len() {
        let t = &toks[i];
        if t.kind == "WORD" && t.value.ends_with("'s") && i + 1 < toks.len() && toks[i + 1].kind == "WORD" {
            let base = &t.value[..t.value.len() - 2];
            let fld = &toks[i + 1];
            out.push(Tok::word(&format!("{}.{}", base, fld.value), &format!("{} {}", t.text, fld.text)));
            i += 2;
        } else {
            out.push(t.clone());
            i += 1;
        }
    }
    let toks: Vec<Tok> = out
        .into_iter()
        .filter(|t| !(t.kind == "WORD" && (lx.articles.contains(&t.value) || lx.filler.contains(&t.value))))
        .collect();

    // label words: "print the word 'Hello'", "say the number 5"
    let mut out = Vec::new();
    for (i, t) in toks.iter().enumerate() {
        let nxt = toks.get(i + 1);
        let prev = if i > 0 { toks.get(i - 1) } else { None };
        if t.kind == "WORD" && lx.label_words.contains(&t.value) && !lx.protected.contains(&t.value) {
            if let Some(nxt) = nxt {
                let literal = ["STR", "NUM", "DEC"].contains(&nxt.kind.as_str());
                let after_print = prev.is_some_and(|p| p.kind == "WORD" && lx.print_forms.contains(&p.value))
                    && nxt.kind == "WORD"
                    && !lx.phrase_starts.contains(&(t.value.clone(), nxt.value.clone()));
                if literal || after_print {
                    continue;
                }
            }
        }
        out.push(t.clone());
    }
    out
}

pub const USER: &str = "their."; // prefix of per-user variables: "their score" -> their.score

/// Map surface words and symbols to canonical keywords, longest phrase first.
pub fn apply_lexicon(toks: &[Tok], lx: &Lexicon) -> Vec<Tok> {
    let mut out: Vec<Tok> = Vec::new();
    let mut i = 0;
    while i < toks.len() {
        let t = &toks[i];
        if t.kind == "SYM" {
            let mut k = Tok::new(&lx.symbols[&t.value], &t.value, &t.text);
            k.sym = true;
            out.push(k);
            i += 1;
            continue;
        }
        if t.kind == "WORD" {
            let mut matched = false;
            let mut n = lx.max_phrase.min(toks.len() - i);
            while n > 0 {
                let window = &toks[i..i + n];
                if window.iter().all(|w| w.kind == "WORD") {
                    let key: Vec<String> = window.iter().map(|w| w.value.clone()).collect();
                    if let Some(kw) = lx.phrases.get(&key) {
                        if !(n > 1 && lx.protected.contains(&key[0]) && key[1] == "of") {
                            let text: Vec<&str> = window.iter().map(|w| w.text.as_str()).collect();
                            out.push(Tok::new(kw, &key.join(" "), &text.join(" ")));
                            i += n;
                            matched = true;
                            break;
                        }
                    }
                }
                n -= 1;
            }
            if matched {
                continue;
            }
        }
        out.push(t.clone());
        i += 1;
    }
    // "their score": a variable that each Telegram user has separately
    let mut merged = Vec::new();
    let mut i = 0;
    while i < out.len() {
        let t = &out[i];
        if t.kind == "WORD" && t.value == "their" && i + 1 < out.len() && out[i + 1].kind == "WORD" {
            merged.push(Tok::word(&format!("{}{}", USER, out[i + 1].value), &format!("{} {}", t.text, out[i + 1].text)));
            i += 2;
        } else {
            merged.push(t.clone());
            i += 1;
        }
    }
    merged
}

pub fn canonical(text: &str, line: u32, file: &str, lx: &Lexicon) -> Res<Vec<Tok>> {
    Ok(apply_lexicon(&normalize(&tokenize(text, line, file)?, lx), lx))
}

pub fn show(toks: &[Tok]) -> String {
    toks.iter().map(|t| if t.text.is_empty() { t.value.clone() } else { t.text.clone() }).collect::<Vec<_>>().join(" ")
}
