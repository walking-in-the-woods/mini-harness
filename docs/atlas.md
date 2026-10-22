# Project Atlas — семантический граф проекта

Atlas — встроенная подсистема mini-harness, которая строит локальный
семантический граф проекта на SQLite и даёт LLM-агенту единый
read-only инструмент `atlas(op, arg, id)` для навигации по коду.

Цель — снизить объём контекста при ответах на структурные вопросы
(«как устроен X», «кто вызывает Y», «что зависит от Z») не менее чем
на 90% и убрать галлюцинации о структуре проекта. Модель получает
детерминированные карточки узлов вместо чтения целых файлов.

Atlas полностью локальный: ни одного сетевого вызова, ни одной
телеметрии. Работает на чистой стандартной библиотеке Python
(`ast`, `sqlite3`, `hashlib`).

**Atlas необязателен.** Если он выключен в `config.yaml`, приложение
работает ровно как раньше: guided mode, batch, sources, proxy —
всё на месте. Atlas появляется только если его явно включили.

---

## TL;DR

```bash
# Один раз проиндексировать проект
scripts/atlas-setup.sh init

# Запустить REPL
./run.sh

# В REPL
>>> /atlas stats
>>> /atlas overview
>>> как устроен класс Counter? используй atlas
```

---

## Зачем это нужно

На N100 с 8 ГБ RAM контекстное окно модели — 2048–4096 токенов.
Прочитать `harness/agent.py` (34 КБ) целиком невозможно. Модель
видит только первые 3000 символов, что для навигации бесполезно.

Atlas решает это так: вместо файла модель получает карточку узла —
квалифицированное имя, сигнатуру, summary, список вызовов. Одна
карточка укладывается в 150 токенов и содержит всё нужное для
ответа на структурный вопрос.

| Сценарий | Без Atlas | С Atlas |
| --- | --- | --- |
| «Что делает `HarnessAgent._dispatch`?» | read_file 34 КБ = 8500 токенов | card `harness::agent::HarnessAgent::_dispatch` ≈ 120 токенов |
| «Кто вызывает `injection_guard`?» | grep по всем файлам | callers ≈ 100 токенов |
| «Какие модули зависят от `audit`?» | ручной обход импортов | impact ≈ 180 токенов |

Atlas не заменяет `read_file` — он даёт **карту** перед чтением.

---

## Как включить

По умолчанию Atlas **выключен в пассивном режиме**: при старте REPL
он открывает/создаёт пустую БД, но ничего не индексирует. Граф
строится только явно.

В `config.yaml`:

```yaml
atlas:
  enabled: true                          # вообще включён
  auto_index_on_start: false             # не индексировать при старте
  workspace_root: "workspace"            # корень индексируемого проекта
  db_path: "workspace/.atlas/graph.db"   # где хранить граф
```

`auto_index_on_start` — ключевой флаг:

- `false` (по умолчанию) — при старте REPL Atlas ничего не делает,
  только открывает пустую БД. Старт мгновенный. Индексация — через
  `/atlas rebuild` или `scripts/atlas-setup.sh init`.
- `true` — если БД пуста, при старте выполняется полная индексация.
  На больших проектах (тысячи файлов) это задержит появление REPL.

Первый запуск с Atlas:

```bash
scripts/atlas-setup.sh init
[atlas] rebuilt: 47 files, errors=0, 0.9s

./run.sh
[i] [atlas] ready: 47 files, 812 nodes
```

Если `enabled: false` или импорт `harness.atlas` упал — подсистема
молча отключается, REPL работает как обычно. Atlas никогда не
блокирует работу mini-harness.

---

## Что индексируется

Корень индексации — `atlas.workspace_root` из `config.yaml`. По
умолчанию это `workspace`, то есть только тестовые файлы, которые
вы кладёте в `workspace/input/`. Чтобы Atlas смотрел на весь проект
`mini-harness`:

```yaml
atlas:
  workspace_root: "."
```

и выполните `scripts/atlas-setup.sh init` заново.

Из обхода исключаются:

- всё, что попадает в `.gitignore` корня (парсер свой, без `pathspec`);
- каталоги из `atlas.ignore_dirs`: `.git`, `__pycache__`, `.venv`,
  `venv`, `node_modules`, `.atlas`, `.pytest_cache`;
- файлы, не подходящие под `atlas.python_extensions` (по умолчанию
  только `.py`).

