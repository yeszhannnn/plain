# Plain: компилируемый язык, в котором программа пишется по-английски

Расширение файлов `.eng`, компилятор `engc`. Редакция словаря: 2026.

## Часть 1. Язык глазами человека

Это всё, что нужно знать тому, кто пишет программы. Остальные части документа описывают внутренности компилятора, их пользователь никогда не видит.

```text
say hello five times.

set age to 18.
the user's score is 100.
add 5 to the user's score.
say "your score is" and then the user's score.

if the user's score is more than 50, say "well done".
otherwise say "try again".

do the following three times.
  say "still here".
  add one to the counter.
that's all.

keep doubling the number until it is bigger than 100.
say "yay" twice, but only if the user is happy.
```

Правила:

- Одно предложение делает одно действие. Предложение заканчивается точкой или переводом строки.
- Регистр букв не важен: `Say`, `say` и `SAY` значат одно и то же.
- Группа действий начинается словами `do the following …` и заканчивается `that's all`, `done` или пустой строкой. Отступы можно ставить для красоты, компилятор на них не смотрит.
- Текст из нескольких слов берётся в кавычки, как прямая речь. Одно слово можно без кавычек: если такого имени ещё нет, это просто текст (`say hello` печатает `hello`).
- Числа можно писать цифрами или словами: `five`, `twenty-one`, `one hundred and five`, `twice`.
- Типов нет. Имя получает значение при первом `set …`, `… is …` или `add … to …` (счётчик начинается с нуля). Одно имя хранит один вид значения: если `age` было числом, текстом оно уже не станет, и компилятор скажет об этом.
- `it` значит последнее имя, которому что-то присвоили.
- Слова `a`, `an`, `the` компилятор пропускает, поэтому назвать переменную `a` нельзя.
- Арифметику можно писать словами (`plus`, `minus`, `times`, `divided by`) или знаками (`+ - * /`). Знаки подчиняются школьным правилам, слова читаются как речь (подробнее в части 4).

Ошибки компилятор пишет простым английским:

```text
I don't know the word "prnit". Did you mean "print"?

This sentence can mean 2 different things:
  1) say 1 plus (2 times 3)
  2) say (1 plus 2) times 3
Please add brackets or rewrite it so that only one reading is possible.

"age" was a number on line 1, and here it would become text. A name keeps one kind of value.
```

### Позиция проекта

Свободный английский как язык программирования не работает, потому что в нём есть неустранимая двусмысленность. Работает контролируемый естественный язык: закрытый словарь, фиксированные шаблоны фраз, записанные правила, к чему относится каждое слово. Самый удачный существующий пример такого подхода Inform 7: он компилирует английскую прозу в исполняемые игры с 2006 года. Близкие предшественники: Attempto Controlled English, AppleScript, HyperTalk, COBOL.

Plain берёт у них идею, но отличается в одном пункте. Неоднозначная фраза никогда не исполняется по догадке: компилятор находит все прочтения и, если их больше одного, останавливается и показывает их человеку. Ощущение «пишу как говорю» дают широкая карта синонимов и свободный порядок слов, а детерминизм сохраняется тем, что у каждой принятой фразы ровно одно значение.

---

## Часть 1.5. Telegram-боты

Если в программе есть хоть одно правило `when someone sends …`, она становится ботом. Пример целиком (`examples/bots/guess.eng`):

```text
when someone sends "/start"
  set their secret to a random number from 1 to 100.
  set their tries to 0.
  reply "I picked a number from 1 to 100. Guess it!".
that's all.

when someone sends a number
  add 1 to their tries.
  if the number they sent is more than their secret, reply "Lower.".
  otherwise, if the number they sent is less than their secret, reply "Higher.".
  otherwise
    reply "Yes! You needed" and then their tries and then "tries. I picked a new number.".
    set their secret to a random number from 1 to 100.
    set their tries to 0.
  that's all.
that's all.

when someone sends anything else, reply "Send me a number, or /start to begin.".
```

Что есть в языке для ботов:

