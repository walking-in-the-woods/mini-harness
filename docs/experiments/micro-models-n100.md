# Микромодели 3B на N100/8GB

## Контекст

- **Машина:** Intel N100, 4 ядра, 8 ГБ RAM, без GPU
- **Ollama:** 0.32.5
- **Режим:** guided mode, пользовательский prompt-файл
- **HARNESS_NUM_CTX:** 2048
- **HARNESS_NUM_PREDICT:** 1024
- **HARNESS_KEEP_ALIVE:** 5m
- **Дата:** 2026-09

Модели подобраны по профилю:

- `llama3.2:3b` — текстовая, диалоговая
- `qwen2.5-coder:3b` — кодовая, специализированная
- `qwen3:4b` — reasoning, для guided mode не подходит (см. раздел ниже)

Цель экспериментов — понять, какие задачи эти модели могут решать
в guided mode, а какие нет. Тестируется не harness, а потолок
моделей: harness делает всю работу по планированию, модель —
только трансформирует текст.

## Тест 1: llama3.2:3b — суммаризация

### Задача

Прочитать `input/article.md` (статья про биолюминесцентные грибы,
~1300 символов) и написать резюме в 3-4 предложениях.

### Prompt-файл `input/prompts/summarize-article.md`

```markdown
Опиши содержание статьи. Ответ — 3-4 предложения на русском языке.

Скажи, о чём статья, кто её авторы, что было обнаружено и в чём
практическая значимость. Упомяни конкретные факты: название вида,
длину волны свечения, имена исследователей.

ВАЖНО: выведи ТОЛЬКО готовый текст. Не пиши вводных фраз вроде
«Хорошо, мне нужно…» или «В этой статье…». Начни сразу с первого
предложения.

Связный текст без заголовков, списков и разметки.
```

### Команда

```sh
прочитай input/article.md согласно инструкции из input/prompts/summarize-article.md, результат в output/article_summary.md
```

### Вывод stderr

```sh
[*] guided mode: summarize input/article.md -> output/article_summary.md [prompt: input/prompts/summarize-article.md]
[*] custom model output: 'Новый вид светящихся грибов в лесах Амазонии получил название *Mycena luxaeterna*, что означает «вечный свет». Этот вид ' (453 chars)
```

### Результат

```markdown
Новый вид светящихся грибов в лесах Амазонии получил название *Mycena luxaeterna*, что означает «вечный свет». Этот вид биолюминесцентных грибов был обнаружен в тропических лесах Амазонии в ходе трехлетней экспедиции под руководством доктора Марии Сантос из Федерального университета Амазонас. Новый вид отличается интенсивным зелёным свечением с длиной волны 520 нанометров, которое служит для привлечения насекомых, которые распространяют споры гриба.
```

### Оценка

- **Выполнено требований:** 3 из 4 (о чём, кто автор, что обнаружено; пропущена практическая значимость).
- **Ключевые факты:** вид, автор, длина волны — есть.
- **Формат:** связный текст, без заголовков и списков.
- **Признаков reasoning нет.**
- **Вывод:** для 3 требований — работает. Четвёртое вытесняется.

## Тест 2: llama3.2:3b — извлечение фактов

### Задача

Извлечь 5 ключевых фактов из того же `article.md` в маркированный список.

### Prompt-файл `input/prompts/extract-facts.md`

```markdown
Извлеки из текста 5 ключевых фактов. Ответ — маркированный список
на русском языке.

Каждый пункт — одна короткая фраза с конкретным фактом. Без общих
слов, только то, что можно проверить по тексту.

ВАЖНО: выведи ТОЛЬКО список. Не пиши вводных фраз, не пересказывай
текст, не добавляй пояснений после списка.

Формат каждого пункта: «- <факт>».
```

### Команда

```sh
прочитай input/article.md согласно инструкции из input/prompts/extract-facts.md, результат в output/article_facts.md
```

### Вывод stderr