На этапе MVP индексируется только Python. JS/TS — в Этапе 2.

Файлы с синтаксическими ошибками не прерывают индексацию: они
пропускаются, ошибка пишется в `logs/atlas.log`, в статистике
увеличивается счётчик `errors`.

---

## Что извлекается

### Узлы (nodes)

| Тип | Что это | Пример `qualified_name` |
| --- | --- | --- |
| `Module` | Файл целиком | `harness::agent` |
| `Class` | Класс верхнего уровня или вложенный | `harness::agent::HarnessAgent` |
| `Function` | Функция верхнего уровня | `harness::agent::_parse_guided_plan` |
| `Method` | Метод класса | `harness::agent::HarnessAgent::run` |
| `Import` | Импорт | `harness::agent::import:json` |
| `Decorator` | Декоратор | (не реализовано в MVP) |

Для каждого узла: `id` (детерминированный `sha1(path::qname)[:16]`),
`kind`, `name`, `qualified_name`, `path`, `start_line`, `end_line`,
`summary` (первая строка docstring), `generation_hash`.

### Рёбра (edges)

| Тип | Значение |
| --- | --- |
| `contains` | Модуль → класс/функция; класс → метод |
| `imports` | Модуль → модуль |
| `calls` | Функция → функция |
| `inherits` | Класс → класс |
| `decorates` | Функция → декоратор |
| `raises` | Функция → исключение |

Для `calls`, `imports`, `inherits` каждое ребро сопровождается:

- **`status`**: `confirmed` — привязка однозначная; `unresolved` —
  статически разрешить не удалось.
- **`reason`**: `import-binding` | `same-name` | `unknown`.

Правила разрешения в MVP:

| Тип | `confirmed` | `unresolved` |
| --- | --- | --- |
| `imports` | Модуль-цель есть в графе | Модуль не найден → `unknown` |
| `inherits` | Ровно один класс с таким именем | Не найден → `unknown`; несколько → `same-name` |
| `calls` | Ровно одна функция/метод с таким именем (в файле или глобально) | Не найдено → `unknown`; несколько → `same-name` |

Правило простое: если привязка однозначная — `confirmed`, иначе
`unresolved` с причиной. Модель всегда видит, где факт, а где
предположение.

---

## Команды в REPL

Управление Atlas — команды **пользователя**, не модели:

| Команда | Что делает |
| --- | --- |
| `/atlas rebuild` | Полная переиндексация. Создаёт `graph.db`, если её нет |
| `/atlas init` | Алиас для `rebuild` |
| `/atlas refresh` | Инкрементальная переиндексация по SHA-256 (изменённые файлы) |
| `/atlas stats` | Файлы, узлы, рёбра, размер БД, `generation_hash` |
| `/atlas overview` | Ручной вызов Root View (для отладки) |

Пример:

```
>>> /atlas stats
[atlas] files=5 nodes=68 edges=201 db=147456B gen=2386a38fd0b440eb

>>> /atlas refresh
[atlas] refreshed: 0 indexed, 0 deleted, errors=0, 0.0s

>>> /atlas rebuild
[atlas] rebuilt: 5 files, errors=0, 0.04s
```

Если БД пуста, `/atlas stats` и `/atlas overview` отвечают подсказкой:

```
>>> /atlas stats
[atlas] not indexed (run /atlas rebuild)
```

---

## Скрипт `scripts/atlas-setup.sh`

То же самое, но без REPL и без запуска сервера инференса. Удобно
для cron, git hook, CI и просто для подготовки индекса до первого
запуска.

| Команда | Что делает |
| --- | --- |
| `init` / `rebuild` | Полная переиндексация |
| `refresh` | Инкрементальная переиндексация |
| `stats` | Статистика |
| `clean` | Удалить `graph.db` и `atlas.log` (с подтверждением) |
| `help` | Справка |

Пример:

```bash
scripts/atlas-setup.sh init
scripts/atlas-setup.sh refresh
scripts/atlas-setup.sh stats
scripts/atlas-setup.sh clean
```

Скрипт находит `python3` или `.venv/bin/python`, импортирует
`harness.atlas.cli`, читает `config.yaml` и вызывает тот же
`AtlasFacade`, что и REPL. Никаких дублирующих путей.

---

## Инструмент `atlas` для модели