| Фраза | Что делает |
|---|---|
| `when someone sends "/start", …` | правило для конкретного сообщения; регистр и пробелы по краям не важны |
| `when someone sends "hi", "hello" or "hey"` | любое из нескольких сообщений |
| `when someone sends a number` | сообщение, которое целиком является числом |
| `when someone sends a message that contains "price"` | сообщение содержит текст (также `starts with`) |
| `when someone sends anything else` | всё остальное |
| `reply "text" and then their name` | ответить тому, кто написал |
| `reply "Pick one" with buttons "yes", "no" and "maybe"` | ответ с кнопками; нажатие кнопки приходит как обычное сообщение с её текстом |
| `the message`, `their name`, `the number they sent` | текст сообщения, имя отправителя, сообщение как число |
| `their score`, `their tries` | память о каждом пользователе отдельно |
| `set total to 0` (без `their`) | общая память для всех пользователей |
| `one of "a", "b" and "c"` | случайный выбор |
| `a random number from 1 to 6` | случайное число |
| `the remainder of n divided by 3`, `n mod 3` | остаток от деления |

Правила проверяются сверху вниз, срабатывает первое подходящее. Предложения вне правил (`set total to 0.`) выполняются один раз при запуске. `reply`, `the message` и `their …` вне правила дают ошибку компиляции, потому что там нет сообщения, на которое можно ответить.

Запуск:

```bash
export TELEGRAM_BOT_TOKEN=123456:ABC...      # токен от @BotFather
python3 engc.py run examples/bots/guess.eng  # интерпретатор
python3 engc.py build examples/bots/guess.eng -o guess && ./guess   # нативный бинарник, нужен libcurl
```

Ограничения текущей версии:
- память (`their …` и общие переменные) живёт, пока работает процесс; после перезапуска бот всё забывает;
- бот понимает только текстовые сообщения, фото и стикеры пропускаются;
- сравнение без учёта регистра работает для латиницы; для кириллицы `"Кофе"` и `"кофе"` пока разные слова;
- пустая строка внутри правила закрывает его, как и `that's all`.

Под капотом бинарник содержит рантайм `runtime/plain_rt.c`: long polling через `getUpdates`, отправку через `sendMessage`, небольшой разборщик JSON и таблицу пользователей. Ни Python, ни внешних сервисов, кроме самого Telegram, ему не нужно. Для тестов есть фейковый сервер Telegram (`tests/mock_telegram.py`), адрес API подменяется переменной `TELEGRAM_API_URL`.

---

## Часть 2. Грамматика и карта синонимов (внутри компилятора)

### 2.1 Стадии фронтенда

```
текст
 → разбиение на предложения (точка, двоеточие, перевод строки; кавычки не режутся)
 → токены (слова, числа, текст в кавычках, знаки)
 → нормализация
 → словарь: синонимы превращаются в канонические ключевые слова
 → парсер, который возвращает ВСЕ разборы
 → проверка однозначности
 → Core IR
```

Нормализация чисто механическая и не зависит от контекста:

| Что | Пример |
|---|---|
| сокращения | `that's` → `that is`, `it's` → `it is`, `isn't` → `is not` |
| числительные | `five` → 5, `twenty-one` → 21, `one hundred and five` → 105, `twice` → `2 times` |
| притяжательные | `the user's score` → одно имя `user.score` |
| артикли и вежливые слова | `the`, `a`, `an`, `please`, `just` удаляются |
| слова-ярлыки | `the word 'Hello'`, `the number 5` → `'Hello'`, `5` (только если за ярлыком стоит значение) |

### 2.2 Карта синонимов

Хранится в `lexicon.json`, это данные, а не код. Словарь заморожен внутри редакции: программа, которая компилируется сегодня, через пять лет даст то же дерево. Новые синонимы попадают только в следующую редакцию, как `edition` в Rust.

| Ключевое слово | Формы в тексте |
|---|---|
| `PRINT` | print, show, say, display, output, write, tell, echo, announce (+ формы на -s/-ing) |
| `REPEAT` | repeat, do the following, repeat the following, do this |
| `SET` / `TO` | set, let, make / to, be |
| `IS` | is, are, equals, is equal to, will be, becomes |
| `ADD` `SUB` `INC` `DEC` | add … to, subtract … from, increase … by, decrease … by |
| `DOUBLE` `HALVE` | double, doubling, halve, halving |
| `KEEP` + `UNTIL`/`WHILE` | keep … until …, keep … while/as long as … |
| `IF` | if, when, only if, but only if, but if, provided that |
| `OTHERWISE` | otherwise, else, or else |
| `GT` `LT` `GE` `LE` `NE` | is more/greater/bigger/larger/higher than, is above, exceeds; is less/smaller/lower than, is below; is at least; is at most; is not |
| `PLUS` `MINUS` `MUL` `DIV` | plus, minus, times / multiplied by, divided by, а также `+ - * /` |
| `CONCAT` | and then, followed by |
| `END` | that's all, that's it, done, end |

