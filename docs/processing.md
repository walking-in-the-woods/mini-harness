# Chunked-обработка длинных файлов

## Зачем

Микромодели на N100 имеют контекст 2048–4096 токенов. Файлы
длиннее не помещаются целиком. Без chunked-режима `batch`
помечает такие файлы failed с сообщением «use --chunk».

С `--chunk` файл разбивается на чанки, каждый обрабатывается
отдельно, результаты собираются.

## Использование

```bash
/batch <src> <glob> <prompt> [<target>] --mode=code|docs [--chunk]
```

Примеры:

```bash
# Non-chunked: файл > бюджет → failed
/batch input/batch '*.py' input/prompts/add-docstrings.md --mode=code

# Chunked: файл разбивается автоматически
/batch input/batch '*.py' input/prompts/add-docstrings.md --mode=code --chunk

# Docs
/batch input/docs '*.md' input/prompts/translate.md --mode=docs --chunk

# Partial-режим
/batch input/docs '*.md' input/prompts/translate.md --mode=docs --chunk \
       --on-failure=partial
```

## Как работает для кода (mode=code)

1. Файл парсится как Python AST.
2. Top-level `def`/`async def`/`class` — каждая сущность становится
   отдельным чанком. Декораторы входят в чанк.
3. Всё остальное (импорты, глобалы, `if __name__`) — не чанки.
   Сохраняется merge'ом как есть.
4. Каждый чанк трансформируется отдельно.
5. Merge — замена блоков в оригинале по offset'ам.
6. Validate — все defs на месте, тела непусты, аргументы не
   изменились.

## Как работает для документов (mode=docs)

1. Разбиение по заголовкам (`#` и `##` по умолчанию).
2. Если заголовков нет или одна секция — по параграфам.
3. Если параграф не влезает — жёсткий рез по байтам, с warning.
4. Merge — конкатенация выходов.
5. Validate — все заголовки на месте, fence'ы сбалансированы.

## Расчёт бюджета чанка

```text
prompt_tokens  = estimate_tokens(prompt_text)
T_available    = num_ctx − prompt_tokens − 50
budget_input   = T_available × (1 − output_reserve_ratio)
budget_used    = budget_input × budget_safety
budget_bytes   = budget_used × 4
```

Где:

- `estimate_tokens(text) = (len(text.encode('utf-8')) + 3) // 4`.
  Для ASCII даёт `chars/4`, для кириллицы — `chars/2`.
- `output_reserve_ratio` (дефолт 0.4) — доля бюджета под ответ модели.
- `budget_safety` (дефолт 0.7) — safety margin.
- `50` — overhead на шапку `[Part N/M]` и обёртку (константа в коде).

Для 4B на `num_ctx=4096`, prompt=2 КБ, safety=0.7:

```text
prompt_tokens ≈ 500
T_available = 4096 − 500 − 50 = 3546
budget_input = 3546 × 0.6 = 2128
budget_used = 2128 × 0.7 = 1490
budget_bytes = 1490 × 4 = 5960
```

Чанк кода ≈ 5960 байт ≈ 1800 символов латиницы. Для русского
текста — ~3000 символов.

## Тюнинг budget_safety

Процедура:

1. Стартуем с 0.7.
2. Прогоняем batch на эталонном файле.
3. **Если llama-server вернул 400/500 с «context size exceeded»** —
   снижаем: 0.6, 0.5.
4. **Если всё проходит, но запросов слишком много** — повышаем:
   0.75, 0.8.
5. Останавливаемся на значении, где нет ошибок и скорость приемлема.

Это значение **для конкретной модели** и **конкретного `num_ctx`**.
При смене любого из них — перетюнинг.

## Конфигурация

Всё в `config/processing.yaml`. Файл опционален, при отсутствии —
дефолты. Полный список полей — в самом файле с комментариями.

Ключевые:

- `defaults.chunk_min_bytes` — минимальный размер чанка.
- `defaults.budget_safety` — safety margin.
- `defaults.output_reserve_ratio` — доля под ответ.
- `defaults.on_chunk_failure` — `fail` или `partial`.
- `defaults.on_merge_invalid` — `fail` или `partial`.
- `defaults.on_insufficient_context` — `fail` или `skip`.
- `code.include_preamble` — включать импорты в каждый чанк.
- `code.validate` — какие проверки применять.
- `docs.header_levels` — уровни заголовков для разбиения.

Override через CLI: `--on-failure`, `--on-merge-invalid`,
`--on-insufficient-context`.

## Partial-режим

При `on_chunk_failure: partial`:

- Успешные чанки записываются в `<target>`.
- Проваленные — с орнаментом в тексте (для code) или оригинальным
  текстом (для docs).
- Рядом создаётся `<target>.failed/` с README и дампами каждого
  проваленного чанка.

```text
output/batch/2026-09-24-XXXXXX/
├── big.py
└── big.py.failed/
    ├── README.md
    ├── chunk_03_source.txt
    ├── chunk_03_output.txt
    └── chunk_03_error.txt
```

Проваленные фрагменты в merged-файле обёрнуты комментарием:

```python
# ======== NOT PROCESSED: chunk 3/8 ========
def multiply(a, b):
    return a * b
# ======== END NOT PROCESSED ========
```

## INSUFFICIENT_CONTEXT

Модель может явно отказаться: если она не может обработать чанк,
она выводит ровно строку `INSUFFICIENT_CONTEXT`. Harness это
распознаёт.

Поведение — `defaults.on_insufficient_context`:

- `fail` (по умолчанию) — считать как провал чанка.
- `skip` — считать как явный пропуск, оригинал сохраняется в merged.

## Ограничения

- **Class не разбивается на методы.** Если класс больше бюджета —
  `ProcessError`.
- **Только Python для mode=code.** Другие языки — `ProcessError`.
- **Overlap между чанками не реализован.** Поле в конфиге есть,
  но > 0 вызывает `ProcessError`.
- **Chunked-режим не гарантирует полное сохранение стиля.** Модель
  видит функции в изоляции; если стиль требует знания соседей —
  ожидайте отклонений.