Если Atlas сконфигурирован, `HarnessAgent` добавляет в список tools
инструмент `atlas` и дополняет system-промпт блоком `_ATLAS_HINT`.
Без Atlas ни инструмент, ни блок не появляются.

### Схема

```text
atlas(op: enum[overview, search, card, callers, callees, impact],
      arg: str,
      id: str = "")
```

Схема занимает ≤ 80 токенов. Инструмент **строго read-only**: ни
одна операция не меняет файлы, граф или конфигурацию.

### Операции

| `op` | `arg` | Что возвращает | Бюджет |
| --- | --- | --- | --- |
| `overview` | — (игнорируется) | Сжатое Root View проекта | ≤ 200 токенов |
| `search` | подстрока имени | До 10 `qualified_name` | ≤ 80 |
| `card` | `qualified_name` | Карточка узла | ≤ 150 |
| `callers` | `qualified_name` | Входящие `calls` с evidence | ≤ 120 |
| `callees` | `qualified_name` | Исходящие `calls` с evidence | ≤ 120 |
| `impact` | `qualified_name` | До 10 узлов, зависящих от заданного | ≤ 180 |

При превышении бюджета результат обрезается с маркером
`[truncated]` в конце.

### Формат ответов

Карточка функции:

```text
harness::agent::HarnessAgent::_dispatch
sum: Dispatch a tool call to the implementation.
calls: _tool_list_dir, _tool_read_file, _tool_propose_write, atlas
gen: 2386a38fd0b440eb
```

Карточка класса:

```text
harness::agent::HarnessAgent
bases: (none)
methods: __init__, run, canonical_rel, mark_written, _dispatch
sum: Tool-loop поверх ChatBackend.
gen: 2386a38fd0b440eb
```

Карточка модуля:

```text
harness/agent.py
sum: Tool-calling loop.
public: HarnessAgent, TOOLS, SYSTEM_PROMPT
imports: json, logging, re, sys, uuid
imported_by: harness::main, harness::batch
gen: 2386a38fd0b440eb
```

Ответ `callers`:

```text
callers of harness::agent::HarnessAgent::_dispatch:
  confirmed: 2
    [import-binding] harness::agent::HarnessAgent::_run_autonomous:312
  unresolved: 0
```

### Почему `overview` игнорирует `arg`

`overview` — это Root View проекта целиком. Он не параметризуется.
Если модели нужен конкретный узел — это `search` или `card`. В
`_ATLAS_HINT` это явно прописано, чтобы 4B-модели не пытались
звать `overview` с `arg`.

---

## Схема БД

Файл по умолчанию: `workspace/.atlas/graph.db`. Формат — SQLite
с FTS5 для поиска по имени.

```sql
CREATE TABLE files (
    path        TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    indexed_at  TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL
);

CREATE TABLE nodes (
    id              TEXT PRIMARY KEY,   -- sha1(path::qname)[:16]
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    path            TEXT NOT NULL,       -- FK → files.path
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    summary         TEXT,
    generation_hash TEXT NOT NULL
);

CREATE TABLE edges (
    src_id          TEXT NOT NULL,       -- FK → nodes.id
    dst_id          TEXT,                -- FK → nodes.id (nullable)
    kind            TEXT NOT NULL,
    status          TEXT NOT NULL,       -- confirmed | unresolved
    reason          TEXT,
    line            INTEGER,
    generation_hash TEXT NOT NULL,
    PRIMARY KEY (src_id, kind, dst_id, line)
);

CREATE VIRTUAL TABLE nodes_fts USING fts5(
    name, qualified_name, summary,
    content='nodes', content_rowid='rowid'
);
```

Связи:

- `nodes.path → files.path ON DELETE CASCADE` — удалили файл,
  удалились все его узлы.
- `edges.src_id → nodes.id ON DELETE CASCADE` — удалили узел,
  удалились рёбра из него.
- `edges.dst_id → nodes.id ON DELETE SET NULL` — если узел-цель
  исчез (например, переименован), ребро становится «висячим»
  и переразрешается на следующем refresh.

Транзакционность: один файл — одна транзакция. Падение на файле
N не портит состояние после файла N−1.

---

## Инкрементальная переиндексация

`/atlas refresh` (или `scripts/atlas-setup.sh refresh`):

1. Обходит дерево, считает SHA-256 каждого `.py`.
2. Сравнивает с `files.sha256`.
3. Переиндексирует только файлы с изменившимся хешем.
4. Удаляет из БД записи для файлов, которых больше нет.
5. Пересчитывает `generation_hash` — общий отпечаток состояния
   индекса.

