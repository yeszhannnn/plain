import glob
import io
import os
import shutil
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
    elif backend == "rust":
        exe = os.path.join(BUILD, name[:-4] + "-rs-bot")
        subprocess.run([PLAIN_RS, "build", path, "-o", exe], check=True, capture_output=True)
        cmd = [exe]
    else:
        cmd = [sys.executable, os.path.join(ROOT, "engc.py"), "run", path]
    old = os.getcwd()
    os.chdir(cwd or BUILD)
    try:
        return mock.run(cmd)
    finally:
        os.chdir(old)


STD = r"""a book has a title, a year (number), a price (decimal), some tags (a list of texts) and an author (a person).
a person has a name and an age (number).
set dune to a new book with title "Dune \"1\"", year 1965 and price 9.5.
add "sf" to the tags of dune.
set the author of dune to a new person with name "Frank" and age 60.
set data to dune as json.
say data.
set copy to data as a book.
say the title of copy and then the name of the author of copy and then the price of copy.
set many to "[{\"title\": \"A\", \"year\": 1}, {\"title\": \"B\"}]" as a list of books.
say the number of items in many and then the year of item 1 of many and then the price of item 2 of many.
set counts to "{\"a\": 1, \"b\": 2}" as a table of numbers.
say counts.
try to set bad to "{oops" as a book, and if that fails, say the error.
say pi with 3 decimals.
say the sine of 0 and then the logarithm of 1.
say the position of "lo" in "hello" and then the position of "z" in "hello".
say "[" and then "ab" padded to 5 and then "]" and then "ab" padded on the left to 5 and then "]".
say "ab" repeated 3 times.
say the code of "A" and then the letter with code 1046.
say 3.14159 with 2 decimals.
run "echo hi; exit 3" and remember the output as out.
say out and then the exit code.
say the environment variable "PLAIN_TEST_VAR".
set t to the current time.
if the year of t is more than 2020, say "year ok".
if the current timestamp is more than 1600000000, say "timestamp ok".
set start to the current time in milliseconds.
wait 50 milliseconds.
if the current time in milliseconds minus start is at least 40, say "wait ok".
set r to a random decimal from 0 to 1.
if r is at least 0 and r is less than 1, say "random ok".
"""

STD_OUT = """{"title": "Dune \\"1\\"", "year": 1965, "price": 9.5, "tags": ["sf"], "author": {"name": "Frank", "age": 60}}
Dune "1" Frank 9.5
2 1 0.0
a: 1, b: 2
The text is not valid JSON.
3.142
0.0 0.0
4 0
[ab   ]   ab]
ababab
65 Ж
3.14
hi
3
xyz
year ok
timestamp ok
wait ok
random ok
"""


class Library(unittest.TestCase):
    """The standard library: JSON, numbers, text, time and the system, in both modes."""

    def test_standard_library(self):
        os.environ["PLAIN_TEST_VAR"] = "xyz"
        self.assertEqual(interpreted(STD), (STD_OUT, "", 0))
        self.assertEqual(native(STD), (STD_OUT, "", 0))

    def test_field_types(self):
        with self.assertRaises(EngError) as e:
            engc.compile_source("a book has a title (colour).\n")
        self.assertIn('I don\'t know the type "colour"', str(e.exception))


