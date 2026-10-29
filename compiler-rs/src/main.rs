//! plain: compiler for Plain, a programming language written as plain English.

mod core;
mod llvm;
mod parser;
mod pyfmt;
mod syntax;
mod types;

use std::path::PathBuf;
use std::process::{exit, Command};

use crate::syntax::{EngError, Lexicon};

const USAGE: &str = "plain: compiler for Plain, a programming language written as plain English.

Usage:
    plain run   FILE [ARGS...]      build the program and run it right away
    plain build FILE [-o OUT]       compile to a native executable (LLVM + clang)
    plain check FILE                only check the program, build nothing
    plain ir    FILE                print the LLVM IR
    plain core  FILE                print the Core IR

Telegram bots (programs with \"when someone sends ...\") read the token from the
TELEGRAM_BOT_TOKEN environment variable.";

struct Program {
    prog: Vec<core::S>,
    types: types::Types,
    file: String,
}

fn compile(path: &str) -> Result<Program, EngError> {
    let src = std::fs::read_to_string(path).map_err(|_| EngError::new(format!("I can't find the file {}.", path), 0, ""))?;
    let mut lx = Lexicon::load();
    let (mut prog, funcs) = parser::parse_program(&src, path, &mut lx)?;
    let types = types::check(&mut prog, &funcs)?;
    Ok(Program { prog, types, file: path.to_string() })
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() < 2 {
        println!("{}", USAGE);
        exit(2);
    }
    let cmd = match args[0].as_str() {
        "--emit-llvm" => "ir",
        "--dump-core" => "core",
        c => c,
    };
    let path = &args[1];
    let report = |e: EngError| -> ! {
        if e.line == 0 && e.file.is_empty() && e.message.starts_with("I can't find the file") {
            eprintln!("{}", e.message);
        } else {
            eprintln!("{}", e.report(&pyfmt::basename(path)));
        }
        exit(1)
    };
    if !["run", "build", "check", "ir", "core"].contains(&cmd) {
        println!("unknown command {}\n\n{}", cmd, USAGE);
        exit(2);
    }
    let program = compile(path).unwrap_or_else(|e| report(e));
    match cmd {
        "check" => println!("{}: no problems found.", pyfmt::basename(path)),
        "core" => println!("{}", core::dump_program(&program.prog)),
        "ir" => print!("{}", llvm::emit(&program.prog, &program.types, &program.file).unwrap_or_else(|e| report(e))),
        "build" => {
            let out = match args.iter().position(|a| a == "-o") {
                Some(i) if i + 1 < args.len() => args[i + 1].clone(),
                _ => path.strip_suffix(".eng").unwrap_or(path).to_string(),
            };
            llvm::build(&program.prog, &program.types, &program.file, &PathBuf::from(&out)).unwrap_or_else(|e| report(e));
            println!("built {}", out);
        }
        _ => {
            // run: build into a temporary folder and start it, like "go run"
            let dir = llvm::temp_dir("run");
            let exe = dir.join(pyfmt::basename(path.strip_suffix(".eng").unwrap_or(path)));
            llvm::build(&program.prog, &program.types, &program.file, &exe).unwrap_or_else(|e| report(e));
            let status = Command::new(&exe).args(&args[2..]).status();
            let _ = std::fs::remove_dir_all(&dir);
            match status {
                Ok(s) => exit(s.code().unwrap_or(1)),
                Err(e) => {
                    eprintln!("I couldn't start the program: {}", e);
                    exit(1)
                }
            }
        }
    }
}