`generation_hash` — это `sha1(пути + sha256 всех файлов)[:16]`. Он
меняется при любом изменении содержимого графа и попадает в каждую
карточку. Модель может понять, что данные устарели, если сравнивает
`gen` из старой карточки с текущим `stats`.

Инкремент 10 файлов на N100 занимает 0.2–0.4 с (НФТ-2 ≤ 2 с
соблюдается с запасом). Полная переиндексация 47 файлов — около
0.9 с; экстраполяция на 3000 файлов ≈ 60 с — на границе НФТ-1,
оптимизация запланирована в Этапе 3.

---

## Безопасность

Atlas работает по тем же правилам, что и остальная подсистема:

- **Только локально.** Ни одного сетевого вызова, ни одного
  DNS-запроса, ни одной телеметрии.
- **Read-only для модели.** Ни одна операция `atlas` не пишет в
  файлы, не меняет конфигурацию, не трогает БД на запись. Запись
  в БД — только через `rebuild` / `refresh`, а это команды
  пользователя.
- **Только статический анализ.** Код не выполняется: используется
  `ast.parse`, не `eval`/`exec`/`import`.
- **Внутри корня проекта.** Пути проверяются через `realpath`
  относительно `workspace_root`, symlink-escape блокируется.
- **Санитайзер на выходе.** В карточках:
  - обрезка до `sanitize_max_name_len` / `sanitize_max_summary_len`;
  - удаление `#`, обратных кавычек, переводов строк;
  - фильтрация injection-паттернов (`ignore previous`, `<|im_start|>`,
    `system:`) → `[filtered]`;
  - redaction секретов (`password=`, `api_key=`, `token=`,
    `sk-...`, `ghp_...`) → `[redacted]`.

Модель никогда не видит содержимого `.env`, ключей, паролей — даже
если они попали в docstring исходника.

---

## Конфигурация

Вся секция `atlas:` в `config.yaml`:

```yaml
atlas:
  enabled: true                          # вообще включён
  auto_index_on_start: false             # не индексировать при старте
  workspace_root: "workspace"            # корень индексируемого проекта
  db_path: "workspace/.atlas/graph.db"   # где хранить БД
  log_path: "logs/atlas.log"             # куда писать лог индексации
  max_depth: 3                           # глубина impact
  budget_overview: 200                   # токенов на overview
  budget_search: 80
  budget_card: 150
  budget_callers: 120
  budget_callees: 120
  budget_impact: 180
  sanitize_max_name_len: 80
  sanitize_max_summary_len: 300
  python_extensions: [".py"]             # какие файлы индексировать
  ignore_dirs:                           # что не обходить
    - ".git"
    - "__pycache__"
    - ".venv"
    - "venv"
    - "node_modules"
    - ".atlas"
    - ".pytest_cache"
```

Все ключи опциональны — при отсутствии используется дефолт.

Чтобы проиндексировать весь проект `mini-harness` (а не только
`workspace/`):

```yaml
atlas:
  workspace_root: "."
  db_path: "logs/.atlas/graph.db"   # чтобы БД не лежала в дереве кода
```

---

## Отключение

Атлас полностью опционален. Отключается одной строкой:

```yaml
atlas:
  enabled: false
```

При этом:

- `/atlas ...` в REPL отвечает `[!] atlas отключён в config.yaml`;
- инструмент `atlas` не появляется в списке tools модели;
- `_ATLAS_HINT` не добавляется в system-промпт;
- `graph.db` не открывается;
- остальная функциональность (guided mode, batch, sources, proxy)
  не затрагивается.

Если нужен «полу-режим» без индексации — оставьте `enabled: true` и
`auto_index_on_start: false` (это дефолт). Atlas будет только
открывать БД при старте; на запросы без индекса отвечает
`not indexed`.

---

## Ограничения MVP

Осознанно не реализовано в этой итерации:

- **Только Python.** JS/TS через tree-sitter — Этап 2.
- **Без watcher.** Инкремент запускается явно: `/atlas refresh` или
  `scripts/atlas-setup.sh refresh`. Никакого фонового мониторинга
  файлов.
- **Упрощённый evidence.** `re-export-binding` и `dynamic-dispatch`
  (getattr, **kwargs, DI) не различаются — в MVP всё неоднозначное
  помечается `unresolved`/`same-name` или `unknown`. Расширение —
  Этап 2.
