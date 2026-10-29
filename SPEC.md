# Спецификация языка Plain

Редакция словаря: 2026. Документ описывает поведение двух компиляторов из этого репозитория: `plain` (на Rust, папка `compiler-rs/`) и эталонного `engc.py` (на Python, папка `plainc/`). Они выдают одинаковый LLVM IR и одинаковые сообщения об ошибках, это проверяют тесты. Если документ и компиляторы расходятся, это ошибка, о ней нужно сообщить.

## 1. Исходный файл

- Расширение `.eng`, кодировка UTF-8.
- Регистр букв в словах не важен: `Say`, `say` и `SAY` одинаковы. Текст в кавычках сохраняет регистр.
- Отступы ни на что не влияют, их ставят для удобства чтения.
- Комментарий — строка, которая начинается с `Note:` или `#`.
- Пустые строки ни на что не влияют.

### 1.1 Предложения

Программа состоит из предложений. Предложение заканчивается точкой, концом строки или двоеточием, после которого на той же строке идёт тело (`repeat 5 times: show 'Hello'`).

Длинное предложение можно перенести. Следующая строка продолжает предыдущую, если та кончается запятой или одним из слов `and`, `or`, `with`, `of`, `to`, `by`, `and then`, `followed by`, а также если сама строка начинается с `and then` или `followed by`:

```text
say "This sentence is long" and then name and then ", and it goes on"
  and then "on the next line".
add a new book with title "Dune", author "Frank Herbert",
  year 1965 and rating 4.5 to books.
```

Правило механическое: компилятор смотрит только на последнее слово строки и первые слова следующей.

Точка между двумя цифрами — часть дробного числа (`2.5`). Точки, двоеточия и запятые внутри кавычек относятся к тексту.

### 1.2 Текст в кавычках

Текст берётся в двойные `"…"` или одинарные `'…'` кавычки. Типографские кавычки `“ ” ‘ ’` равнозначны обычным. Апостроф между двумя буквами (`don't`, `user's`) считается частью слова. Пустой текст: `""`.

Внутри кавычек работают `\"` (кавычка), `\\` (обратная косая черта), `\n` (перевод строки) и `\t` (табуляция): `say "She said \"hi\""`.

### 1.3 Нормализация

До разбора компилятор механически упрощает каждое предложение. Шаги идут в таком порядке:

1. Сокращения раскрываются: `that's` → `that is`, `it's` → `it is`, `isn't` → `is not`, `what's` → `what is`, `doesn't` → `does not`.
2. Числа словами превращаются в числа (раздел 3.1).
3. Притяжательная форма `X's Y` становится одним именем (`the user's score`).
4. Удаляются артикли `the`, `a`, `an` и слова вежливости `please`, `just`, `kindly`. Поэтому `a`, `an` и `the` нельзя использовать как имена.
5. Слова-ярлыки `word`, `words`, `text`, `number`, `value`, `phrase` удаляются, если за ними идёт текст в кавычках или число (`say the number 5`), а также если они стоят сразу после глагола печати или ответа перед другим словом (`print the word hello`).
6. Синонимы заменяются ключевыми словами по словарю `lexicon.json`. Побеждает самая длинная подходящая фраза: `is greater than or equal to` распознаётся раньше, чем `is greater than`.
7. `their X` становится переменной пользователя бота (раздел 9.4).

## 2. Типы

| Тип | Как появляется | Как печатается |
|---|---|---|
| целое число | `18`, `five`, арифметика с целыми | `18` |
| дробное число | `2.5`, арифметика с дробными, `square root of` | `2.5`, `4.0` |
| текст | `"hello"`, одно незнакомое слово, `the message` | как есть |
| да/нет | условие (`x is 5`) | `true` / `false` |
| список | `1, 2 and 3`, `an empty list` | `1, 2 and 3`; пустой — `nothing` |
| таблица | `the price of "tea" is 5` | `tea: 5, coffee: 9`; пустая — `nothing` |
| вещь своего вида | `a new book with title "Dune"` (раздел 6.4) | `book (title: Dune, year: 1965)` |

Типы не объявляются. Компилятор выводит их из того, как значения используются во всей программе, включая параметры своих команд и элементы списков.

