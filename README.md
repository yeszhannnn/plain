# Plain

Язык программирования, на котором код пишется обычными английскими предложениями. Компилятор `engc` переводит такой текст через LLVM в нативный исполняемый файл. В языке есть списки, таблицы, свои функции, рекурсия, работа с текстом и файлами, ввод с клавиатуры, модули и Telegram-боты с памятью на диске.

```text
to find the factorial of a number
  if the number is at most 1, give back 1.
  give back the number times the factorial of (the number minus 1).
that's all.

the scores of "Ana" are 95, 88 and 92.
the scores of "Bob" are 72, 65 and 80.

for every student in the scores
  set average to (the average of the scores of student) rounded.
  say student and then ":" and then average and then "points".
that's all.

say "10! is" and then the factorial of 10.
```

Ни одного объявления типа, ни одной скобки вокруг блоков. Типы компилятор выводит сам, а если фразу можно понять двумя способами, он не угадывает, а просит её уточнить.

Документация:
- [SPEC.md](SPEC.md): спецификация языка, все конструкции и правила;
- [BLUEPRINT.md](BLUEPRINT.md): устройство компилятора и план развития;
- [bench/README.md](bench/README.md): замер скорости против Python и C.

## Что нужно установить

| Задача | Нужно |
|---|---|
| запускать программы и ботов (`engc.py run`) | Python 3 (проверено на 3.13) |
| собирать нативные программы (`engc.py build`) | clang 15+ и Boehm GC (`libgc`) |
| собирать нативных ботов | ещё libcurl с заголовками |
| ускорение сборки через LTO (необязательно) | lld |

```bash
# Ubuntu / Debian
sudo apt install clang lld libgc-dev libcurl4-openssl-dev

# macOS: clang и libcurl ставятся с инструментами Xcode, сборщик мусора — через Homebrew
xcode-select --install
brew install bdw-gc
```

На Windows команда `run` работает как есть, а для `build` используйте WSL.

## Быстрый старт

```bash
git clone https://github.com/yeszhannnn/plain
cd plain
echo 'say Hello five times.' > hello.eng
python3 engc.py run hello.eng               # выполнить сразу
python3 engc.py build hello.eng -o hello    # собрать исполняемый файл
./hello
```

## Коротко о языке

```text
Note: comments start with "Note:" or "#".

set price to 9.99.
the guests are "Ana", "Bob" and "Dana".
add "Eve" to the guests.
for every guest in the guests, say "Welcome," and then the guest and then "!".
for every n from 1 to 5, say n squared.

the menu of "coffee" is 900.
if the menu of "tea" is unknown, say "no tea".

to greet someone, say "Hi" and then someone.
greet "Ana".

ask "How old are you?" and remember the answer as age.
say "Next year you will be" and then age plus 1.

read the file "notes.txt" into notes.
for every line in the lines of notes, say line in uppercase.

set n to 100.
as long as n is more than 1
  halve n.
  if n is 25, stop the loop.
that's all.
```

Полный список конструкций и правил — в [SPEC.md](SPEC.md).

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
   python3 engc.py run bot.eng
   ```

У каждого пользователя свой список (`their tasks`), а `remember everything in` сохраняет память на диск, так что после перезапуска бот ничего не забывает.

Не храните токен в `.eng`-файлах и не коммитьте его: он передаётся только через переменную окружения.

## Бот на сервере

На сервере с Ubuntu или Debian один скрипт собирает бота, создаёт пользователя `plain`, спрашивает токен и запускает бота как systemd-сервис, который сам поднимается после сбоя и перезагрузки:

```bash
git clone https://github.com/yeszhannnn/plain && cd plain
sudo deploy/install.sh examples/bots/showcase.eng
journalctl -u plain-bot -f          # логи
sudo systemctl restart plain-bot    # перезапуск после обновления
```

Бот живёт в `/opt/plain-bot`, токен лежит в `/opt/plain-bot/bot.env` с правами `600`, файл памяти пишется туда же. Описание сервиса — `deploy/plain-bot.service`.

## Примеры

| Файл | Что показывает |
|---|---|
| `examples/program.eng` | переменные, условия, повторы |
| `examples/grades.eng` | таблица списков, своя функция, сортировка |
| `examples/primes.eng` | решето Эратосфена на списках |
| `examples/wordcount.eng` | чтение файла, подсчёт слов в таблице |
| `examples/quiz.eng` | вопросы и ответы с клавиатуры |
| `examples/todo.eng` | список дел в файле, команды через аргументы |
| `examples/modules/main.eng` | подключение другого файла через `use` |
| `examples/fizzbuzz.eng`, `collatz.eng`, `table.eng`, `interest.eng` | небольшие расчёты |
| `examples/bots/echo.eng`, `double.eng`, `counter.eng` | простые боты |
| `examples/bots/guess.eng`, `magic8.eng`, `shop.eng` | игра, случайные ответы, меню на кнопках |
| `examples/bots/todo_bot.eng` | список дел для каждого пользователя с памятью на диске |
| `examples/bots/showcase.eng` | витрина: все программы в одном боте с меню, [@plainlangbot](https://t.me/plainlangbot) |

## Команды компилятора

```bash
python3 engc.py run   FILE [ARGS...]    # выполнить
python3 engc.py build FILE -o OUT       # собрать исполняемый файл
python3 engc.py core  FILE              # внутреннее дерево программы
python3 engc.py ir    FILE              # LLVM IR
```

## Скорость

На решете Эратосфена до 5 000 000 собранная программа на Plain примерно в 9 раз быстрее того же алгоритма на Python и примерно в 6 раз медленнее C. Подробности и как повторить замер — в [bench/README.md](bench/README.md).

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

Тесты запускают каждый пример и интерпретатором, и собранным бинарником и сравнивают вывод с эталоном. Боты разговаривают с фейковым сервером Telegram (`tests/mock_telegram.py`), настоящий токен не нужен. Для тестов сборки нужны clang, libgc и libcurl.