```sh
[*] guided mode: custom input/article.md -> output/article_facts.md [prompt: input/prompts/extract-facts.md]
[*] custom model output: '- Биологи объявили об открытии нового вида биолюминесcentных грибов в тропических лесах Амазонии. - Новый вид получил на' (559 chars)
```

### Результат

```markdown
- Биологи объявили об открытии нового вида биолюминесcentных грибов в тропических лесах Амазонии.
- Новый вид получил название *Mycena luxaeterna*, что означает «вечный свет» на латыни.
- Свечение гриба служит для привлечения насекомых, которые распространяют споры гриба.
- Гриб растёт на гниющих стволах деревьев и предпочитает влажные участки на высоте от 200 до 600 метров над уровнем моря.
- Исследователи видят потенциал в двух направлениях: создание экологически чистых источников освещения и разработке биосенсоров для обнаружения загрязнений в почве.
```

### Оценка

- **Пунктов:** 5 из 5.
- **Формат:** каждый пункт начинается с `-`, корректно.
- **Факты:** проверяемые, без воды.
- **Дефект:** опечатка `биолюминесcentных` — смешаны кириллица и латиница. Редкий glitch токенизатора, не галлюцинация.
- **Вывод:** структурированный вывод — сильная сторона 3B. Список выполнен идеально.

## Тест 3: qwen2.5-coder:3b — добавление методов в класс

### Задача

Добавить два метода `increment()` и `decrement()` в класс `Counter`,
сохранив весь существующий код.

### Исходный файл `input/counter.py`

```python
"""Simple counter with configurable step."""


class Counter:
    """Counter that increments or decrements by a fixed step."""

    def __init__(self, start=0, step=1):
        self.value = start
        self.step = step
        self.history = [start]

    def get_value(self):
        return self.value

    def reset(self):
        self.value = 0
        self.history = [0]

    def get_history(self):
        return list(self.history)

    # TODO: implement increment() and decrement()
```

### Prompt-файл `input/prompts/add-methods.md`

```markdown
В файле описан класс Counter. Добавь в класс два метода:
increment() и decrement().

Метод increment() увеличивает self.value на self.step, добавляет
новое значение в self.history и не возвращает ничего.

Метод decrement() уменьшает self.value на self.step, добавляет
новое значение в self.history и не возвращает ничего.

У каждого метода должен быть docstring в стиле существующих методов
класса (краткое описание в одну строку).

ВАЖНО: выведи ВЕСЬ файл целиком, включая уже существующий код.
Не сокращай, не пиши «# остальной код без изменений». Сохрани
все существующие методы и их docstrings без изменений.

Выведи только Python-код, без markdown-обёртки и без пояснений.
```

### Команда

```sh
прочитай input/counter.py согласно инструкции из input/prompts/add-methods.md, результат в output/code/counter_extended.py
```

### Вывод stderr

```sh
[*] guided mode: custom input/counter.py -> output/code/counter_extended.py [prompt: input/prompts/add-methods.md]
[*] custom model output: 'class Counter:     """Counter that increments or decrements by a fixed step."""      def __init__(self, start=0, step=1)' (752 chars)
```

### Результат

```python
class Counter:
    """Counter that increments or decrements by a fixed step."""

    def __init__(self, start=0, step=1):
        self.value = start
        self.step = step
        self.history = [start]

    def get_value(self):
        return self.value

    def reset(self):
        self.value = 0
        self.history = [0]

    def get_history(self):
        return list(self.history)

    def increment(self):
        """Increments the counter value by the step and adds it to the history."""
        self.value += self.step
        self.history.append(self.value)

    def decrement(self):
        """Decrements the counter value by the step and adds it to the history."""
        self.value -= self.step
        self.history.append(self.value)
```

### Проверки

```sh
$ python3 -m py_compile workspace/output/code/counter_extended.py && echo "syntax OK"
syntax OK
$ grep -c "def increment\|def decrement" workspace/output/code/counter_extended.py
2
$ python3 -c "
import sys
sys.path.insert(0, 'workspace/output/code')
from counter_extended import Counter
c = Counter(start=10, step=5)
c.increment()
c.increment()
c.decrement()
assert c.get_value() == 15
assert c.get_history() == [10, 15, 20, 15]
print('logic OK')
"
logic OK
```

