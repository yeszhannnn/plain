import io
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engc  # noqa: E402
from mock_telegram import MockTelegram, say  # noqa: E402

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
                subprocess.run(["clang", "-O2", "-Wno-parentheses-equality", c, engc.RUNTIME, "-o", exe] + engc.link_flags(prog), check=True)
            out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, run_text(src(name)), f"{name} via {kind}")

    def test_native_matches_interpreter(self):
        for name in ("hello_print.eng", "program.eng", "fizzbuzz.eng", "collatz.eng"):
            for kind in ("llvm", "c"):
                self.check_backend(name, kind)


BOTS = os.path.join(EX, "bots")


def bot_replies(name, script, backend):
    """Talk to a bot through the fake Telegram server, interpreted or compiled."""
    path = os.path.join(BOTS, name)
    mock = MockTelegram(script)
    with tempfile.TemporaryDirectory() as d:
        if backend == "native":
            exe = os.path.join(d, "bot")
            prog, types = engc.compile_front(open(path, encoding="utf-8").read())
            engc.build(prog, types, exe)
            cmd = [exe]
        else:
            cmd = [sys.executable, os.path.join(ROOT, "engc.py"), "run", path]
        return mock.run(cmd)


class Bots(unittest.TestCase):
    BACKENDS = ("interpreter", "native")

    def test_echo(self):
        for b in self.BACKENDS:
            replies = bot_replies("echo.eng", say("/start", "hello there"), b)
            self.assertEqual(replies, [("Hi Ana! Send me anything and I will repeat it.", []),
                                       ("You said: hello there", [])], b)

    def test_counter_remembers_each_user(self):
        for b in self.BACKENDS:
            replies = bot_replies("counter.eng", say("/start", "+1", "+1", "reset", "what"), b)
            self.assertEqual([r[0] for r in replies], [
                "Hi Ana! Press a button.",
                "Your count is 1. All users together: 1.",
                "Your count is 2. All users together: 2.",
                "Your count is back to 0.",
                "Press a button."], b)
            self.assertEqual(replies[0][1], ["+1", "reset"], b)

    def test_guess_the_number(self):
        def binary_search(replies):
            st = binary_search.state
            if not replies:
                return "/start"
            last = replies[-1][0]
            if last.startswith("Yes!"):
                return None
            if last == "Lower.":
                st["hi"] = st["guess"] - 1
            elif last == "Higher.":
                st["lo"] = st["guess"] + 1
            st["guess"] = (st["lo"] + st["hi"]) // 2
            return str(st["guess"])

        for b in self.BACKENDS:
            binary_search.state = {"lo": 1, "hi": 100, "guess": 0}
            replies = bot_replies("guess.eng", binary_search, b)
            self.assertTrue(replies[-1][0].startswith("Yes! You needed"), (b, replies))
            self.assertLessEqual(len(replies) - 1, 7, b)   # binary search needs at most 7 guesses

    def test_magic8_and_shop(self):
        for b in self.BACKENDS:
            replies = bot_replies("magic8.eng", say("will it rain?", "hello"), b)
            self.assertIn(replies[0][0], ["Yes.", "No.", "Maybe.", "Definitely.", "Ask again later."], b)
            self.assertTrue(replies[1][0].startswith("That is not a question"), b)
            replies = bot_replies("shop.eng", say("MENU", "Prices", "how much is a coffee", "кофе есть?"), b)
            self.assertEqual(replies[0], ("What do you want to know?", ["Prices", "Hours", "Contact"]), b)
            self.assertEqual(replies[1][0], "Coffee 900 tenge, tea 600 tenge.", b)
            self.assertEqual(replies[2][0], "Coffee is 900 tenge. Come by!", b)
            self.assertEqual(replies[3][0], "Coffee is 900 tenge. Come by!", b)

    def test_showcase(self):
        for b in self.BACKENDS:
            replies = bot_replies("showcase.eng", say("/start", "Doubler", "21", "Times table", "3", "Hello"), b)
            self.assertEqual(len(replies[0][1]), 12, b)
            self.assertEqual(replies[2][0], "Twice that is 42.", b)
            self.assertEqual(replies[4][0].splitlines()[-1], "3 × 10 = 30", b)
            self.assertEqual(replies[5][0], "\n".join(["Hello"] * 5), b)

    def test_glued_text_and_otherwise_if_block(self):
        text = ('set t to "n:". set i to 1. do the following 3 times. '
                'set t to t and then a new line and then i. increase i. that\'s all. say t.\n'
                'if i is 1, say one.\notherwise, if i is 4\nsay four.\nsay "really".\nthat\'s all.\n')
        self.assertEqual(run_text(text), "n:\n1\n2\n3\n4\nreally\n")  # "four" is a number word

    def test_bot_words_outside_a_rule(self):
        with self.assertRaises(engc.EngError):
            engc.compile_front('reply "hi".')
        with self.assertRaises(engc.EngError):
            engc.compile_front("set their score to 1.")

    def test_remainder_and_random(self):
        self.assertEqual(run_text("say the remainder of 17 divided by 5. say 17 mod 5."), "2\n2\n")
        n = int(run_text("say a random number from 3 to 4."))
        self.assertIn(n, (3, 4))


if __name__ == "__main__":
    unittest.main()