- Целые числа 64-битные. При переполнении значение переходит через границу, как в машинном коде.
- Дробные числа — 64-битные числа с плавающей точкой, печатаются с 12 значащими цифрами.
- Переменная хранит значения одного типа. Если `age` получила целое число, текстом она уже не станет: это ошибка компиляции.
- Единственное автоматическое преобразование: целое число превращается в дробное там, где ожидается дробное. Если переменной хоть раз присвоено дробное значение, она дробная, и целые значения в ней хранятся как дробные (`set total to 0. add 0.5 to total.` даёт `0.5`).
- В списке все элементы одного типа. `1, 2.5 and 3` — список дробных: `1.0, 2.5 and 3.0`.
- Ключи таблицы — текст или целые числа, значения — любого одного типа, в том числе списки.
- Вещи своего вида передаются по ссылке, как объекты в Java и Python. Пустая ссылка пишется `nothing`.

## 3. Значения

### 3.1 Числа

- Цифрами: `0`, `42`, `-7`, `2.5`, `-0.25`. Минус перед числом записывается символом.
- Словами: `zero` … `nineteen`, `twenty` … `ninety`, `hundred`, `thousand` и их сочетания: `twenty-one`, `one hundred and five`, `two thousand`.
- `once`, `twice`, `thrice` означают `1 times`, `2 times`, `3 times`.
- `one` остаётся словом в `one of …` и после `next`, `this`, `that`, `each`, `any` (`skip to the next one`).

### 3.2 Текст

- В кавычках: `"Hello, world"`.
- Одно слово без кавычек: если переменной с таким именем нет, это текст с исходным написанием (`say Hello` печатает `Hello`). Если переменная есть, берётся её значение.
- Текст из нескольких слов обязательно берётся в кавычки.
- Ключевые слова языка (`done`, `end`, `it`, `times`, `message` и другие из `lexicon.json`) без кавычек текстом не считаются.
- `a new line` (также `line break`, `newline`) — текст из одного перевода строки.

### 3.3 Арифметика

| Операция | Словами | Символом |
|---|---|---|
| сложение | `plus` | `+` |
| вычитание | `minus`, `take away` | `-` |
| умножение | `times`, `multiplied by` | `*`, `×` |
| деление | `divided by` | `/` |
| остаток | `mod`, `modulo`, `the remainder of A divided by B` | `%` |
| степень | `to the power of`, `raised to` | `^` |

- Целое на целое делится нацело с округлением к нулю: `7 / 2` = 3. Если хоть одно число дробное, деление обычное: `7.0 / 2` = 3.5.
- Остаток имеет знак делимого: `-7 mod 2` = -1.
- Целое в отрицательной степени — ошибка выполнения: пишите `2.0 to the power of -1`.
- Деление на ноль останавливает программу с ошибкой.
- Круглые скобки группируют выражения.

Приоритет:
- символы подчиняются школьным правилам: `1 + 2 * 3` = 7;
- словесные операции одной силы читаются слева направо: `10 minus 3 minus 2` = 5; степень читается справа налево;
- словесные операции разной силы без скобок дают ошибку неоднозначности: `1 plus 2 times 3` можно понять двумя способами;
- если в паре операций хотя бы одна записана символом, действуют школьные правила.

### 3.4 Числовые функции

| Запись | Результат |
|---|---|
| `the square root of x` | дробное; от отрицательного — ошибка |
| `the absolute value of x` | того же типа |
| `x squared` | того же типа |
| `x rounded` | целое, половина округляется от нуля: `2.5` → 3 |
| `x rounded up`, `x rounded down` | целое |
| `a random number from 1 to 6` | целое, оба конца включены (также `between 1 and 6`) |
| `one of "a", "b" and "c"` | случайный вариант из перечисленных |
| `one of the guests` | случайный элемент списка |
| `a random decimal from 0 to 1` | дробное, от первого числа включительно до второго |
| `pi` | 3.141592653589793 |
| `the sine of x`, `the cosine of x`, `the tangent of x` | дробное, угол в радианах |
| `the logarithm of x` | натуральный логарифм; от нуля и отрицательных — ошибка |
| `x with 2 decimals` | текст с двумя знаками после точки: `3.14159 with 2 decimals` → `3.14` |