### Оценка

- **Оба метода добавлены, логика верна.**
- **Docstrings в стиле существующих.**
- **Весь существующий код сохранён.**
- **Функциональный тест прошёл.**
- **Замечание:** первый прогон был заблокирован harness — `.py` в `WRITE_EXT_BLOCK`. Решено через `ext_allow_paths` для `output/code/**`. Модель работала корректно оба раза.

## Тест 4: qwen2.5-coder:3b — генерация docstrings

### Задача

Добавить Google-style docstrings к четырём функциям без них.
Условное правило: `Raises:` — только там, где функция реально
выбрасывает исключение.

### Исходный файл `input/text_utils.py`

```python
"""Text processing utilities."""

import re


def clean_whitespace(text, collapse=True):
    if collapse:
        return re.sub(r"\s+", " ", text).strip()
    return text.strip()


def word_count(text, min_length=1):
    words = text.split()
    return len([w for w in words if len(w) >= min_length])


def truncate(text, max_length, suffix="..."):
    if max_length < 0:
        raise ValueError("max_length must be non-negative")
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def extract_emails(text):
    pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    return re.findall(pattern, text)
```

### Prompt-файл `input/prompts/add-docstrings.md`

```markdown
В файле text_utils.py описаны четыре функции без docstrings.

Добавь каждой функции docstring в формате Google-style.

Структура docstring:

1. Первая строка — краткое описание функции (одно предложение,
   в повелительном наклонении: «Очищает...», «Считает...»,
   «Усекает...», «Извлекает...»).

2. Пустая строка.

3. Блок Args: — для каждого параметра строка вида:
       param_name (type): описание параметра.
   Описывай только те параметры, у которых нет значения по умолчанию,
   если параметр опциональный — упомяни в скобках дефолт.

4. Блок Returns: — тип возвращаемого значения и краткое описание.

5. Блок Raises: — только если функция может выбросить исключение.
   Формат: ExceptionType: описание условия.

ВАЖНО: выведи ВЕСЬ файл целиком, включая все импорты, все функции
и весь существующий код без изменений. Не сокращай. Не переписывай
тело функций — только добавь docstring сразу под строкой def.

Выведи только Python-код, без markdown-обёртки и без пояснений.
```

### Команда

```sh
прочитай input/text_utils.py согласно инструкции из input/prompts/add-docstrings.md, результат в output/code/text_utils_documented.py
```

### Вывод stderr

```sh
[*] guided mode: custom input/text_utils.py -> output/code/text_utils_documented.py [prompt: input/prompts/add-docstrings.md]
[*] custom model output: '"""Text processing utilities."""  import re   def clean_whitespace(text, collapse=True):     """     Removes extra white' (2037 chars)
```

### Результат

См. `artifacts/text_utils_documented.py`.

### Оценка

- **4 из 4 функций с docstrings.**
- **`Args:` везде, `Returns:` везде.**
- **`Raises:` только в `truncate`** — условное правило выполнено.
- **Модульный docstring и импорт сохранены.**
- **Тело функций не изменено.**
- **Замечания:** описания на английском (стандарт для кода); `extract_emails` возвращает `list`, не `list[str]`; `clean_whitespace` упоминает «collapsed whitespace» без оговорки для `collapse=False`.

## Тест 5: таблица с классификацией из трёх категорий

### Задача

Извлечь 6 фактов из `article.md`, оформить как markdown-таблицу
с двумя колонками: «Факт» и «Категория». Категория — одно из трёх
значений: Открытие, Метод, Практика.

Отличие от теста 2: там был список без выбора, здесь — семантическая
классификация каждой строки.

### Prompt-файл `input/prompts/extract-facts-table.md`

