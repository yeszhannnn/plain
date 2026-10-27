# Plain

Язык программирования, на котором код пишется обычными английскими предложениями. Компилятор `engc` переводит такой текст через LLVM в нативный исполняемый файл. На Plain можно писать Telegram-ботов и простые консольные программы.

```text
when someone sends "/start", reply "Hi" and then their name and then "! Send me a number.".
when someone sends a number, reply "Twice that is" and then the number they sent times 2.
when someone sends anything else, reply "I only understand numbers for now.".
```

Это бот целиком. Собранный бинарник весит около 30 КБ и запускается без Python.

Документация:
- [SPEC.md](SPEC.md): спецификация языка, все конструкции и правила;
- [BLUEPRINT.md](BLUEPRINT.md): архитектура компилятора и план развития.

## Что нужно установить

| Задача | Нужно |
|---|---|
| запускать программы и ботов (`engc.py run`) | Python 3 (проверено на 3.13), больше ничего |
| собирать нативные бинарники (`engc.py build`) | clang 15 или новее |
| собирать нативных ботов | clang и libcurl с заголовками |

Установка clang и libcurl:

```bash
# Ubuntu / Debian
sudo apt install clang libcurl4-openssl-dev

# macOS: clang и libcurl ставятся вместе с инструментами Xcode
xcode-select --install
```

На Windows команда `run` работает как есть, а для `build` используйте WSL.

## Быстрый старт

```bash
git clone https://github.com/yeszhannnn/plain
cd plain
```

Создайте файл `hello.eng`:

```text
say Hello five times.
```

Запустите:

```bash
python3 engc.py run hello.eng
```

Соберите в исполняемый файл и запустите его:

```bash
python3 engc.py build hello.eng -o hello
./hello
```

## Свой Telegram-бот за пять минут

1. В Telegram откройте [@BotFather](https://t.me/BotFather), отправьте `/newbot` и получите токен вида `123456789:AA...`.
2. Создайте файл `bot.eng`:

   ```text
   when someone sends "/start", reply "Hi" and then their name and then "!" with buttons "Hello" and "Help".
   when someone sends "hello", reply "Hello to you too!".
   when someone sends "help", reply "Press a button or write anything.".
   when someone sends anything else, reply "You said:" and then the message.
   ```

3. Запустите:

   ```bash
   export TELEGRAM_BOT_TOKEN=ваш_токен
   python3 engc.py run bot.eng
   ```

4. Напишите боту в Telegram. Остановить бота: Ctrl+C.

Чтобы бот работал постоянно, соберите его и запустите на сервере:

```bash
python3 engc.py build bot.eng -o mybot
TELEGRAM_BOT_TOKEN=ваш_токен ./mybot
```

Не храните токен в `.eng`-файлах и не коммитьте его в репозиторий: он передаётся только через переменную окружения.

## Примеры

| Файл | Что делает |
|---|---|
| `examples/program.eng` | переменные, условия, повторы, цикл «пока» |
| `examples/fizzbuzz.eng` | FizzBuzz |
| `examples/collatz.eng` | гипотеза Коллатца для числа 27 |
| `examples/table.eng` | таблица умножения |
| `examples/interest.eng` | сложный процент |
| `examples/bots/echo.eng` | бот, который повторяет сообщения |
| `examples/bots/double.eng` | бот, который удваивает числа |
| `examples/bots/counter.eng` | счётчик с кнопками и памятью для каждого пользователя |
| `examples/bots/guess.eng` | игра «угадай число» |
| `examples/bots/magic8.eng` | магический шар, случайные ответы |
| `examples/bots/shop.eng` | меню кофейни на кнопках |
| `examples/bots/showcase.eng` | витрина: все программы выше в одном боте с меню, работает в [@plainlangbot](https://t.me/plainlangbot) |

## Команды компилятора

```bash
python3 engc.py run   FILE            # выполнить
python3 engc.py build FILE -o OUT     # собрать исполняемый файл
python3 engc.py core  FILE            # показать внутреннее дерево программы
python3 engc.py ir    FILE            # показать LLVM IR
python3 engc.py c     FILE            # показать эквивалентный код на C
```

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

Боты в тестах разговаривают с фейковым сервером Telegram (`tests/mock_telegram.py`), настоящий токен для тестов не нужен. Тесты сборки требуют clang и libcurl.

## Состояние проекта

Ранняя версия. Чего пока нет:
- памяти бота между перезапусками;
- дробных чисел;
- списков;
- собственных команд и функций.

План развития описан в [BLUEPRINT.md](BLUEPRINT.md).
