import glob
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
from plainc.core import dump_program  # noqa: E402
from plainc.errors import AmbiguityError, EngError  # noqa: E402

EX = os.path.join(ROOT, "examples")
BOTS = os.path.join(EX, "bots")
BUILD = tempfile.mkdtemp(prefix="plain-tests-")


def src(name):
    with open(os.path.join(EX, name), encoding="utf-8") as f:
        return f.read()


def run_text(text, argv=(), inp=""):
    out = io.StringIO()
    engc.run(engc.compile_source(text), argv, out, io.StringIO(inp))
    return out.getvalue()


def core(text):
    return dump_program(engc.compile_source(text).prog)


def native(text, argv=(), inp="", cwd=None, file="prog.eng"):
    """Build text into an executable and run it: (stdout, stderr, exit code)."""
    path = os.path.join(BUILD, file)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    exe = path[:-4]
    engc.build(engc.compile_file(path), exe)
    p = subprocess.run([exe, *argv], input=inp, capture_output=True, text=True, cwd=cwd or BUILD)
    return p.stdout, p.stderr, p.returncode


def interpreted(text, argv=(), inp="", cwd=None, file="prog.eng"):
    path = os.path.join(BUILD, file)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "engc.py"), "run", path, *argv], input=inp,
                       capture_output=True, text=True, cwd=cwd or BUILD)
    return p.stdout, p.stderr, p.returncode


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