```markdown
Извлеки из текста 6 ключевых фактов. Оформи как markdown-таблицу
с двумя колонками: «Факт» и «Категория».

Колонка «Категория» принимает ровно одно из трёх значений:
Открытие, Метод, Практика.

Значение категории определяется так:
- Открытие — что обнаружено: объект, характеристики, локация.
- Метод — как исследовали или как работает механизм.
- Практика — где можно применить результат.

Структура таблицы: первая строка — заголовки «Факт» и «Категория»
через разделитель |, вторая строка — разделитель из тире, дальше
шесть строк данных. Каждая строка начинается и заканчивается
символом |.

ВАЖНО: выведи ТОЛЬКО таблицу. Не пиши заголовков секций, вводных
фраз, пояснений. Не рассуждай, не проверяй себя. Каждый факт —
одна короткая фраза без точки в конце.
```

### Прогон A: llama3.2:3b

**Команда:**

```sh
прочитай input/article.md согласно инструкции из input/prompts/extract-facts-table.md, результат в output/facts_table_llama.md
```

**Вывод stderr:**

```sh
[*] guided mode: custom input/article.md -> output/facts_table_llama.md [prompt: input/prompts/extract-facts-table.md]
[*] custom model output: '| Факт | Категория | | --- | --- | | Обнаружено новое вид биолюминесцентных грибов в тропических лесах Амазонии | Открыт' (695 chars)
```

**Результат:**

```markdown
| Факт | Категория |
| --- | --- |
| Обнаружено новое вид биолюминесцентных грибов в тропических лесах Амазонии | Открытие |
| Название нового вида - *Mycena luxaeterna*, что означает «вечный свет» | Открытие |
| Биолюминесценция основана на реакции люциферин-люцифераза | Метод |
| Гриб растёт на гниющих стволах деревьев, предпочитает влажные участки на высоте от 200 до 600 метров над уровнем моря | Открытие |
| Практическое применение открытия: создание экологически чистых источников освещения и разработка биосенсоров для обнаружения загрязнений в почве | Практика |
| Финансирование экспедиции обеспечил Национальный совет по научным и технологическим исследованиям Бразилии | Открытие |
```

См. также `artifacts/facts_table_llama.md`.

**Оценка:**

- **Формат:** 6 из 6 — заголовок, разделитель `| --- | --- |`, 6 строк, каждая обрамлена `|`, нет обёртки, нет вводных фраз.
- **Классификация:** 5 из 6. Единственная ошибка — «финансирование» отнесено к «Открытие», хотя это контекст исследования (ближе к «Метод»).
- **Разнообразие категорий:** Открытие×4, Метод×1, Практика×1.
- **Грамматика:** «Обнаружено новое вид» — glitch токенизатора, аналогичный `биолюминесcentных` из теста 2.
- **Выбор фактов:** шесть тем — обнаружение, название, механизм, локация, применение, финансирование. Разные, без повторов.
- **Вывод:** формат выполнен идеально, классификация почти идеальна. Модель споткнулась на пограничном факте.

### Прогон B: qwen2.5-coder:3b

**Команда:**

```sh
прочитай input/article.md согласно инструкции из input/prompts/extract-facts-table.md, результат в output/facts_table_qwen.md
```

**Вывод stderr:**

```sh
[*] guided mode: custom input/article.md -> output/facts_table_qwen.md [prompt: input/prompts/extract-facts-table.md]
[*] custom model output: '| Факт | Категория | |------|----------| | Найден новый вид светящихся грибов в лесах Амазонии | Открытие | | Вид получи' (488 chars)
```

**Результат:**

```markdown
| Факт | Категория |
|------|----------|
| Найден новый вид светящихся грибов в лесах Амазонии | Открытие |
| Вид получили название *Mycena luxaeterna* | Открытие |
| Механизм биолюминесценции основан на реакции люциферин-люцифераза | Метод |
| Гриб растёт на гниющих стволах деревьев, предпочитает влажные участки | Практика |
| Основное применение — создание экологически чистых источников освещения | Практика |
| Разработка биосенсоров для обнаружения загрязнений в почве | Практика |
```