### 3.5 Преобразования

| Запись | Результат |
|---|---|
| `x as a number` | целое; текст должен быть целым числом (`" 42 "` можно), дробное округляется к нулю |
| `x as a decimal` | дробное; из текста вида `3`, `-2.5` |
| `x as text` | текст так, как он был бы напечатан |
| `x is a number` | условие: текст похож на целое или дробное число |

### 3.6 Текст

| Запись | Результат |
|---|---|
| `x in uppercase`, `x in lowercase` | смена регистра для латиницы, кириллицы и казахских букв (Ә Ғ Қ Ң Ө Ұ Ү Һ І) |
| `x trimmed` | без пробелов, табуляций и переводов строк по краям |
| `x with "a" replaced by "b"` | все вхождения заменены |
| `the words of x` | список слов (разделитель — пробелы, табуляции, переводы строк) |
| `the lines of x` | список строк; последний перевод строки не даёт пустой строки |
| `the letters of x` | список букв |
| `x split by ","` | список частей, пустые части сохраняются |
| `list joined with ", "` | текст из элементов списка; `joined together` — без разделителя |
| `the number of letters in x` | длина в буквах (не в байтах) |
| `letter 2 of x`, `the last letter of x` | одна буква |
| `letters 2 to 4 of x` | часть текста; выход за края обрезается |
| `the position of "lo" in x` | где начинается `"lo"`, считая буквы с 1; 0, если не найдено |
| `x padded to 10`, `x padded on the left to 10` | текст, дополненный пробелами справа или слева до 10 букв |
| `x repeated 3 times` | текст три раза подряд |
| `the code of "A"`, `the letter with code 1046` | код буквы (65) и буква по коду (Ж) |

Части текста можно склеивать через `and then` или `followed by` (раздел 5.1), в том числе в значении переменной: `set greeting to "Hi" and then name and then "!"`. `add "!" to greeting` приклеивает текст к переменной без пробела.

### 3.7 `it`

`it` означает последнюю переменную, которой выше по тексту что-то присвоили или которую изменили. Значение `it` определяется при компиляции по порядку предложений.

### 3.8 Как читается «of»

`of` связывает только ближайшее значение, а окончания (`rounded`, `in uppercase`, `joined with`, `as a number` и другие) относятся ко всей фразе перед ними:

```text
say the sum of scores rounded.                 → (the sum of scores) rounded
say the words of poem joined with "-".         → (the words of poem) joined with "-"
say the words of (poem in lowercase).          скобки, чтобы окончание относилось к poem
```

То же правило действует для своих функций: последнее значение после `of` — самое короткое. `the factorial of n minus 1` означает `(the factorial of n) minus 1`; чтобы передать `n minus 1`, нужны скобки.

## 4. Переменные

### 4.1 Имена

Имя — одно слово (`age`, `score`, `r3`) или притяжательная пара (`the user's score`). Переменная появляется в том месте текста, где ей впервые что-то присвоили. Каждая переменная стартует с «нуля» своего типа: `0`, `0.0`, пустой текст, `false`, пустой список, пустая таблица.

До первого присваивания одно слово с таким же написанием считается текстом: `say score.` до `set score to …` печатает `score`.

### 4.2 Присваивание

```text
set age to 18.
let age be 18.
age is 18.
the guests are "Ana", "Bob" and "Dana".
set item 2 of the guests to "Zed".
the price of "coffee" is 900.
set ready to age is 18.                  да/нет можно хранить в переменной
```

Форма `X is …` работает, только если имя стоит в начале предложения. После `if`, `until`, `when` слово `is` означает сравнение.

### 4.3 Изменение значений

```text
add 5 to score.            subtract 2 from score.
increase score by 5.       decrease score by 2.
increase score.            decrease score.             на 1
double score.              halve score.                halve у целых делит нацело
add "Eve" to the guests.   добавить в конец списка
add "!" to greeting.       приклеить текст
add 1 to the count of "apples".
```

`add X to Y` выбирает действие по типу `Y`: число — сложение, список — добавление элемента, текст — приклеивание. Если `Y` ещё не существует, то при числовом `X` появляется счётчик с нулём, а при любом другом — новый список.