Сопоставление идёт по самой длинной фразе: `is greater than or equal to` распознаётся целиком раньше, чем `is greater than`, а `and then` раньше, чем `and`.

### 2.3 Грамматика

Записана над каноническими ключевыми словами. Она намеренно неоднозначна, поэтому парсер обязан уметь вернуть все разборы. LALR-генераторы (Bison, lalrpop) не подходят: они разрешают конфликты молча, а для Plain поиск таких конфликтов и есть главная работа фронтенда.

```ebnf
sentence   = simple [ guard ]
           | IF cond ( "," | THEN ) sentence          (* if x, say hi *)
           | IF cond                                   (* открывает группу *)
           | OTHERWISE [ sentence ]
           | REPEAT expr TIMES [ sentence ]            (* без тела открывает группу *)
           | KEEP simple ( UNTIL | WHILE ) cond
           | END ;
guard      = [ "," ] IF cond ;                         (* хвостовое "but only if" *)

simple     = PRINT args [ expr TIMES ]                 (* say hi 5 times *)
           | SET name TO expr
           | name IS expr                              (* the user's score is 100 *)
           | ADD expr TO name | SUB expr FROM name
           | INC name [ BY expr ] | DEC name [ BY expr ]
           | DOUBLE name | HALVE name ;
args       = expr { CONCAT expr } ;

cond       = cond OR cond | cond AND cond | NOT cond | expr cmp expr ;
expr       = expr op expr | NUM | STR | name | IT | "(" expr ")" ;
op         = PLUS | MINUS | MUL | TIMES | DIV ;
```

`expr = expr op expr` записано без приоритетов специально. Приоритеты применяются фильтром после разбора (раздел 4.1), и только там, где их подразумевает сам текст.

### 2.4 Три фразы, одно дерево

```
"Print the word 'Hello' 5 times"
   нормализация → print 'Hello' 5 times
   токены       → PRINT STR(Hello) NUM(5) TIMES
   правило      → simple = PRINT args expr TIMES

"Repeat 5 times: show 'Hello'"
   двоеточие делит на заголовок и тело на той же строке
   токены       → REPEAT NUM(5) TIMES | PRINT STR(Hello)
   правило      → sentence = REPEAT expr TIMES, тело = одно предложение

"Say 'Hello' five times"
   нормализация → say 'Hello' 5 times
   токены       → PRINT STR(Hello) NUM(5) TIMES
```

После десахаризации (хвостовое повторение становится внешним циклом) все три дают:

```
Repeat(Int(5))
  Print(Text("Hello"))
```

Прототип это проверяет: `engc.py core` на четырёх файлах `examples/hello_*.eng` выводит одно и то же.

### 2.5 Core IR

Маленький и не знает ничего о естественном языке:

```
Assign(name, expr)      Update(name, op, expr)     Print(parts...)
Repeat(count) body      While(cond) body           If(cond) then [Else else]
Int  Text  Var  Bin(op,l,r)  Cmp(op,l,r)  And/Or/Not
```

### 2.6 Переменные без типов

- `set age to 18` → `Assign(age, Int(18))`.
- `the user's score is 100` → `Assign(user.score, Int(100))`. В полной версии `user` становится записью с полем `score`, форма записи выводится из всех упоминаний в программе. В прототипе `user.score` хранится как отдельное имя.
- `is` в начале предложения означает присваивание, а после `if`, `until`, `when` означает сравнение. Это правило по позиции слова, а не догадка.

Вывод типов работает унификацией: литерал `18` даёт число, `"…"` даёт текст, сравнение даёт да/нет. Каждое имя после проверки имеет один машинный тип (`i64`, указатель на строку, `i1`), поэтому сгенерированный код не проверяет типы во время выполнения и работает со скоростью C. Неявных приведений нет.

### 2.7 Условие "but only if the user is happy"

Хвостовое условие относится ко всему предложению:

```
say "yay" twice, but only if the user is happy.
→ If(Cmp(==, Var(user), Text("happy")))
    Repeat(Int(2))
      Print(Text("yay"))
```