См. также `artifacts/facts_table_qwen.md`.

**Оценка:**

- **Формат:** 6 из 6. Разделитель `|------|----------|` — валидный GFM.
- **Классификация:** 4 из 6.
  - Факт о локации ошибочно отнесён к «Практика» (глагол «растёт» сбил модель на «применение»).
  - Факты 5 и 6 — это один факт из источника, раздробленный на две строки, чтобы заполнить 6 строк и всё уложить в «Практику».
- **Разнообразие категорий:** Открытие×2, Метод×1, Практика×3. Перекос.
- **Грамматика:** «Вид получили название» — согласование неверное.
- **Выбор фактов:** перекос в практическую значимость, три из шести строк об одном.
- **Вывод:** формат идеален, классификация слабее, чем у llama. Coder-модель смотрит на глаголы и путает характеристику с применением.

### Сравнение

| Критерий | llama3.2:3b | qwen2.5-coder:3b |
| --- | --- | --- |
| Формат | идеально | идеально |
| Классификация | 5 из 6 | 4 из 6 |
| Разнообразие категорий | Открытие×4, Метод×1, Практика×1 | Открытие×2, Метод×1, Практика×3 |
| Грамматика | «Обнаружено новое вид» | «Вид получили название» |
| Разделитель | `\| --- \| --- \|` | `\|------\|----------\|` |

**Общий вывод по тесту 5:** таблица с классификацией из трёх
категорий — работает на обеих 3B моделях. Гипотеза о превышении
сложности не подтвердилась. Обе модели теряют точность на границе
смежных категорий: llama — между «Открытие» и «Метод», qwen —
между «Открытие» и «Практика». Формулировка категорий как
взаимоисключающих критична.

## Тест 8: рефакторинг — выделение helper

### Задача

В `validators.py` три функции с одинаковой структурой проверок.
Выделить общую часть в `_validate_basic`, вызвать из каждой
`validate_*`, сохранить поведение без изменений.

Первый тест, где модель должна **изменить** существующий код, а
не добавить новый. Проверяется способность сохранять семантические
инварианты (возвращаемое значение, точные сообщения, разницу
между похожими условиями).

### Исходный файл `input/validators.py`

```python
"""Basic validators for user-supplied identifiers."""


def validate_username(name):
    if not name:
        raise ValueError("username is empty")
    if len(name) > 32:
        raise ValueError("username too long")
    if not name.isalnum():
        raise ValueError("username must be alphanumeric")
    return True


def validate_slug(slug):
    if not slug:
        raise ValueError("slug is empty")
    if len(slug) > 64:
        raise ValueError("slug too long")
    cleaned = slug.replace("-", "").replace("_", "")
    if not cleaned.isalnum():
        raise ValueError("slug has invalid characters")
    return True


def validate_tag(tag):
    if not tag:
        raise ValueError("tag is empty")
    if len(tag) > 16:
        raise ValueError("tag too long")
    if not tag.isalnum():
        raise ValueError("tag must be alphanumeric")
    return True
```

### Prompt-файл `input/prompts/refactor-validators.md`

```markdown
В файле validators.py три функции: validate_username, validate_slug,
validate_tag. У всех трёх одинаковая структура проверок.

Выдели общую часть в helper-функцию _validate_basic. Вызови её
из каждой validate_*. Функции validate_username, validate_slug,
validate_tag должны сохранить своё поведение без изменений.

Параметры каждой функции:

- validate_username: имя "username", лимит длины 32, разрешены
  только латинские буквы и цифры.
- validate_slug: имя "slug", лимит длины 64, разрешены латинские
  буквы, цифры, дефис и подчёркивание.
- validate_tag: имя "tag", лимит длины 16, разрешены только
  латинские буквы и цифры.

ВАЖНО: сообщения об ошибках должны остаться точно такими же, как
в исходном файле. Для пустого значения — "<имя> is empty". Для
слишком длинного — "<имя> too long". Для недопустимых символов
у username и tag — "<имя> must be alphanumeric", у slug — "slug
has invalid characters".

ВАЖНО: выведи ВЕСЬ файл целиком, включая docstring модуля, все
три функции и helper. Сохрани порядок функций. Не добавляй новых
функций, кроме _validate_basic.

Выведи только Python-код, без markdown-обёртки и без пояснений.
```