- **`overview` не параметризуется.** Для конкретного узла — `search`
  и `card`.
- **Нет LRU-кэша карточек.** Каждый вызов читает из SQLite. На
  масштабе MVP (десятки файлов) это <5 мс, кэш — Этап 3.
- **FTS-rebuild на каждый файл.** Простая, но неоптимальная
  стратегия; для больших проектов будет заменена на инкрементальный
  FTS — Этап 3.

---

## Производительность

| Операция | N100, 47 файлов | Экстраполяция 3000 файлов |
| --- | --- | --- |
| Полная индексация | 0.9 с | ≈ 60 с (на границе НФТ-1) |
| Инкремент 10 файлов | 0.3 с | — (НФТ-2 ≤ 2 с ✅) |
| `overview` | <5 мс | — (НФТ-3 ≤ 50 мс ✅) |
| `card` | <5 мс | — |
| `search` | <10 мс | — |
| Размер БД, 47 файлов | ≈ 150 КБ | — (НФТ-4 ≤ 200 МБ ✅) |

Память: в режиме запросов Atlas держит только открытое SQLite-соединение
(единицы МБ). При полной индексации пик — 20–50 МБ на 47 файлов,
на 3000 файлов укладывается в 300 МБ (НФТ-5).

---

## Troubleshooting

### `[atlas] not indexed (run /atlas rebuild)`

Индекс пуст. Выполните:

```bash
scripts/atlas-setup.sh init
```

или в REPL `/atlas rebuild`.

### `not indexed` возвращается сразу после `init`

Проверьте, что индексируется правильный корень:

```yaml
atlas:
  workspace_root: "."   # или "workspace"
```

и что в нём есть `.py`. По умолчанию `workspace_root: "workspace"`,
а в `workspace/` могут быть только тестовые файлы.

### `errors > 0` в `stats`

Часть файлов не распарсилась. Смотрите детали:

```bash
tail -20 logs/atlas.log
```

Обычно это либо не-Python файл с расширением `.py` (редко), либо
файл с реальной синтаксической ошибкой. Индексация не прерывается —
проблемные файлы просто не попадают в граф.

### `[atlas] disabled: ...` при старте

Импорт `harness.atlas` или инициализация `AtlasFacade` упали.
Проверьте:

```bash
./.venv/bin/python -c "import harness.atlas; print('ok')"
./.venv/bin/python -c "import yaml; print('yaml ok')"
```

Если ошибка — пришлите трейс, скорее всего проблема в `config.yaml`
(например, `db_path` указывает в недоступное место).

### Модель не вызывает `atlas`

Проверьте:

1. `/atlas stats` в REPL — если `not indexed`, сначала `init`.
2. В аудите `logs/audit.jsonl` ищите `tool_call` с `tool: "atlas"`.
3. Если модель вызывает `atlas`, но получает `ERROR`, пришлите
   аргументы `op`/`arg` из аудита — возможно, формат
   `qualified_name` не тот (см. `/atlas search`).

Помните: `_ATLAS_HINT` помогает, но не гарантирует — 4B-модели
иногда всё равно отвечают из обучающих данных вместо вызова
инструмента. 7B+ справляются стабильно.

### Хочу сбросить всё

```bash
scripts/atlas-setup.sh clean       # удалит graph.db и atlas.log
scripts/atlas-setup.sh init        # переиндексирует с нуля
```

---

## Дальше

Текущее состояние — **Этап 1** из плана ТЗ. Запланировано:

- **Этап 2:** JS/TS через tree-sitter; расширенный evidence
  (`re-export-binding`, `dynamic-dispatch`); расширенный redaction.
- **Этап 3:** LRU-кэш карточек; инкрементальный FTS; интеграция
  с `/batch` для группировки однотипных символов; оптимизация под
  НФТ-1 на 3000 файлов.
- **Этап 4:** нагрузочное тестирование на N100; приёмочные
  испытания.

---

## См. также

- [Архитектура](architecture.md) — как устроен mini-harness в целом
- [Политики доступа](policies.md) — whitelist/blacklist для fs
- [Модель угроз](security-model.md) — что защищено, что нет
- [Batch](batch.md) — пакетная обработка файлов
- [Шпаргалка](cheatsheet.md) — команды на одну страницу