В прототипе `the user is happy.` присваивает имени `user` текст `happy`, а `if the user is happy` сравнивает с ним. В полной версии добавляются определяемые прилагательные: `a user is happy if their mood is above 5.` компилируется в функцию `happy(user)`, и тогда условие вызывает её. Этот механизм взят из Inform 7.

### 2.8 Циклы без for и while

| Фраза | Core IR | В прототипе |
|---|---|---|
| `say hi 3 times` | `Repeat(3)` | да |
| `do the following 3 times. … that's all.` | `Repeat(3)` с группой | да |
| `keep doubling x until it exceeds 100` | `While(Not(x > 100))` | да |
| `for every name in the guest list, say name` | `ForEach` | нет, этап M3 |
| `count from 1 to 10 as n, say n` | `ForRange` | нет, этап M3 |

---

## Часть 3. Архитектура компилятора

### 3.1 Конвейер

```
hello.eng
  1. исходник: UTF-8, у каждого предложения номер строки
  2. предложения → токены → нормализация → словарь
  3. парсер всех разборов (Earley в Rust-версии)
  4. проверка однозначности: больше одного разного дерева → ошибка
  5. десахаризация → Core IR
  6. имена, "it", вывод типов
  7. Core IR → LLVM IR (переменные через alloca, LLVM сам переводит их в регистры через mem2reg)
  8. LLVM -O2 → объектный файл
  9. линковка с маленьким рантаймом (печать, строки, списки)
  ▼
./hello  (нативный исполняемый файл; в прототипе 16 КБ)
```

Во время выполнения программы нет ни интерпретатора, ни сборщика мусора, ни ИИ. Рантайм полной версии оценивается в 1–2 тысячи строк на C или Rust `no_std`; строки и списки со счётчиком ссылок.

### 3.2 Стек для полной версии

| Слой | Выбор | Причина |
|---|---|---|
| язык компилятора | Rust | алгебраические типы для AST, хорошие биндинги к LLVM |
| лексер | `logos` | быстрый, декларативный |
| парсер | свой Earley с SPPF или крейт `earlgrey` | нужен полный лес разборов, чтобы находить неоднозначность |
| словарь | TOML/JSON + `serde`, вшивается через `include_str!` | детерминизм и редакции |
| диагностики | `ariadne` или `miette` | подсветка фразы и варианты переписывания |
| типы | своя унификация, `ena` для union-find | |
| кодогенерация | `inkwell` | обёртка над LLVM C API |
| позже | `salsa`, `tower-lsp`, форматтер `engfmt` | редактор, подсказки, единый стиль в команде |

Flex/Bison отклонены по той же причине, что и LALR: они требуют однозначной грамматики.

### 3.3 Прототип

`engc.py`, один файл, только стандартная библиотека Python. Python выбран для проверки грамматики и словаря, а не ради скорости компиляции. Нативный бинарник при этом настоящий: прототип пишет LLVM IR текстом и собирает его установленным `clang`.

Парсер прототипа написан в стиле list-of-successes: каждая функция возвращает список всех удачных разборов. Для этой грамматики результат совпадает с Earley (все деревья), но экспоненциален в худшем случае; в Rust-версии его заменит Earley с общим лесом разборов, который работает за O(n³).

```bash
python3 engc.py run   examples/program.eng           # выполнить
python3 engc.py build examples/hello_print.eng -o hello && ./hello
python3 engc.py ir    examples/hello_print.eng       # LLVM IR   (--emit-llvm)
python3 engc.py c     examples/hello_print.eng       # C         (--emit-c)
python3 engc.py core  examples/hello_say.eng         # Core IR   (--dump-core)
python3 -m unittest discover tests
```

LLVM IR, который прототип выдаёт для `Print the word 'Hello' 5 times`:

```llvm
@.s1 = private unnamed_addr constant [6 x i8] c"Hello\00"
@.s2 = private unnamed_addr constant [4 x i8] c"%s\0A\00"
declare i32 @printf(ptr, ...)

define i32 @main() {
entry:
  %rep1 = alloca i64
  store i64 0, ptr %rep1
  br label %repeat.cond1
repeat.cond1:
  %t1 = load i64, ptr %rep1
  %t2 = icmp slt i64 %t1, 5
  br i1 %t2, label %repeat.body2, label %repeat.end3
repeat.body2:
  %t3 = call i32 (ptr, ...) @printf(ptr @.s2, ptr @.s1)
  %t4 = load i64, ptr %rep1
  %t5 = add i64 %t4, 1
  store i64 %t5, ptr %rep1
  br label %repeat.cond1
repeat.end3:
  ret i32 0
}
```