class Web(unittest.TestCase):
    """fetch and send against a small server on this machine."""

    @classmethod
    def setUpClass(cls):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body=b""):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/weather":
                    self.reply(200, json.dumps({"latitude": 43.25, "current_weather": {
                        "temperature": 21.44, "windspeed": 7.2, "weathercode": 0}}).encode())
                else:
                    self.reply(404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                self.reply(200, f"{self.headers['Content-Type']} | {body}".encode())

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.no_proxy = os.environ.get("NO_PROXY")
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        if cls.no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = cls.no_proxy

    def test_weather_example(self):
        program = os.path.join(EX, "weather.eng")
        exe = os.path.join(BUILD, "weather")
        engc.build(engc.compile_file(program), exe)
        cases = [("/weather", "Almaty: 21.4 °C, wind 7.2 km/h.\nClear sky.\n"),
                 ("/missing", f"Could not get the weather: The request to {self.url}/missing failed with status 404.\n")]
        for path, expected in cases:
            with self.subTest(path=path):
                p = subprocess.run([sys.executable, os.path.join(ROOT, "engc.py"), "run", program, self.url + path],
                                   capture_output=True, text=True)
                self.assertEqual(p.stdout, expected)
                p = subprocess.run([exe, self.url + path], capture_output=True, text=True)
                self.assertEqual(p.stdout, expected)

    def test_send(self):
        text = (f'a order has a dish and a count.\nset o to a new order with dish "plov" and count 2.\n'
                f'send o to "{self.url}/orders" and remember the reply as r.\nsay r.\n'
                f'send "hi" to "{self.url}/notes" and remember the reply as r.\nsay r.\n'
                f'try to send "x" to "http://127.0.0.1:1/", and if that fails, say the error.\n')
        expected = ('application/json | {"dish": "plov", "count": 2}\ntext/plain; charset=utf-8 | hi\n'
                    "I couldn't reach http://127.0.0.1:1/.\n")
        self.assertEqual(interpreted(text), (expected, "", 0))
        self.assertEqual(native(text), (expected, "", 0))


TEXT_GROWTH = """set first to "x".
add "y" to first.
set b to first.
add "z" to first.
say first and then b.
set items to an empty list.
set s to "".
for every n from 1 to 3
  add n as text to s.
  add s to items.
that's all.
say items.
to mention some words, say the words.
add "!" to s.
mention s.
add "?" to s.
mention s.
add s to s.
say s.
set t to a new list.
set w to "q".
for every n from 1 to 3
  add "w" to w.
  add w to t.
  set item 1 of t to w.
that's all.
say t.
set total to "".
to grow by some piece
  add the piece to total.
that's all.
grow by "a".
set saved to total.
grow by "b".
say total and then saved.
if w is "qwww", say "same".
say the length of w.
"""


class Speed(unittest.TestCase):
    """Rules the faster native code must keep."""

    def test_text_grows_in_place_without_changing_copies(self):
        expected = "xyz xy\n1, 12 and 123\n123!\n123!?\n123!?123!?\nqwww, qww and qwww\nab a\nsame\n4\n"
        self.assertEqual(run_text(TEXT_GROWTH), expected)
        self.assertEqual(native(TEXT_GROWTH)[0], expected)

    def test_counting_direction(self):
        text = ("set n to 3.\nfor every i from n plus 1 to n, say i.\nfor every i from 3 to 1, say i.\n"
                "for every i from n down to 2, say i.\nset xs to 1, 2 and 3.\n"
                "for every i from 1 to 3\n  for every j from i plus 1 to 3, say item i of xs and then item j of xs.\n"
                "that's all.\n")
        expected = "3\n2\n1\n3\n2\n1 2\n1 3\n2 3\n"
        self.assertEqual(run_text(text), expected)
        self.assertEqual(native(text)[0], expected)

    def test_joined_values_in_brackets_and_add(self):
        text = 'set v to an empty list.\nadd ("w" and then 5) to v.\nadd "q" and then 6 to v.\nsay v.\n'
        self.assertEqual(run_text(text), "w 5 and q 6\n")

    def test_item_out_of_range_in_native_code(self):
        a, b = interpreted("set xs to 1 and 2.\nset item 3 of xs to 1.\n"), native("set xs to 1 and 2.\nset item 3 of xs to 1.\n")
        self.assertEqual(a, b)
        self.assertEqual(a[1], "prog.eng, line 2:\nThere is no item 3: the list has 2 items.\n")


PLAIN_RS = os.path.join(ROOT, "compiler-rs", "target", "release", "plain")

BAD_PROGRAMS = [
    'set x to 1, "two" and 3.\n',
    "stop the loop.\n",
    'a book has a title.\nset b to a new book with title "x".\nset the title of b to 5.\n',
    "a book has a title (colour).\n",
    "say 1 plus 2 times 3.\n",
    "pritn 5.\n",
    "set x to 5.\nset x to \"five\".\n",
    "say it.\n",
    "to greet someone, say \"hi\" and then someone.\ngreet.\n",
    "to find the area of a width and a height, say 1.\n",
    "if x is more than, say 1.\n",
    "say \"unclosed.\n",
    "say 5 @ 3.\n",
    "that's all.\n",
    "otherwise say 1.\n",
    'reply "hi".\n',
    "set t to an empty table.\nsay the length of 5.\n",
    "set l to 1 and 2.\nsort l by.\n",
    "give back 5.\n",
    'use "nowhere.eng".\n',
]


@unittest.skipUnless(shutil.which("cargo"), "the Rust compiler needs cargo")
class RustCompiler(unittest.TestCase):
    """compiler-rs (the plain command) gives the same Core IR, LLVM IR, errors and programs as plainc."""

    @classmethod
    def setUpClass(cls):
        subprocess.run(["cargo", "build", "--release", "--quiet"], cwd=os.path.join(ROOT, "compiler-rs"), check=True)

    def both(self, cmd, path, cwd):
        a = subprocess.run([sys.executable, os.path.join(ROOT, "engc.py"), cmd, path], capture_output=True, text=True, cwd=cwd)
        b = subprocess.run([PLAIN_RS, cmd, path], capture_output=True, text=True, cwd=cwd)
        return (a.stdout, a.stderr, a.returncode), (b.stdout, b.stderr, b.returncode)

    def test_same_core_and_ir(self):
        paths = sorted(glob.glob(os.path.join(EX, "**", "*.eng"), recursive=True) +
                       glob.glob(os.path.join(ROOT, "bench", "*.eng")))
        self.assertGreater(len(paths), 30)
        for path in paths:
            for cmd in ("core", "ir"):
                with self.subTest(program=os.path.relpath(path, ROOT), cmd=cmd):
                    a, b = self.both(cmd, os.path.basename(path), os.path.dirname(path))
                    self.assertEqual(a, b)

    def test_same_errors(self):
        for k, text in enumerate(BAD_PROGRAMS):
            path = os.path.join(BUILD, f"bad{k}.eng")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            with self.subTest(program=text):
                a, b = self.both("core", path, BUILD)
                self.assertEqual(a, b)
                self.assertEqual(b[2], 1)

    def test_examples_run(self):
        for expected_path in sorted(glob.glob(os.path.join(EX, "*.expected")) + glob.glob(os.path.join(EX, "*", "*.expected"))):
            program = expected_path[:-len(".expected")] + ".eng"
            folder = os.path.dirname(program)
            inp_path = expected_path[:-len(".expected")] + ".input"
            inp = open(inp_path).read() if os.path.exists(inp_path) else ""
            with open(expected_path, encoding="utf-8") as f:
                expected = f.read()
            with self.subTest(program=os.path.relpath(program, EX)):
                exe = os.path.join(BUILD, "rs-" + os.path.basename(program)[:-4])
                subprocess.run([PLAIN_RS, "build", program, "-o", exe], check=True, capture_output=True)
                p = subprocess.run([exe], input=inp, capture_output=True, text=True, cwd=folder)
                self.assertEqual(p.stdout, expected)

    def test_run_and_check(self):
        path = os.path.join(BUILD, "args.eng")
        with open(path, "w") as f:
            f.write('say "got" and then the number of items in the arguments.\nfail with "stop".\n')
        p = subprocess.run([PLAIN_RS, "run", path, "a", "b"], capture_output=True, text=True)
        self.assertEqual((p.stdout, p.stderr, p.returncode), ("got 2\n", "args.eng, line 2:\nstop\n", 1))
        p = subprocess.run([PLAIN_RS, "check", path], capture_output=True, text=True)
        self.assertEqual((p.stdout, p.returncode), ("args.eng: no problems found.\n", 0))


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

    def test_notes_bot(self):
        backends = self.BACKENDS + (("rust",) if shutil.which("cargo") else ())
        if "rust" in backends:
            subprocess.run(["cargo", "build", "--release", "--quiet"], cwd=os.path.join(ROOT, "compiler-rs"), check=True)
        for b in backends:
            work = tempfile.mkdtemp(prefix=f"plain-notes-{b}-")
            first = bot_replies("notes_bot.eng", say("buy milk", "call mom", "/find milk", "/export"), b, cwd=work)
            self.assertEqual([r[0] for r in first], [
                "Saved as note 1. Everyone saved 1 notes so far.",
                "Saved as note 2. Everyone saved 2 notes so far.",
                "buy milk",
                '[{"text": "buy milk", "number": 1}, {"text": "call mom", "number": 2}]'], b)
            second = bot_replies("notes_bot.eng", say("/list", "/clear", "/list", "/find tea"), b, cwd=work)
            self.assertEqual([r[0] for r in second], [
                "1. buy milk\n2. call mom", "All your notes are gone.", "No notes yet.", "Nothing about tea."], b)

    def test_coffee_rules(self):
        for b in self.BACKENDS:
            work = tempfile.mkdtemp(prefix=f"plain-coffee-{b}-")
            got = bot_replies("coffee_rules.eng", say("latte", "croissant", "/pay", "cappuccino", "cappuccino",
                                                      "cappuccino", "cappuccino", "/pay", "/refund", "/price latte 1"),
                              b, cwd=work)
            self.assertEqual([r[0] for r in got][2], "To pay: 2400 tenge, discount 0 tenge.", b)
            self.assertEqual([r[0] for r in got][7:], ["To pay: 5040 tenge, discount 560 tenge.", "Refunded 5040 tenge.",
                                                       "Only the owner can change prices."], b)

    def test_bot_words_outside_a_rule(self):
        with self.assertRaises(EngError):
            engc.compile_source('reply "hi".')
        with self.assertRaises(EngError):
            engc.compile_source("set their score to 1.")


if __name__ == "__main__":
    unittest.main()