Запись таблицы, которой ещё нет, при `add`, `increase`, `decrease`, `double` и `halve` начинается с нуля своего типа: `add 1 to the count of word` считает слова без подготовки.

## 5. Команды

### 5.1 Печать

```text
say "Hello".
print the word 'Hello' 5 times.
show "score is" and then score.
say the guests.                          → Ana, Bob and Dana
```

Глаголы: `print`, `show`, `say`, `display`, `output`, `write`, `tell`, `echo`, `announce` и их формы на `-s` и `-ing`.

Части соединяются через `and then` или `followed by` и печатаются в одну строку. Между частями ставится один пробел, кроме трёх случаев: предыдущая часть заканчивается пробелом или переводом строки; следующая часть начинается с пробела или перевода строки; следующая часть начинается со знака `. , ! ? ; : )`.

`N times` в конце предложения повторяет печать N раз. `times` между двумя значениями означает умножение.

### 5.2 Группы

Группа — несколько предложений, которые относятся к одной команде. Группу открывают:
- `do the following N times`, `repeat N times`, `repeat the following N times`;
- `do the following until <условие>`, `keep doing the following while <условие>`;
- `as long as <условие>`, `while <условие>`;
- `for every x in …`, `for every n from A to B`;
- `if <условие>` и `otherwise, if <условие>` без запятой;
- `otherwise` без продолжения;
- `to …` без запятой (своя команда, раздел 6);
- `when someone sends …` без запятой (раздел 9).

Группу закрывают `that's all`, `that's it`, `done` или `end`. Группы своих команд и правил бота закрываются ещё и автоматически, когда начинается следующее определение `to …` или правило `when someone sends …`, а также в конце файла.

Если после заголовка стоит двоеточие и текст на той же строке, тело — это одно предложение.

### 5.3 Повторение и циклы

```text
say hi 3 times.
repeat 3 times: say hi
do the following 3 times. … that's all.

for every guest in the guests, say "Welcome," and then the guest.
for every letter in "кот", say letter.
for every drink in the price, say drink.          ключи таблицы, в порядке добавления
for every n from 1 to 10, say n squared.
for every n from 10 to 1, say n.                  обратный счёт: обе границы записаны числами
for every n from count down to 1, say n.          обратный счёт с вычисляемой границей
for every j from i plus 1 to n, say j.            при i = n не выполнится ни разу

as long as n is more than 1, halve n.
do the following until n is 0. … that's all.
keep doubling n until it is bigger than 100.      одно простое действие
```

- Количество повторений и границы счёта вычисляются один раз перед началом.
- Счёт идёт вниз, только если обе границы записаны числами и первая больше (`from 10 to 1`) или если написано `down to`. В остальных случаях счёт идёт вверх, и цикл пустой, когда начало больше конца. Направление известно при компиляции.
- `for every … in` проходит список так, как он выглядит в момент каждого шага: добавленные во время цикла элементы тоже будут пройдены. Таблица и текст проходятся по снимку, сделанному перед началом.
- `stop the loop` (также `stop repeating`, `leave the loop`) выходит из ближайшего цикла.
- `skip to the next one` (также `skip this one`, `go to the next one`) переходит к следующему шагу.
- Вне цикла эти команды — ошибка компиляции.

### 5.4 Условия

```text
if score is more than 50, say "well done".
if score is more than 50 then say "well done".
otherwise say "try again".

if score is more than 50
  say "well done".
otherwise, if score is more than 20
  say "not bad".
otherwise
  say "try again".
that's all.

say "yay" twice, but only if the user is happy.
```

`otherwise` относится к ближайшему предыдущему `if` без `otherwise` в той же группе, в том числе к последнему звену цепочки `otherwise, if`. Условие в конце предложения (`if`, `when`, `only if`, `but only if`, `provided that`) относится ко всему предложению.

### 5.5 Списки

```text
the guests are "Ana", "Bob" and "Dana".        список из перечисления
set scores to a list of 5.                      список из одного элемента
set crossed to an empty list.
add "Eve" to the guests.
remove "Bob" from the guests.                   первое совпадение; если нет — ничего не происходит
remove item 2 from the guests.
sort the guests.                                по возрастанию; текст — по алфавиту кодов
reverse the guests.
say item 1 of the guests.                       позиции с 1
say the first item of the guests.
say the last item of the guests.
say items 2 to 3 of the guests.                 выход за края обрезается
say the number of items in the guests.
if the guests contain "Ana", say "here".
say the sum of scores.                          также average, largest, smallest
```