### Команда

```sh
прочитай input/validators.py согласно инструкции из input/prompts/refactor-validators.md, результат в output/code/validators_refactored.py
```

### Вывод stderr

```sh
[*] guided mode: custom input/validators.py -> output/code/validators_refactored.py [prompt: input/prompts/refactor-validators.md]
[*] custom model output: '"""Basic validators for user-supplied identifiers."""   def _validate_basic(name, max_length, error_message):     if not' (634 chars)
```

### Результат

```python
"""Basic validators for user-supplied identifiers."""


def _validate_basic(name, max_length, error_message):
    if not name:
        raise ValueError(f"{name} is empty")
    if len(name) > max_length:
        raise ValueError(f"{name} too long")
    cleaned = name.replace("-", "").replace("_", "")
    if not cleaned.isalnum():
        raise ValueError(error_message)


def validate_username(name):
    _validate_basic(name, 32, "username must be alphanumeric")


def validate_slug(slug):
    _validate_basic(slug, 64, "slug has invalid characters")


def validate_tag(tag):
    _validate_basic(tag, 16, "tag must be alphanumeric")
```

См. также `artifacts/validators_refactored.py`.

### Проверки

**Синтаксис:**

```sh
$ python3 -m py_compile workspace/output/code/validators_refactored.py && echo "syntax OK"
syntax OK
```

**Функциональный тест** (полный скрипт — в промпте теста) даёт
четыре падения:

```sh
$ python3 -c "
import sys
sys.path.insert(0, 'workspace/output/code')
from validators_refactored import (
    validate_username, validate_slug, validate_tag
)

# --- validate_username ---
assert validate_username('alice') is True   # FAIL: None is not True
"
AssertionError
```

### Оценка

**Форма:** корректна. Helper создан, вызван из всех трёх функций,
порядок функций сохранён, docstring модуля сохранён.

**Семантика:** провалена в четырёх местах.

1. **Возвращаемое значение изменилось.** Оригинал возвращал `True`
   при успехе. Refactored возвращает `None` — helper ничего не
   возвращает, а `validate_*` просто вызывают его без `return`.

2. **Сообщение о пустом значении стало пустым.** `f"{name} is empty"`
   при `name=""` даёт `" is empty"`. В оригинале была константа
   `"username is empty"`.

3. **Сообщение о длине содержит значение.** `f"{name} too long"`
   для длинного name даёт «aaaa... too long». В оригинале —
   `"username too long"`.

4. **Проверка символов слишком мягкая.** Helper всегда удаляет
   `-` и `_` перед `isalnum()`. Это верно для `slug`, но неверно
   для `username` и `tag`, которые в оригинале требовали чистого
   `isalnum()`. `validate_username('alice-bob')` теперь проходит,
   хотя должен падать.

### Разбор причин

1. **Путаница в роли параметра `name`.** Промпт описывал «имя
   "username"» — идентификатор для сообщений об ошибке. Модель
   интерпретировала `name` как значение, которое валидируется,
   и подставила его в f-строки вместо констант.

2. **Возвращаемое значение не описано явно.** Формулировка
   «сохранить поведение» слишком абстрактна для 3B. Модель
   проверила исключения, но забыла про `return True`.

3. **Условие для `slug` показалось лишним.** Модель
   «унифицировала» три проверки до одного `replace.replace.isalnum()`,
   не различив, что `username` и `tag` должны быть строже.

### Сравнение с аддитивными задачами

