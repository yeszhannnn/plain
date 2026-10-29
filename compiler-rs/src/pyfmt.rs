//! Small pieces of Python behaviour the reference compiler relies on, so both compilers print the same
//! messages and the same LLVM IR: float repr, json.dumps of a text, difflib.get_close_matches.

use std::collections::HashMap;

/// repr(x) for a Python float: the shortest text that reads back as x.
pub fn float_repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let e = format!("{:e}", x); // shortest round-trip digits: "-1.2345e3"
    let (mant, exp) = e.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    let (neg, mant) = match mant.strip_prefix('-') {
        Some(m) => (true, m),
        None => (false, mant),
    };
    let digits: String = mant.chars().filter(|c| *c != '.').collect();
    let mut out = String::new();
    if neg {
        out.push('-');
    }
    if !(-4..16).contains(&exp) {
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push('e');
        out.push(if exp < 0 { '-' } else { '+' });
        out.push_str(&format!("{:02}", exp.abs()));
    } else if exp >= 0 {
        let point = exp as usize + 1;
        if digits.len() <= point {
            out.push_str(&digits);
            out.push_str(&"0".repeat(point - digits.len()));
            out.push_str(".0");
        } else {
            out.push_str(&digits[..point]);
            out.push('.');
            out.push_str(&digits[point..]);
        }
    } else {
        out.push_str("0.");
        out.push_str(&"0".repeat((-exp - 1) as usize));
        out.push_str(&digits);
    }
    out
}

/// json.dumps(s, ensure_ascii=False)
pub fn json_str(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn matching_size(a: &[char], b: &[char]) -> usize {
    let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
    for (j, c) in b.iter().enumerate() {
        b2j.entry(*c).or_default().push(j);
    }
    let longest = |alo: usize, ahi: usize, blo: usize, bhi: usize| -> (usize, usize, usize) {
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0usize);
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        for (i, ch) in a.iter().enumerate().take(ahi).skip(alo) {
            let mut new: HashMap<usize, usize> = HashMap::new();
            if let Some(js) = b2j.get(ch) {
                for &j in js {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let k = (if j > 0 { j2len.get(&(j - 1)).copied().unwrap_or(0) } else { 0 }) + 1;
                    new.insert(j, k);
                    if k > bestsize {
                        besti = i + 1 - k;
                        bestj = j + 1 - k;
                        bestsize = k;
                    }
                }
            }
            j2len = new;
        }
        (besti, bestj, bestsize)
    };
    let mut total = 0;
    let mut queue = vec![(0, a.len(), 0, b.len())];
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, k) = longest(alo, ahi, blo, bhi);
        if k > 0 {
            total += k;
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    total
}

/// difflib.get_close_matches(word, pool, n=1, cutoff)
pub fn close_match(word: &str, pool: &[String], cutoff: f64) -> Option<String> {
    let b: Vec<char> = word.chars().collect();
    let mut best: Option<(f64, &String)> = None;
    for x in pool {
        let a: Vec<char> = x.chars().collect();
        let total = a.len() + b.len();
        let ratio = if total == 0 { 1.0 } else { 2.0 * matching_size(&a, &b) as f64 / total as f64 };
        if ratio >= cutoff {
            let better = match best {
                None => true,
                Some((s, bx)) => ratio > s || (ratio == s && x > bx),
            };
            if better {
                best = Some((ratio, x));
            }
        }
    }
    best.map(|(_, x)| x.clone())
}

/// os.path.basename
pub fn basename(path: &str) -> String {
    path.rsplit('/').next().unwrap_or("").to_string()
}