class Language(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(run_text("say 1 + 2 * 3."), "7\n")
        self.assertEqual(run_text("say (1 plus 2) times 3."), "9\n")
        self.assertEqual(run_text("say 10 minus 3 minus 2."), "5\n")
        self.assertEqual(run_text("say 7 / 2. say 7.0 / 2. say -7 mod 2. say 2 to the power of 10."),
                         "3\n3.5\n-1\n1024\n")
        self.assertEqual(run_text("say the remainder of 17 divided by 5. say 17 mod 5."), "2\n2\n")

    def test_decimals(self):
        self.assertEqual(run_text("set total to 0. add 0.5 to total. say total. say 2.5 rounded. say 10 / 4.0."),
                         "0.5\n3\n2.5\n")
        self.assertEqual(run_text("say the square root of 16. say 3.7 rounded down. say 3.2 rounded up."),
                         "4.0\n3\n4\n")

    def test_text(self):
        self.assertEqual(run_text('say "Hello" in uppercase. say "ҚАЗАҚ Кофе" in lowercase.'), "HELLO\nқазақ кофе\n")
        self.assertEqual(run_text('say the words of "a b  c" joined with "-".'), "a-b-c\n")
        self.assertEqual(run_text('say "banana" with "a" replaced by "o". say "  x  " trimmed and then "|".'),
                         "bonono\nx |\n")
        self.assertEqual(run_text('say the number of letters in "қала". say letter 2 of "қала".'), "4\nа\n")
        self.assertEqual(run_text('say "42" as a number plus 1. if "1.5" is a number, say "yes".'), "43\nyes\n")

    def test_lists(self):
        text = ('the guests are "Ana", "Bob" and "Dana". add "Eve" to the guests. remove "Bob" from the guests. '
                'say the guests. sort the guests. reverse the guests. say item 1 of the guests. '
                'say the number of items in the guests. if the guests contain "Ana", say "Ana is here".')
        self.assertEqual(run_text(text), "Ana, Dana and Eve\nEve\n3\nAna is here\n")
        self.assertEqual(run_text("set s to 3, 5 and 8. say the sum of s. say the largest of s. say items 2 to 3 of s."),
                         "16\n8\n5 and 8\n")

    def test_tables(self):
        text = ('the price of "coffee" is 900. the price of "tea" is 600. '
                'if the price of "juice" is unknown, say "no juice". say the price. '
                'add 1 to the count of "x". add 1 to the count of "x". say the count of "x".')
        self.assertEqual(run_text(text), "no juice\ncoffee: 900, tea: 600\n2\n")

    def test_loops(self):
        self.assertEqual(run_text("for every n from 3 to 1, say n."), "3\n2\n1\n")
        text = ("set n to 0.\nas long as n is less than 100\nincrease n by 7.\n"
                "if n is more than 30, stop the loop.\nthat's all.\nsay n.\n"
                "for every k from 1 to 5\nif the remainder of k divided by 2 is 0, skip to the next one.\nsay k.\n"
                "that's all.\n")
        self.assertEqual(run_text(text), "35\n1\n3\n5\n")

    def test_functions(self):
        text = ("to greet someone, say \"Hi\" and then someone.\ngreet \"Ana\".\n"
                "to find the factorial of a number\n  if the number is at most 1, give back 1.\n"
                "  give back the number times the factorial of (the number minus 1).\nthat's all.\n"
                "say the factorial of 10.\n"
                "to find a number doubled, give back the number times 2.\nsay 21 doubled.\n")
        self.assertEqual(run_text(text), "Hi Ana\n3628800\n42\n")

    def test_if_block_with_otherwise_if(self):
        text = "set x to 2.\nif x is 1, say one.\notherwise, if x is 2\nsay \"second\".\nsay \"really\".\nthat's all.\n"
        self.assertEqual(run_text(text), "second\nreally\n")

    def test_it_and_comments(self):
        self.assertEqual(run_text("Note: a comment.\n# another\nset x to 4. double it. say it."), "8\n")

    def test_arguments_and_input(self):
        self.assertEqual(run_text("say the arguments.", ["a", "b"]), "a and b\n")
        out = run_text('ask "Age?" and remember the answer as age. say age plus 1.', inp="x\n41\n")
        self.assertEqual(out, "Age? Please type a whole number.\nAge? 42\n")


class Objects(unittest.TestCase):
    BOOK = 'a book has a title, an author and a year.\n'

    def test_fields_and_printing(self):
        text = self.BOOK + ('set dune to a new book with title "Dune", author "Frank Herbert", and year 1965.\n'
                            "say the title of dune. say dune's author. add 1 to the year of dune. say dune.\n")
        self.assertEqual(run_text(text), "Dune\nFrank Herbert\nbook (title: Dune, author: Frank Herbert, year: 1966)\n")

    def test_methods_and_lists_of_things(self):
        text = self.BOOK + ('to describe a book, say the title of the book and then "(" and then the year of the book '
                            'and then ")".\nset shelf to an empty list.\n'
                            'add a new book with title "Emma" and year 1815 to shelf.\n'
                            'for every book in shelf, describe book.\n')
        self.assertEqual(run_text(text), "Emma (1815)\n")

    def test_nothing(self):
        text = ("a node has a value and a next.\nset n to a new node with value 1.\n"
                "if the next of n is nothing, say \"end\".\nset the next of n to n.\n"
                "if the next of n is not nothing, say \"loop\".\nsay n.\n")
        self.assertEqual(run_text(text), "end\nloop\nnode (value: 1, next: node (value: 1, next: node (value: 1, "
                                         "next: node (...))))\n")

    def test_generic_functions(self):
        text = ("to find the first of some things, give back item 1 of the things.\n"
                "to find the twofold of a number, give back the number times 2.\n"
                'say the first of (5, 6 and 7) and then the first of ("a" and "b").\n'
                "say the twofold of 21 and then the twofold of 1.25.\n")
        self.assertEqual(run_text(text), "5 a\n42 2.5\n")

    def test_object_errors(self):
        with self.assertRaises(EngError):
            engc.compile_source(self.BOOK + 'set b to a new book with title "x".\nset the title of b to 5.\n')
        text = ("a node has a value and a next.\nset n to a new node with value 1.\n"
                "set m to a new node with value 2 and next n.\nsay the value of the next of n.\n")
        a, b = interpreted(text), native(text)
        self.assertEqual(a, b)
        self.assertIn("There is no node here: it is nothing.", a[1])


class ListsAndErrors(unittest.TestCase):
    def test_sort_filter_map_best(self):
        text = ("set scores to 40, 95, 70 and 95.\nsort scores from highest to lowest.\nsay scores.\n"
                "say n times 2 for every n in scores where n is more than 50.\n"
                "set words to \"ccc\", \"a\" and \"bb\".\nsort words by the length of each.\nsay words.\n"
                "say the w in words with the largest length of w.\n")
        self.assertEqual(run_text(text), "95, 95, 70 and 40\n190, 190 and 140\na, bb and ccc\nccc\n")

    def test_try_and_fail(self):
        text = ("try the following.\n  say item 9 of (1 and 2).\nif something goes wrong\n"
                "  say \"caught:\" and then the error.\nthat's all.\n"
                "try to fail with \"boom\", and if that fails, say the error.\n"
                "set total to 0.\nfor every n from 1 to 5\n  try the following.\n    add n to total.\n"
                "    if n is 3, stop the loop.\n  that's all.\nthat's all.\nsay total.\n")
        expected = "caught: There is no item 9: the list has 2 items.\nboom\n6\n"
        self.assertEqual(run_text(text), expected)
        self.assertEqual(native(text)[0], expected)

    def test_uncaught_fail(self):
        a, b = interpreted('fail with "bad input".\n'), native('fail with "bad input".\n')
        self.assertEqual(a, b)
        self.assertEqual(a[1], "prog.eng, line 1:\nbad input\n")

    def test_long_sentences(self):
        self.assertEqual(run_text('say "one," and then\n  "two" and then "three"\n  and then "four".\n'),
                         "one, two three four\n")


class Errors(unittest.TestCase):
    def test_plus_times_is_ambiguous(self):
        with self.assertRaises(AmbiguityError) as cm:
            engc.compile_source(src("ambiguous.eng"))
        self.assertIn("1 plus (2 times 3)", cm.exception.message)
        self.assertIn("(1 plus 2) times 3", cm.exception.message)

    def test_typo_suggestion(self):
        with self.assertRaises(EngError) as cm:
            engc.compile_source(src("typo.eng"))
        self.assertIn('Did you mean "print"', cm.exception.message)

    def test_type_conflict(self):
        with self.assertRaises(EngError) as cm:
            engc.compile_source(src("type_error.eng"))
        self.assertEqual(cm.exception.line, 2)
        self.assertIn("whole number", cm.exception.message)

    def test_mixed_list(self):
        with self.assertRaises(EngError):
            engc.compile_source('set x to 1, "two" and 3.')

    def test_break_outside_loop(self):
        with self.assertRaises(EngError):
            engc.compile_source("stop the loop.")

    def test_runtime_errors_match(self):
        cases = ["set l to 1, 2 and 3.\nsay item 5 of l.\n",
                 "set x to 0.\nsay 5 / x.\n",
                 'say "abc" as a number.\n',
                 'the price of "tea" is 5.\nsay the price of "milk".\n',
                 'read the file "nowhere.txt" into t.\n']
        for text in cases:
            a, b = interpreted(text), native(text)
            self.assertEqual(a, b, text)
            self.assertEqual(a[2], 1, text)
            self.assertTrue(a[1].startswith("prog.eng, line "), a[1])


class Examples(unittest.TestCase):
    """Every example: interpreter, native binary and the recorded output agree."""

    def test_examples(self):
        paths = sorted(glob.glob(os.path.join(EX, "*.expected")) + glob.glob(os.path.join(EX, "*", "*.expected")))
        self.assertGreater(len(paths), 10)
        for expected_path in paths:
            program = expected_path[:-len(".expected")] + ".eng"
            folder = os.path.dirname(program)
            inp_path = expected_path[:-len(".expected")] + ".input"
            inp = open(inp_path).read() if os.path.exists(inp_path) else ""
            expected = open(expected_path, encoding="utf-8").read()
            with self.subTest(program=os.path.relpath(program, EX)):
                p = subprocess.run([sys.executable, os.path.join(ROOT, "engc.py"), "run", program], input=inp,
                                   capture_output=True, text=True, cwd=folder)
                self.assertEqual(p.stdout, expected)
                exe = os.path.join(BUILD, os.path.basename(program)[:-4])
                engc.build(engc.compile_file(program), exe)
                p = subprocess.run([exe], input=inp, capture_output=True, text=True, cwd=folder)
                self.assertEqual(p.stdout, expected)

    def test_todo_cli(self):
        work = tempfile.mkdtemp(prefix="plain-todo-")
        exe = os.path.join(BUILD, "todo")
        engc.build(engc.compile_file(os.path.join(EX, "todo.eng")), exe)
        steps = [(["add", "milk"], "Added: milk\n"), (["add", "bread"], "Added: bread\n"),
                 ([], "1. milk\n2. bread\n"), (["done", "1"], "Done: milk\n"), ([], "1. bread\n")]
        for args, expected in steps:
            out = subprocess.run([exe, *args], capture_output=True, text=True, cwd=work).stdout
            self.assertEqual(out, expected, args)


def bot_replies(name, script, backend, cwd=None):
    """Talk to a bot through the fake Telegram server, interpreted or compiled."""
    path = os.path.join(BOTS, name)
    mock = MockTelegram(script)
    if backend == "native":
        exe = os.path.join(BUILD, name[:-4] + "-bot")
        engc.build(engc.compile_file(path), exe)
        cmd = [exe]
    else:
        cmd = [sys.executable, os.path.join(ROOT, "engc.py"), "run", path]
    old = os.getcwd()
    os.chdir(cwd or BUILD)
    try:
        return mock.run(cmd)
    finally:
        os.chdir(old)


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
            self.assertLessEqual(len(replies) - 1, 7, b)

    def test_magic8_and_shop(self):
        for b in self.BACKENDS:
            replies = bot_replies("magic8.eng", say("will it rain?", "hello"), b)
            self.assertIn(replies[0][0], ["Yes.", "No.", "Maybe.", "Definitely.", "Ask again later."], b)
            self.assertTrue(replies[1][0].startswith("That is not a question"), b)
            replies = bot_replies("shop.eng", say("MENU", "Prices", "how much is a coffee", "Кофе есть?"), b)
            self.assertEqual(replies[0], ("What do you want to know?", ["Prices", "Hours", "Contact"]), b)
            self.assertEqual(replies[1][0], "Coffee 900 tenge, tea 600 tenge.", b)
            self.assertEqual(replies[2][0], "Coffee is 900 tenge. Come by!", b)
            self.assertEqual(replies[3][0], "Coffee is 900 tenge. Come by!", b)

    def test_showcase(self):
        for b in self.BACKENDS:
            replies = bot_replies("showcase.eng", say("/start", "Doubler", "21", "Times table", "3", "Hello"), b)
            self.assertEqual(len(replies[0][1]), 13, b)
            self.assertEqual(replies[2][0], "Twice that is 42.", b)
            self.assertEqual(replies[4][0].splitlines()[-1], "3 × 10 = 30", b)
            self.assertEqual(replies[5][0], "\n".join(["Hello"] * 5), b)

    def test_todo_bot_remembers_after_restart(self):
        for b in self.BACKENDS:
            work = tempfile.mkdtemp(prefix=f"plain-memory-{b}-")
            first = bot_replies("todo_bot.eng", say("buy milk", "call mom"), b, cwd=work)
            self.assertEqual(first[-1][0], "Added. You have 2 tasks. All users added 2 so far.", b)
            second = bot_replies("todo_bot.eng", say("/list", "/done 1", "/list"), b, cwd=work)
            self.assertEqual([r[0] for r in second],
                             ["Your tasks:\n1. buy milk\n2. call mom", "Done: buy milk", "Your tasks:\n1. call mom"], b)

    def test_bot_words_outside_a_rule(self):
        with self.assertRaises(EngError):
            engc.compile_source('reply "hi".')
        with self.assertRaises(EngError):
            engc.compile_source("set their score to 1.")


if __name__ == "__main__":
    unittest.main()