| Задача | Тип | Результат |
| --- | --- | --- |
| Добавить методы в класс | аддитивная | идеально |
| Добавить docstrings | аддитивная | идеально |
| Извлечь в список / таблицу | аддитивная | идеально |
| **Выделить helper** | **рефакторинг** | **провал по семантике** |

**Вывод:** 3B надёжны для аддитивных задач. При перестройке кода
семантика теряется, даже если формальный шаблон соблюдён. Модель
меняет «форму» кода, но не отслеживает, что при этом сохранить.

Практический вывод для guided mode с 3B:

- Задачи «добавь X» — да.
- Задачи «измени X так, чтобы Y сохранилось» — нет.
- Для рефакторинга нужны модели от 7B или специализированные
  (qwen2.5-coder:7b, не 3b).

## Сводка

| # | Задача | Модель | Требований | Выполнено | Итог |
| --- | --- | --- | --- | --- | --- |
| 1 | Суммаризация статьи | llama3.2:3b | 4 | 3 | частично |
| 2 | Извлечение 5 фактов списком | llama3.2:3b | 3 | 3 | успех |
| 3 | Добавление методов в класс | qwen2.5-coder:3b | 6 | 6 | идеально |
| 4 | Docstrings с условным `Raises:` | qwen2.5-coder:3b | 9 | 9 | идеально |
| 5a | Таблица с классификацией | llama3.2:3b | 12 | 11 | успех |
| 5b | Таблица с классификацией | qwen2.5-coder:3b | 12 | 10 | успех с оговоркой |
| 8 | Рефакторинг: helper | qwen2.5-coder:3b | 9 | 5 | провал по семантике |

## Установленные границы

**Что работает на 3B:**

- Структурные задачи со строгим форматом: список, docstring,
  методы в класс, markdown-таблица.
- Простые условные правила: `Raises:` только там, где нужно.
- Классификация из 2-3 взаимоисключающих категорий.
- Тексты до ~1500 символов источника + ~500 символов промпта.
- Специализация: `llama3.2:3b` — текст, `qwen2.5-coder:3b` — код.

**Где предел:**

- **Три требования — потолок для содержательных задач.** Четвёртое
  вытесняет первые. В тесте 1 модель выполнила 3 из 4, потеряв
  практическую значимость.
- **Классификация с пересекающимися категориями.** Выбор становится
  почти случайным на границе. Чем чётче разделены определения, тем
  надёжнее результат.
- **Рефакторинг с сохранением поведения.** 3B меняет форму кода,
  но не отслеживает семантические инварианты (возвращаемые значения,
  точные сообщения, разницу между похожими проверками). Форма
  задачи соблюдается, поведение — нет. Это принципиальное
  ограничение: аддитивные задачи работают, модифицирующие — нет.
- **Длинные тексты.** Больше 2000 символов источника при
  `num_ctx=2048` начинают обрезаться.
- **Reasoning-leakage.** `qwen3:4b` не подходит для guided mode:
  `/no_think` не помогает в текущей сборке Ollama.

## Особый случай: qwen3:4b

Не включён в основные тесты, но проверялся отдельно. Модель
генерирует 6.6 КБ рассуждений вслух («Хорошо, мне нужно...») даже
при `think=False` и явном `/no_think` в system-промпте. Известный
баг сборки `qwen3:4b` в Ollama — см. issues #12438 и #12234.

Harness защищён: детектор `_looks_like_reasoning` + retry + жёсткий
отказ после второго срабатывания. Файл не записывается, nonce не
показывается. Но сама модель в guided mode бесполезна.

**Рекомендация:** для 4B использовать `qwen2.5:7b-instruct` (не
reasoning) или остаться на 3B-моделях.

## Что тестировать дальше

Планируемые сценарии (по возрастанию сложности):

- **Тест 6:** длинный файл (3000+ слов). Проверка деградации
  при переполнении контекста.
- **Тест 7:** перевод с сохранением markdown-разметки.
  `llama3.2:3b`.
- **Batch-режим.** Обработка коллекции файлов и скользящее окно
  по длинному файлу. Архитектура обсуждается отдельно.
