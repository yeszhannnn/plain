import io
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import engc  # noqa: E402

EX = os.path.join(ROOT, "examples")


def src(name):
    with open(os.path.join(EX, name), encoding="utf-8") as f:
        return f.read()


def run_text(text):
    prog, types = engc.compile_front(text)
    buf = io.StringIO()
    engc.run(prog, types, buf)
    return buf.getvalue()


def core(text):
    return engc.dump_program(engc.parse_program(text))


class SameMeaning(unittest.TestCase):
    def test_hello_variants_share_core_ir(self):
        expected = 'Repeat(Int(5))\n  Print(Text("Hello"))'
        for name in ("hello_print.eng", "hello_repeat.eng", "hello_say.eng", "hello_plain.eng"):
            self.assertEqual(core(src(name)), expected, name)

    def test_more_spellings(self):
        expected = core("say Hello 5 times")
        for text in ("SHOW 'Hello' FIVE TIMES.",
                     "display the text 'Hello' 5 times",
                     "do the following five times.\nsay Hello.\nthat's all.",
                     "repeat 5 times say Hello"):
            self.assertEqual(core(text), expected, text)

    def test_number_words(self):
        self.assertEqual(run_text("say twenty-one."), "21\n")
        self.assertEqual(run_text("say one hundred and five."), "105\n")
        self.assertEqual(run_text("say hi twice."), "hi\nhi\n")


class Programs(unittest.TestCase):
    def test_program_output(self):
        self.assertEqual(run_text(src("program.eng")),
                         "age is 18\nscore is 105\nwell done\n"
                         "still here\nstill here\nstill here\n"
                         "the counter is 3\n192\nyay\nyay\n")

    def test_symbols_follow_school_maths(self):
        self.assertEqual(run_text("say 1 + 2 * 3."), "7\n")
        self.assertEqual(run_text("say (1 plus 2) times 3."), "9\n")
        self.assertEqual(run_text("say 10 minus 3 minus 2."), "5\n")

    def test_if_block_with_otherwise(self):
        text = "set x to 2.\nif x is 1\nsay one.\notherwise\nsay other.\nthat's all.\n"
        self.assertEqual(run_text(text), "other\n")

    def test_else_if_chain(self):
        self.assertEqual(run_text(src("fizzbuzz.eng")).split()[:6], ["1", "2", "Fizz", "4", "Buzz", "Fizz"])

    def test_otherwise_inside_if_block(self):
        self.assertEqual(run_text(src("collatz.eng")), "steps: 111\nhighest: 9232\n")

    def test_it(self):
        self.assertEqual(run_text("set x to 4. double it. say it."), "8\n")


class Errors(unittest.TestCase):
    def test_plus_times_is_ambiguous(self):
        with self.assertRaises(engc.AmbiguityError) as cm:
            engc.compile_front(src("ambiguous.eng"))
        self.assertIn("1 plus (2 times 3)", cm.exception.message)
        self.assertIn("(1 plus 2) times 3", cm.exception.message)

    def test_times_as_operator_and_as_repetition(self):
        # "2 times 3" is multiplication, the trailing "4 times" is repetition
        self.assertEqual(run_text("say 2 times 3 4 times."), "6\n" * 4)
        with self.assertRaises(engc.EngError):
            engc.compile_front("say 2 times 3 times 4 times.")

    def test_typo_suggestion(self):
        with self.assertRaises(engc.EngError) as cm:
            engc.compile_front(src("typo.eng"))
        self.assertIn('Did you mean "print"', cm.exception.message)

    def test_type_conflict(self):
        with self.assertRaises(engc.EngError) as cm:
            engc.compile_front(src("type_error.eng"))
        self.assertEqual(cm.exception.line, 2)


class Native(unittest.TestCase):
    def check_backend(self, name, kind):
        prog, types = engc.compile_front(src(name))
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "a.out")
            if kind == "llvm":
                engc.build(prog, types, exe)
            else:
                c = os.path.join(d, "a.c")
                with open(c, "w") as f:
                    f.write(engc.emit_c(prog, types))
                subprocess.run(["clang", "-O2", c, "-o", exe], check=True)
            out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, run_text(src(name)), f"{name} via {kind}")

    def test_native_matches_interpreter(self):
        for name in ("hello_print.eng", "program.eng", "fizzbuzz.eng", "collatz.eng"):
            for kind in ("llvm", "c"):
                self.check_backend(name, kind)


if __name__ == "__main__":
    unittest.main()