---

## Часть 4. Проблемы и решения

### 4.1 Двусмысленность

Принцип один: неоднозначность находится механически, по числу разборов, и неоднозначная фраза не компилируется. Компилятор предлагает варианты, человек выбирает. Угадывать намерение компилятору запрещено, потому что угаданная программа однажды сделает не то, что имелось в виду, и никто этого не заметит.

| Проблема | Пример | Решение |
|---|---|---|
| приоритет операций в речи | `say 1 plus 2 times 3` | В устной речи у `plus` и `times` нет приоритета, слушатель понимает фразу двумя способами. Поэтому словесные операторы разной силы не получают приоритета, оба дерева доживают до проверки, и компилятор просит скобки. Знаки (`1 + 2 * 3`) читаются по школьным правилам и дают 7. Операторы одной силы (`10 minus 3 minus 2`) читаются слева направо. |
| две роли слова `times` | `say 2 times 3 4 times` | `times` между двумя значениями означает умножение, `times` в конце предложения после числа означает повторение. Фраза печатает 6 четыре раза. Если после `times` нет значения и это не конец предложения, разбора нет и выдаётся ошибка. |
| к чему относится условие | `say hi 3 times, but only if x is 2` | Хвостовое условие всегда относится ко всему предложению. Правило записано в спецификации, а не выведено из контекста. |
| `is`: присваивание или сравнение | `the score is 100` и `if the score is 100` | По позиции: в начале предложения присваивание, после `if`/`until`/`when` сравнение. |
| местоимения | `set x to 4. double it.` | `it` означает последнее имя, которому что-то присвоили. В редакторе (этап M4) подсказка показывает, во что превратилось `it`. |
| опечатки | `prnit hello` | Ошибка с подсказкой по похожести слова. Автоисправления нет: исправление без спроса меняет смысл программы. |
| смысл слов меняется со временем | новый синоним в новой версии | Словарь заморожен внутри редакции, редакция указана в проекте. |
| `and`/`or` вместе | `if p is 1 and q is 2 or r is 3` | `and` сильнее `or`, как в логике. В полной версии линтер предупреждает о смешении без скобок. |

### 4.2 Многословие

Тут дешёвых решений хватает:

| Проблема | Решение |
|---|---|
| формулы словами длинные | Знаки разрешены прямо в предложении: `set area to width * height`. Словесная форма остаётся для тех, кому так понятнее, обе дают одно дерево. |
| сложные выражения | Скобки разрешены как в обычном тексте: `say (1 plus 2) times 3`. |
| повторяющиеся куски | Пользовательские глаголы и прилагательные (этап M3): `to greet someone, say "hi" and then their name.`, дальше просто `greet the user.` |
| длинные ссылки на данные | `it`, притяжательные (`the user's score`), позже `its`/`their`. |
| скорость набора | Подсказки фраз в редакторе и форматтер `engfmt`, который приводит любые синонимы к одной форме, чтобы код в команде читался одинаково. |

Из матрицы видно, где основной объём работы. Многословие закрывается синтаксическим сахаром за пару недель. Двусмысленность требует архитектуры: парсер полного леса разборов, правила фильтрации и понятные сообщения с вариантами переписывания. На это уйдёт большая часть усилий по фронтенду.

---

## Часть 5. Дорожная карта Rust-версии

| Этап | Содержание | Готово, когда |
|---|---|---|
| M1 | фронтенд на Rust: словарь, нормализация, Earley, проверка однозначности, интерпретатор Core IR | все тесты прототипа проходят на Rust-версии |
| M2 | кодогенерация через `inkwell`, рантайм для строк, `engc build` | бинарники совпадают по выводу с интерпретатором |
| M3 | списки, `for every`, `count from … to`, записи, пользовательские глаголы и прилагательные, функции | можно написать FizzBuzz и сортировку списка без единого знака препинания, кроме точки |
| M4 | LSP, `engfmt`, подсказки фраз, показ разрешения `it` при наведении | редактор подсвечивает неоднозначность до компиляции |

Что прототип пока не умеет: списки и `for every`, записи с полями, дробные числа, пользовательские глаголы, сохранение памяти бота на диск, inline-кнопки под сообщением и рассылку всем пользователям. Всё это относится к M3.