- Позиция за пределами списка — ошибка выполнения: `There is no item 5: the list has 3 items.`
- Списки передаются по ссылке: если положить список в другую переменную и изменить его там, изменится и исходный.
- `the average of` всегда дробное. `largest`/`smallest`/`average` пустого списка — ошибка выполнения, `sum` пустого — 0.
- Перечисление через `and` внутри условия читается как логическое «и». Если список нужен в условии или как значение функции, его берут в скобки: `the total of (1, 2 and 3)`.

Сортировка, отбор и преобразование:

```text
sort scores from highest to lowest.             также in descending order; from lowest to highest — по возрастанию
sort the books by year.                         по полю
sort the words by the length of each.           по выражению; each — текущий элемент
set recent to every book in the books where the year of book is more than 1960.
set titles to the title of book for every book in the books.
say n times 2 for every n in scores where n is more than 50.
say the book in the books with the largest year.       также highest, biggest, most, smallest, lowest, least
```

- Сортировка устойчивая: элементы с одинаковым ключом сохраняют порядок.
- `every X in L where …` даёт новый список из подходящих элементов, `E for every X in L` — новый список из значений `E`. `where` можно добавить и ко второй форме.
- `X in L with the largest K` даёт сам элемент, а не ключ. Из нескольких лучших берётся первый. Пустой список — ошибка выполнения.

### 5.6 Таблицы

```text
the price of "coffee" is 900.                   таблица появляется с первой записью
set the price of "tea" to 600.
say the price of "tea".
if the price of "juice" is unknown, say "no juice".     также is known
remove the price of "tea".
for every drink in the price, say drink and then the price of drink.
say the number of items in the price.
the scores of "Ana" are 95, 88 and 92.          значения могут быть списками
add 1 to the count of "apples".                 отсутствующая запись начинается с нуля
```

Чтение отсутствующей записи без `add`/`increase` — ошибка выполнения: `There is no price of "milk".` Записи хранятся в порядке добавления.

### 5.7 Ввод

```text
ask "What is your name?" and remember the answer as name.
ask "How old are you?" and remember the answer as age.
```

Вопрос печатается с пробелом в конце, ответ читается до конца строки. Тип ответа выводится из того, как переменная используется дальше: если `age` участвует в арифметике, это число, и на ответ, который числом не является, программа печатает `Please type a whole number.` и спрашивает снова. Если подсказок нет, ответ — текст. Конец ввода — ошибка выполнения. В ботах `ask` не работает.

### 5.8 Файлы

```text
read the file "notes.txt" into notes.
for every line in the lines of the file "notes.txt", say line.
write "first line" to the file "out.txt".       перезаписать
add "next line" to the file "out.txt".          дописать в конец
if the file "out.txt" exists, say "found".
delete the file "out.txt".
```

- Пути считаются от папки, из которой запущена программа.
- `write` и `add … to the file` дописывают перевод строки, если текст им не заканчивается. Части соединяются как при печати.
- Нечитаемый файл — ошибка выполнения: `I can't read the file "x".` Удаление несуществующего файла ничего не делает.

### 5.9 Аргументы и выход

- `the arguments` — список текстов, переданных программе: `plain run todo.eng add milk` или `./todo add milk`.
- `stop the program` (также `quit`) завершает программу.

## 6. Свои команды и функции

### 6.1 Команда

```text
to greet someone, say "Hi" and then someone and then "!".
greet "Ana".

to visit a place
  add 1 to visits.
  say "Visiting" and then the place.
that's all.
visit "Almaty".
```

Определение начинается с `to`. Значения, которые команда принимает (параметры), — это слова после `a`, `an` или `some`, а также `someone`, `somebody`, `something`. Остальные слова — неизменная часть фразы. Вызов — та же фраза с конкретными значениями на месте параметров.

### 6.2 Функция

Определение `to find …` (также `to calculate`, `to compute`, `to get`, `to work out`) даёт функцию со значением. Её вызывают как существительное, без слова `find`:

```text
to find the area of a width and a height, give back the width times the height.
say the area of 3 and 4.

to find the factorial of a number
  if the number is at most 1, give back 1.
  give back the number times the factorial of (the number minus 1).
that's all.

to find a number doubled, give back the number times 2.
say 21 doubled.                                 функция, которая начинается с параметра, пишется после значения
```

- `give back X` (также `return X`) сразу возвращает значение.
- `the result is X` задаёт значение, которое вернётся в конце, если раньше не было `give back`.
- Функция, которая ничего не возвращает, — ошибка компиляции.

### 6.3 Правила

- Команды и функции можно вызывать до определения и из самих себя (рекурсия).
- Внутри определения видны его параметры, собственные переменные и общие переменные, заданные выше определения. Новое имя внутри определения — локальная переменная.
- Типы параметров выводятся из тела и из вызовов. Если тело не требует от параметра конкретного типа, функция обобщённая: `to find the first of some things, give back item 1 of the things.` работает и со списком чисел, и со списком книг. Для каждого набора типов компилятор делает отдельную копию функции, как шаблоны в C++.
- Параметр, названный именем вида (`to describe a book`), сразу получает этот вид.
- Первое слово определения должно быть своим словом, а не словом языка: `to show …` нельзя, потому что `show` уже означает печать.
- Если вызов подходит под две фразы сразу, это ошибка неоднозначности.

### 6.4 Виды (свои типы данных)

```text
a book has a title, an author and a year.
a weather has a temperature (decimal), a windspeed (decimal) and a code (number).
a report has a current weather (a weather) and some notes (a list of texts).

set dune to a new book with title "Dune", author "Frank Herbert" and year 1965.
say the title of dune.                          также dune's title
set the year of dune to 1966.
add 1 to the year of dune.
say dune.                                       → book (title: Dune, author: Frank Herbert, year: 1966)
```

- Описание вида начинается с `a`, `an`, `every` или `each`, затем имя вида и `has`. Поля перечисляются через запятые и `and`; `some` перед именем поля отбрасывается (`some notes` — поле `notes`).
- Типы полей выводятся из использования, как у переменных. В скобках после поля тип можно указать явно: `number`, `decimal`, `text`, `yes/no`, имя вида, `a list of …`, `a table of …`. Поле, названное как вид (`a weather has … and a place`, если есть вид `place`), получает этот вид. Явный тип нужен, когда поле только читается, например из JSON.
- `a new book` создаёт вещь, у которой все поля равны нулю своего типа. После `with` перечисляются начальные значения: `with title "Dune" and year 1965`.
- `the title of dune` и `dune's title` — одно и то же. Если `X of Y` подходит и под поле, и под таблицу, это ошибка неоднозначности.
- `is` для вещей сравнивает, тот же ли это объект. `nothing` — пустая ссылка: `if the next of node is nothing`. Чтение поля у `nothing` — ошибка выполнения `There is no node here: it is nothing.`
- При печати вложенные вещи сокращаются после трёх уровней: `node (value: 1, next: node (...))`.
- Наследования видов нет.

## 7. Модули

```text
use "shapes.eng".
```

Подключает определения и предложения другого файла. Путь считается от папки текущего файла. Каждый файл подключается один раз, циклы подключений безопасны. В сообщениях об ошибках указывается файл, где ошибка.

## 8. Сравнения

| Смысл | Формы |
|---|---|
| равно | `is`, `equals`, `is equal to`, `=` |
| не равно | `is not`, `isn't`, `does not equal`, `is different from`, `!=` |
| больше | `is more than`, `is greater than`, `is bigger than`, `is larger than`, `is higher than`, `is above`, `is over`, `exceeds`, `>` |
| меньше | `is less than`, `is smaller than`, `is fewer than`, `is lower than`, `is below`, `is under`, `<` |
| не меньше | `is at least`, `is not less than`, `>=` |
| не больше | `is at most`, `is not more than`, `<=` |
| содержит | `contains`, `contain`, `includes` (текст, список, таблица по ключу) |
| начинается с | `starts with`, `begins with` |

