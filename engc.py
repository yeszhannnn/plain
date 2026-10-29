#!/usr/bin/env python3
"""engc: compiler for Plain, a programming language written as plain English.

Usage:
    engc.py run   FILE [ARGS...]      run the program right away
    engc.py build FILE [-o OUT]       compile to a native executable (LLVM + clang)
    engc.py ir    FILE                print the LLVM IR       (alias: --emit-llvm)
    engc.py core  FILE                print the Core IR       (alias: --dump-core)

Telegram bots (programs with "when someone sends ...") read the token from the
TELEGRAM_BOT_TOKEN environment variable.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from plainc import interpreter  # noqa: E402
from plainc.core import dump_program, is_bot  # noqa: E402
from plainc.errors import EngError  # noqa: E402
from plainc.parser import parse_program  # noqa: E402
from plainc.types import check  # noqa: E402


class Program:
    def __init__(self, prog, funcs, types, file):
        self.prog, self.funcs, self.types, self.file = prog, funcs, types, file

    @property
    def is_bot(self):
        return is_bot(self.prog)


def compile_source(src, file=""):
    prog, funcs = parse_program(src, file)
    return Program(prog, funcs, check(prog, funcs), file)


def compile_file(path):
    with open(path, encoding="utf-8") as f:
        return compile_source(f.read(), path)


def run(program, argv=(), out=sys.stdout, inp=sys.stdin):
    interpreter.run(program.prog, program.types, argv, out, inp)


def emit_llvm(program):
    from plainc import llvm
    return llvm.emit(program.prog, program.funcs, program.types, program.file)


def build(program, output):
    from plainc import llvm
    llvm.build(program.prog, program.funcs, program.types, program.file, output)


ALIASES = {"--emit-llvm": "ir", "--dump-core": "core"}


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    cmd, path = ALIASES.get(argv[0], argv[0]), argv[1]
    try:
        program = compile_file(path)
        if cmd == "run":
            run(program, argv[2:])
        elif cmd == "core":
            print(dump_program(program.prog))
        elif cmd == "ir":
            print(emit_llvm(program), end="")
        elif cmd == "build":
            out = argv[argv.index("-o") + 1] if "-o" in argv else os.path.splitext(path)[0]
            build(program, out)
            print(f"built {out}")
        else:
            print(f"unknown command {cmd}\n\n{__doc__.strip()}")
            return 2
    except EngError as e:
        sys.stdout.flush()
        print(e.report(os.path.basename(path)), file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"I can't find the file {path}.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
