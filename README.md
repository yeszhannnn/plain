# Plain

Язык программирования, на котором код пишется обычными английскими предложениями. Компилятор `plain` переводит такой текст через LLVM в нативный исполняемый файл. В языке есть свои типы данных с полями, обобщённые функции, перехват ошибок, списки и таблицы с сортировкой и отбором, HTTP и JSON, файлы, модули и Telegram-боты с памятью на диске.

```text
a book has a title, an author, a year and a rating.

set books to an empty list.
add a new book with title "Dune", author "Frank Herbert", year 1965 and rating 4.5 to books.
add a new book with title "Emma", author "Jane Austen", year 1815 and rating 4.0 to books.
add a new book with title "Beloved", author "Toni Morrison", year 1987 and rating 4.4 to books.

sort books by year.
for every book in books, say the title of book and then "(" and then the year of book and then ")".

set modern to every book in books where the year of book is more than 1950.
say "After 1950:" and then the title of book for every book in modern.
say "Best rated:" and then the title of (the book in books with the highest rating).
```

Ни одного объявления типа и ни одной фигурной скобки. Типы компилятор выводит сам, а если фразу можно понять двумя способами, он не угадывает и просит её уточнить.

Документация:
- [SPEC.md](SPEC.md): спецификация языка, все конструкции и правила;
- [BLUEPRINT.md](BLUEPRINT.md): устройство компилятора и план развития;
- [bench/README.md](bench/README.md): замеры скорости против C и Python.

## Что нужно установить

| Задача | Нужно |
|---|---|
| компилятор `plain` | Rust 1.75+ (`cargo`) |
| собирать и запускать программы | clang 15+, Boehm GC (`libgc`), libcurl с заголовками |
| ускорение через LTO (необязательно) | lld |
| эталонный компилятор и интерпретатор `engc.py` (необязательно) | Python 3 (проверено на 3.13) |

```bash
# Ubuntu / Debian
sudo apt install clang lld libgc-dev libcurl4-openssl-dev
curl https://sh.rustup.rs -sSf | sh

# macOS: clang и libcurl ставятся с инструментами Xcode, сборщик мусора через Homebrew
xcode-select --install
brew install bdw-gc rust
```

Затем сам компилятор:

```bash
git clone https://github.com/yeszhannnn/plain
cd plain
cargo install --path compiler-rs        # команда plain появится в ~/.cargo/bin
```

На Windows используйте WSL.

## Быстрый старт

```bash
echo 'say Hello five times.' > hello.eng
plain run hello.eng                 # собрать во временную папку и запустить
plain build hello.eng -o hello      # собрать исполняемый файл
./hello
```

## Коротко о языке

```text
Note: comments start with "Note:" or "#".

set price to 9.99.
the guests are "Ana", "Bob" and "Dana".
add "Eve" to the guests.
for every guest in the guests, say "Welcome," and then the guest and then "!".
sort the guests from highest to lowest.

the menu of "coffee" is 900.
if the menu of "tea" is unknown, say "no tea".

to greet someone, say "Hi" and then someone.
greet "Ana".

to find the first of some things, give back item 1 of the things.
say the first of (5, 6 and 7) and then the first of ("a" and "b").

try the following.
  read the file "notes.txt" into notes.
if something goes wrong
  say "Could not read notes:" and then the error.
that's all.

a weather has a temperature (decimal) and a windspeed (decimal).
fetch "https://example.com/weather.json" into page.
set now to page as a weather.
say the temperature of now with 1 decimals and then "°C".
```

Длинное предложение можно перенести на следующую строку, если строка кончается запятой или связкой вроде `and`, `with`, `and then`. Полный список конструкций и правил лежит в [SPEC.md](SPEC.md).

## Свой Telegram-бот за пять минут