- Целые и дробные можно сравнивать между собой. Тексты сравниваются по кодам букв.
- `is` для текста точен и учитывает регистр. `contains` и `starts with` для текста регистр не учитывают.
- Условия объединяются через `and`, `or`, `not`. `and` сильнее `or`. Части условия вычисляются слева направо и только пока результат не ясен: в `if the price of x is known and the price of x is more than 5` вторая часть не вычисляется, если записи нет.
- Переменная да/нет — условие сама по себе: `if ready, …`.

## 9. Telegram-боты

Программа становится ботом, если в ней есть хотя бы одно правило `when someone sends …`.

### 9.1 Порядок выполнения

1. Предложения вне правил выполняются один раз при запуске, сверху вниз.
2. Если есть `remember everything in "file.json"`, память загружается из файла.
3. Бот получает сообщения через long polling.
4. Для каждого текстового сообщения правила проверяются сверху вниз; выполняется первое подходящее.
5. После каждого сообщения память сохраняется в файл, если сохранение включено.

Сообщения без текста (фото, стикеры, голосовые) пропускаются.

### 9.2 Правила

| Запись | Срабатывает, когда сообщение… |
|---|---|
| `when someone sends "/start"` | совпадает с текстом без учёта регистра и пробелов по краям |
| `when someone sends "hi", "hello" or "hey"` | совпадает с любым из вариантов |
| `when someone sends a number` | целиком является целым числом |
| `when someone sends a message that contains "price"` | содержит текст без учёта регистра |
| `when someone sends a message that starts with "/buy"` | начинается с текста |
| `when someone sends anything` | любое (также `anything else`, `something`, `a message`) |

Начало правила может звучать и как `when somebody sends`, `when anyone sends`, `when someone writes`, `when someone types`, `when someone says`, `when someone presses`, `when user sends`, `when they send`.

### 9.3 Сообщение и отправитель

| Выражение | Тип | Значение |
|---|---|---|
| `the message`, `their message`, `what they sent` | текст | текст сообщения |
| `their name`, `their first name` | текст | имя отправителя из профиля Telegram |
| `the number they sent` | целое | сообщение как число, или 0 |

### 9.4 Память

- Переменные с `their` (`their score`, `their tasks`) хранятся отдельно для каждого чата. Они существуют с начала программы и у нового пользователя равны нулю своего типа.
- Переменные без `their` общие для всех пользователей.
- `remember everything in "bot-memory.json".` сохраняет общие переменные и все `their …` в JSON-файл после каждого сообщения и загружает их при запуске. Без этой строки память живёт, пока работает процесс. Формат файла одинаков у `engc.py run` и у собранного бинарника.

### 9.5 Ответ

```text
reply "Thanks!".
reply "Hi" and then their name and then "!".
reply "Pick one" with buttons "Yes", "No" and "Maybe".
```

Глаголы: `reply`, `answer`, `respond`, `reply with`, `answer with`, `respond with`, `send back`. Кнопки показываются клавиатурой под полем ввода, по три в ряд; нажатие приходит как обычное сообщение с текстом кнопки.

`reply`, `the message`, `their name`, `the number they sent` и переменные `their …` разрешены только внутри правил и внутри своих команд, которые вызываются из правил. Компилятор проверяет это по всем цепочкам вызовов.

### 9.6 Переменные окружения

| Переменная | Назначение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота от @BotFather, обязательна |
| `TELEGRAM_API_URL` | адрес Bot API, по умолчанию `https://api.telegram.org` |
| `PLAIN_BOT_EXIT_WHEN_IDLE` | бот завершается, когда новых сообщений нет; для тестов |

## 10. Ошибки

Программа с ошибкой компиляции не запускается. Ошибка выполнения останавливает программу с кодом 1, если её не перехватили. Сообщения пишутся по-английски, с файлом и номером строки, и совпадают у `engc.py run`, у бинарника `engc.py build` и у бинарника `plain`.

### 10.1 Перехват ошибок

```text
try the following.
  read the file "settings.txt" into settings.
if something goes wrong
  say "Could not read settings:" and then the error.
that's all.

try to read the file "x.txt" into notes, and if that fails, set notes to "".
fail with "Age must be positive".
```