1. В Telegram откройте [@BotFather](https://t.me/BotFather), отправьте `/newbot` и получите токен вида `123456789:AA...`.
2. Создайте файл `bot.eng`:

   ```text
   remember everything in "bot-memory.json".

   when someone sends "/start", reply "Hi" and then their name and then "! Send me tasks." with buttons "My list".
   when someone sends "My list", reply "Your tasks:" and then their tasks.
   when someone sends anything else
     add the message to their tasks.
     reply "Added. You have" and then the number of items in their tasks and then "tasks.".
   that's all.
   ```

3. Запустите:

   ```bash
   export TELEGRAM_BOT_TOKEN=ваш_токен
   plain run bot.eng
   ```

У каждого пользователя свой список (`their tasks`), а `remember everything in` сохраняет память на диск, так что после перезапуска бот ничего не забывает. Бот посложнее, на своём виде `note` и с выгрузкой в JSON, лежит в `examples/bots/notes_bot.eng`.

Не храните токен в `.eng`-файлах и не коммитьте его: он передаётся только через переменную окружения.

## Бот на сервере

На сервере с Ubuntu или Debian один скрипт собирает бота, создаёт пользователя `plain`, спрашивает токен и запускает бота как systemd-сервис, который сам поднимается после сбоя и перезагрузки:

```bash
git clone https://github.com/yeszhannnn/plain && cd plain
sudo deploy/install.sh examples/bots/showcase.eng
journalctl -u plain-bot -f          # логи
sudo systemctl restart plain-bot    # перезапуск после обновления
```

Скрипт берёт `plain`, если он установлен, ставит его через `cargo`, если есть Rust, и в остальных случаях собирает эталонным `engc.py`. Бот живёт в `/opt/plain-bot`, токен лежит в `/opt/plain-bot/bot.env` с правами `600`, файл памяти пишется туда же.

## Примеры

| Файл | Что показывает |
|---|---|
| `examples/library.eng` | свой вид `book`, сортировка по полю, отбор, лучший по рейтингу |
| `examples/generic.eng` | виды внутри видов, обобщённые функции |
| `examples/errors.eng` | `try`, `fail with`, `the error` |
| `examples/stats.eng` | дробные числа, `with 2 decimals`, выравнивание, график из символов |
| `examples/weather.eng` | HTTP-запрос и JSON прямо в свой вид |
| `examples/grades.eng` | таблица списков, своя функция |
| `examples/wordcount.eng` | чтение файла, подсчёт слов в таблице |
| `examples/quiz.eng` | вопросы и ответы с клавиатуры |
| `examples/todo.eng` | список дел в файле, команды через аргументы |
| `examples/modules/main.eng` | подключение другого файла через `use` |
| `examples/fizzbuzz.eng`, `collatz.eng`, `primes.eng`, `table.eng`, `interest.eng` | небольшие расчёты |
| `examples/bots/echo.eng`, `double.eng`, `counter.eng` | простые боты |
| `examples/bots/guess.eng`, `magic8.eng`, `shop.eng` | игра, случайные ответы, меню на кнопках |
| `examples/bots/todo_bot.eng`, `notes_bot.eng` | память для каждого пользователя, свой вид, JSON |
| `examples/bots/showcase.eng` | витрина: все программы в одном боте с меню, [@plainlangbot](https://t.me/plainlangbot) |

## Команды

```bash
plain run   FILE [ARGS...]      # собрать и запустить
plain build FILE -o OUT         # собрать исполняемый файл
plain check FILE                # только проверить программу
plain core  FILE                # внутреннее дерево программы
plain ir    FILE                # LLVM IR
```

`engc.py` с теми же командами (`run`, `build`, `core`, `ir`) остаётся эталонной реализацией. Его `run` выполняет программу интерпретатором без сборки, это удобно там, где нет clang.

## Скорость

Пять замеров, каждый написан на Plain, C и Python (`bench/run.sh`, лучшее из пяти запусков):

| Замер | C | Plain | Python |
|---|---|---|---|
| решето до 5 000 000 | 0.03 с | 0.12 с | 1.82 с |
| n-body, 5 млн шагов | 0.29 с | 0.29 с | 21.15 с |
| fib(40) рекурсией | 0.28 с | 0.28 с | 11.28 с |
| 5 млн слов в таблице | 0.11 с | 0.19 с | 1.41 с |
| текст из миллиона чисел | 0.05 с | 0.09 с | 0.10 с |

На числах и вызовах функций Plain идёт вровень с C. Решето отстаёт в 4 раза, потому что список чисел в Plain занимает 8 байт на элемент, а C хранит решето в байтах. Подробности в [bench/README.md](bench/README.md).

## Если что-то не компилируется

Компилятор отвечает по-английски и подсказывает, как исправить:

```text
bot.eng, line 3:
I don't know the word "prnit". Did you mean "print"?
```

Если фраза нормально звучит по-английски, а компилятор её не понял, создайте issue с этой фразой. По таким фразам и пополняется словарь.

## Тесты

```bash
python3 -m unittest discover tests
```

Тесты прогоняют каждый пример через интерпретатор, через бинарник от `engc.py` и через бинарник от `plain` и сравнивают вывод с эталоном. Отдельный тест проверяет, что оба компилятора выдают одинаковые дерево программы, LLVM IR и сообщения об ошибках. Боты разговаривают с фейковым сервером Telegram (`tests/mock_telegram.py`), а HTTP проверяется на локальном сервере, так что ни токен, ни интернет не нужны. Для тестов нужны clang, libgc, libcurl и cargo.