- `try the following` открывает группу. Её закрывает `if something goes wrong` (также `if anything goes wrong`, `if it fails`, `on error`), за которым идут действия при ошибке и `that's all`.
- `try to <действие>` перехватывает ошибку одного действия; `, and if that fails, <действие>` добавляет обработчик в том же предложении. Без обработчика ошибка просто пропускается.
- `the error` (также `what went wrong`) — текст последней перехваченной ошибки.
- `fail with "…"` останавливает программу своей ошибкой. Внутри `try` её можно перехватить, как любую другую.
- Перехватываются все ошибки выполнения: выход за список, нет файла, деление на ноль, ошибки сети и JSON, `fail with`. Типов ошибок нет, только текст.
- `stop the loop` и `give back` внутри `try` работают как обычно.

### 10.2 Сообщения

| Ситуация | Пример сообщения |
|---|---|
| неизвестное слово | `I don't know the word "prnit". Did you mean "print"?` |
| два прочтения | `This sentence can mean 2 different things: …` |
| конфликт типов | `"age" holds a whole number, so it can't become text.` |
| выход за список | `There is no item 5: the list has 3 items.` |
| нет записи | `There is no price of "milk".` |
| деление на ноль | `Division by zero.` |
| не число | `"abc" is not a whole number.` |
| нет файла | `I can't read the file "notes.txt".` |

Компилятор никогда не выбирает одно из нескольких прочтений сам.

## 11. Стандартная библиотека

### 11.1 Сеть

```text
fetch "https://api.example.com/items" into page.
send order to "https://example.com/orders" and remember the reply as answer.
send "plain text" to "https://example.com/notes".
```

- `fetch URL into name` делает GET-запрос и кладёт тело ответа в текст. Ответ с кодом 400 и выше — ошибка выполнения `The request to URL failed with status 404.`, недоступный адрес — `I couldn't reach URL.`
- `send X to URL` делает POST. Вещь, список или таблица отправляются как JSON (`Content-Type: application/json`); текст отправляется как есть, а если он похож на JSON, то тоже с типом JSON.
- Ошибки сети перехватываются через `try`.

### 11.2 JSON

```text
set data to dune as json.                       → {"title": "Dune", "year": 1965}
set copy to data as a book.
set books to page as a list of books.
set counts to page as a table of numbers.
```

- `X as json` превращает любое значение в текст JSON. Поля вида пишутся с подчёркиванием вместо пробела: поле `first name` становится ключом `first_name`.
- `text as <тип>` читает JSON прямо в тип: `a book`, `a list of books`, `a table of numbers`, `a number`, `a decimal`, `text`, `a list of texts`. При чтении ключ поля подходит и с подчёркиванием, и с пробелом.
- Отсутствующий ключ или значение другого типа дают ноль своего типа, а не ошибку. Текст, который не является JSON, — ошибка выполнения `The text is not valid JSON.`

### 11.3 Время

| Запись | Результат |
|---|---|
| `the current time` | вещь вида `moment` с полями `year`, `month`, `day`, `hour`, `minute`, `second` (целые) и `weekday` (текст, `Monday`) |
| `the current timestamp` | целое, секунды с 1970 года |
| `the current time in milliseconds` | целое |
| `wait 2 seconds`, `wait 300 milliseconds` | пауза; можно дробное число |

### 11.4 Система

| Запись | Результат |
|---|---|
| `the environment variable "HOME"` | текст; пустой, если переменной нет |
| `run "ls -l"` | запускает команду оболочки и ждёт её окончания |
| `run "ls -l" and remember the output as listing` | то же, вывод команды попадает в текст |
| `the exit code` | код завершения последней команды |

## 12. Словарь

Все синонимы перечислены в `lexicon.json`. Словарь заморожен внутри редакции: в редакции 2026 значение фраз не меняется.

## 13. Чего пока нет

- наследования видов и интерфейсов;
- функций как значений (лямбд): их заменяют `sort by`, `every … where`, `… for every …` и `with the largest`;
- потоков и параллельного выполнения;
- типов ошибок: `try` перехватывает любую ошибку, её описание — только текст;
- пакетного менеджера и отладчика;
- inline-кнопок под сообщением и рассылки всем пользователям бота;
- HTTPS-сервера: программа может отправлять запросы, но не принимать их.
