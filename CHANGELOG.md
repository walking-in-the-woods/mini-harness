# Changelog

Все значимые изменения проекта документируются в этом файле.

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **Project Atlas — MVP (Python-only).** Подсистема семантического
  графа проекта на SQLite + read-only инструмент `atlas(op, arg, id)`
  для LLM-агента. Цель — снизить объём контекста при навигации по
  коду не менее чем на 90% и убрать галлюцинации о структуре.
  Полностью локально, без сети, без новых зависимостей
  (`ast`, `sqlite3`, `hashlib`).
  - **`harness/atlas/`** — новый пакет:
    - `config.py` — `AtlasConfig` из секции `atlas:` в `config.yaml`.
    - `gitignore.py` — мини-парсер `.gitignore` → regex (без
      `pathspec`).
    - `indexer/walker.py` — обход дерева с учётом `.gitignore` и
      `ignore_dirs`.
    - `indexer/python_ast.py` — извлечение узлов и рёбер из `ast`.
    - `evidence/resolver.py` — разрешение рёбер до `dst_id` с
      `status` (`confirmed` / `unresolved`) и `reason`.
    - `store/schema.py` — DDL: `meta`, `files`, `nodes`, `edges`,
      `nodes_fts` (FTS5).
    - `store/queries.py` — get, find, search, incoming/outgoing,
      stats.
    - `formatter/budget.py` — `estimate_tokens`, `enforce_budget`.
    - `formatter/capsule.py` — overview / search / card / callers /
      callees / impact с жёсткими бюджетами.
    - `security/sanitizer.py` — обрезка, фильтрация injection-
      паттернов, redaction секретов.
    - `lifecycle/manager.py` — полная и инкрементальная индексация
      по SHA-256, транзакция на файл.
    - `facade.py` — `AtlasFacade.call(op, arg, id)`,
      `rebuild/refresh/stats/overview`, `ensure_indexed`.
    - `cli.py` — CLI для `scripts/atlas-setup.sh`.
  - **`scripts/atlas-setup.sh`** — управление графом без REPL и без
    сервера инференса. Команды: `init` / `rebuild`, `refresh`,
    `stats`, `clean`, `help`.
  - **`/atlas rebuild|init|refresh|stats|overview`** — команды REPL.
    Обрабатываются в `main.py`, не уходят в модель.
  - **Инструмент `atlas` для модели** — схема ≤ 80 токенов,
    read-only, 6 операций (`overview`, `search`, `card`, `callers`,
    `callees`, `impact`). Добавляется в `HarnessAgent.tools` только
    если Atlas сконфигурирован. В `system`-промпт при этом
    добавляется `_ATLAS_HINT` с инструкцией, когда и как вызывать
    `atlas`.
  - **`config.yaml → atlas:`** — новая секция с дефолтами.
    Ключевое: `enabled: true` (подсистема включена) и
    `auto_index_on_start: false` (по умолчанию не индексировать при
    старте REPL — старт мгновенный).
  - **`docs/atlas.md`** — полная документация подсистемы: зачем,
    как включить, формат узлов/рёбер, команды, скрипт, схема БД,
    инкрементальная переиндексация, безопасность, ограничения MVP,
    производительность, troubleshooting.
  - **`tests/atlas/`** — 71 тест: gitignore, схема БД, AST-парсер,
    resolver, sanitizer, budget, lifecycle (rebuild / refresh /
    idempotency / generation hash), capsule / facade, интеграция
    с `HarnessAgent._dispatch`.
  - Ссылки на `docs/atlas.md` добавлены в `README.md`,
    `docs/README.md`, `docs/index.md`, `docs/cheatsheet.md`.
    Раздел про `atlas-setup.sh` добавлен в `scripts/README.md`.

### Changed

- **`HarnessAgent.__init__`** — новый опциональный параметр
  `atlas: "AtlasFacade | None" = None`. Если передан — в список
  tools добавляется `atlas`, в system-промпт — `_ATLAS_HINT`.
  Без параметра поведение идентично прежнему.
- **`HarnessAgent._tool_schemas()`** — новый метод. Собирает список
  схем tools с учётом Atlas (через локальный импорт
  `atlas_tool_schema`, чтобы не тащить пакет при `atlas=None`).
- **`HarnessAgent._system_prompt()`** — новый метод. Возвращает
  `SYSTEM_PROMPT` или `SYSTEM_PROMPT + _ATLAS_HINT`. Вызывается
  из `_run_autonomous`.
- **`LlamaCppBackend._normalize_response`** — сохраняет OpenAI-поля
  `id` и `type` в tool_calls; если `id` не пришёл — генерирует.
  Нужно для `llama-server --jinja`.
- **`_extract_tool_calls_from_content`** — возвращает tool_calls
  в полном OpenAI-формате: `id` (генерируется), `type="function"`,
  `function: {name, arguments}`. Раньше был «голый» `{function: ...}`.
- **`harness/main.py`** — `cfg["atlas"]` в `load_runtime_config`;
  `_init_atlas()` (создаёт фасад, ошибки не фатальны);
  `_handle_atlas_command()` (`/atlas ...`);
  `atlas=atlas_facade` в оба `HarnessAgent(...)`; `atlas_facade.close()`
  на выходе; в баннер добавлена строка про `/atlas`.
- **`config.yaml`** — новая секция `atlas:` (16 ключей).

### Fixed

- **llama-server с `--jinja` падал на втором раунде tool-loop** с
  `Failed to parse messages: Missing tool call type`. Причина:
  `assistant.tool_calls` и `role=tool` в истории не содержали
  `id`/`type`/`tool_call_id`, а jinja-шаблон llama.cpp требует их
  для сопоставления вызова с результатом. Теперь `_run_autonomous`
  нормализует `tool_calls` в полный OpenAI-формат перед помещением
  в `messages` и добавляет `tool_call_id` в tool-сообщение.
  Ollama-путь не затронут — формат с `id`/`type` она принимает.
- **`atlas(op="search")` возвращал `path::name`, а `card` искал по
  `qualified_name`.** Модель получала от `search` строку
  `input/counter.py::Counter`, передавала её в `card`, получала
  `node not found` и зацикливалась. Теперь `search` возвращает
  `qualified_name` — ровно тот формат, который принимают `card`,
  `callers`, `callees`, `impact`.
- **`PythonAstParser` вычислял `path` из `qualified_name`** вместо
  реального пути файла. Для функций/методов это давало `src.py`
  вместо `src/main.py`, для вложенных классов — `src/main/MyClass.py`.
  Нарушался `FOREIGN KEY nodes.path → files.path`, `INSERT INTO
  nodes` падал, вся индексация разваливалась каскадом. Теперь
  `RawNode.path` прокидывается явно из парсера как реальный путь
  файла.
- **`EvidenceResolver._lookup_module`** использовал `LIKE '%::module'`
  для поиска модуля, что давало ложные срабатывания и промахи на
  простых qname. Заменено на точное сравнение `qualified_name ==
  module_name.replace(".", "::")`.

### Планируется

- **Atlas, этап 2:** JS/TS через tree-sitter; расширенный evidence
  (`re-export-binding`, `dynamic-dispatch` через `getattr`/`**kwargs`/
  DI); расширенный redaction секретов.
- **Atlas, этап 3:** LRU-кэш карточек; инкрементальный FTS вместо
  `INSERT INTO nodes_fts('rebuild')` на каждый файл; интеграция с
  `/batch` (группировка однотипных символов через Atlas); оптимизация
  полной индексации под НФТ-1 (3000 файлов ≤ 60 с).
- **Atlas, этап 4:** нагрузочное тестирование на N100; приёмочные
  испытания на реальном проекте Заказчика.
- Smoke-тест для llama.cpp в CI (требует живого `llama-server`,
  сейчас только юнит-тесты).
- Retry-транспорт для `LlamaCppBackend` для удалённых серверов.
- Overlap между чанками в docs-режиме (`overlap_bytes`).
- Разбиение класса на методы в code-режиме.
- Скользящее окно для очень больших файлов.

---

## [0.3.0] — 2026-09-24

Chunked-обработка длинных файлов. `/batch` теперь умеет
разбивать файл на чанки, обрабатывать каждый отдельно и
собирать результат. Отдельно для кода (`--mode=code`) и
для документов (`--mode=docs`).

### Added

- **`harness/processing/`** — новый пакет chunked-обработки:
  - `base.py` — `Processor` ABC, `Chunk`, `ValidationIssue`,
    `estimate_tokens`, `compute_budget_bytes`.
  - `config.py` — загрузка `config/processing.yaml`, fail-fast
    на неизвестных ключах.
  - `code.py` — `CodeProcessor`: AST-разбиение по top-level
    def/class, replace-block merge, валидация (ast_parseable,
    defs_preserved, bodies_nonempty, signature_args_preserved,
    no_new_top_level).
  - `docs.py` — `DocProcessor`: разбиение по заголовкам /
    параграфам / жёстко, конкатенация, валидация
    (headers_preserved, fences_balanced).
- **`config/processing.yaml`** — конфиг chunked-режима.
  Опционален, при отсутствии — дефолты.
- **`/batch ... --mode=code|docs --chunk`** — новые флаги.
- **`--on-failure`, `--on-merge-invalid`, `--on-insufficient-context`** —
  override настроек конфига из CLI.
- **Pre-flight в non-chunked режиме**: файл больше бюджета →
  `failed` с сообщением «Use --chunk». Раньше такой файл уходил
  в модель и там тихо ломался.
- **Partial-режим**: `<target>.failed/` с README и дампами
  проваленных чанков. Орнамент `NOT PROCESSED` вокруг
  необработанных фрагментов в code-режиме.
- **`INSUFFICIENT_CONTEXT`** — механизм честного отказа модели.
- **`scripts/llama-server.sh`** — единый запускатель
  llama-server для нескольких моделей. Команды: `list`,
  `start`, `use`, `stop`, `stop-all`, `logs`, `env`.
  Автоматический выбор свободного порта.
- **`scripts/README.md`** — документация по запуску моделей.
- **`docs/processing.md`** — формула бюджета, тюнинг, ограничения.
- **Тесты**: `test_processing.py`, `test_processing_code.py`,
  `test_batch_chunked.py` (~100 тестов).

### Changed

- **`BatchRunner.__init__`** принимает `mode`, `chunk_enabled`,
  `processing_config`, `overrides`.
- **`BatchItem`** расширен полями chunked-статуса.
- **`main.py:_handle_batch_command`** парсит новые флаги,
  валидирует overrides.
- **`load_runtime_config`** читает `config/processing.yaml`.

### Fixed

- **Pre-flight в non-chunked режиме.** Раньше файл больше
  контекста уходил в модель и падал тихо: либо HTTP 500 от
  llama.cpp, либо обрезанный вывод, либо structural loss,
  который не всегда ловился. Теперь `failed` до вызова модели.

---

## [0.2.0] — 2026-09-24

Абстракция бэкенда инференса. Проект работает как с Ollama,
так и с llama.cpp, переключаясь одной переменной в `.env`.

### Added

- **`harness/backends/`** — новый пакет:
  - `base.py` — `ChatBackend` ABC и `BackendError`.
  - `ollama_backend.py` — обёртка `ollama.Client`.
  - `llamacpp_backend.py` — HTTP-клиент к `llama-server`
    (`/v1/chat/completions`, `/health`, `/v1/models`).
  - `__init__.py` — фабрика `build_backend(cfg)`.
- **`docs/backends.md`** — документация по бэкендам.
- **`HARNESS_BACKEND`, `LLAMACPP_HOST`, `LLAMACPP_API_KEY`,
  `LLAMACPP_TIMEOUT`** — новые переменные `.env`.
- **Параметр `backend=` в `HarnessAgent.__init__`**.
- **Диагностика `_backend_diagnose`** при старте REPL.
- **`_check_backend_alive`** в smoke-тестах: отличает «модель
  слабая» (xfail) от «сервер недоступен» (fail).
- **Тесты**: `test_backends.py`, `test_agent_backend_integration.py`,
  `test_llamacpp_smoke.py`.

### Changed

- **`HarnessAgent`** использует `self.backend` вместо `self.client`.
  Параметр `client=` сохранён для обратной совместимости.
- **Событие аудита `ollama_error` → `backend_error`**.
- **`main.py:load_runtime_config`** читает новые переменные.
- **Баннер REPL** показывает бэкенд и адрес.
- **`run.sh`** проверяет сервер по выбранному бэкенду.
- **`.github/workflows/ci.yml`** покрывает `harness/backends/`.

### Removed

- **`self.client`** в `HarnessAgent`.

### Fixed

- **Tool-call аргументы от llama.cpp.** OpenAI отдаёт
  `function.arguments` JSON-строкой; бэкенд нормализует в dict.

---

## [0.1.0] — 2026-09-16

Первый публичный релиз.

### Added

- Файловый шлюз, подтверждение записи, read-after-write block.
- Защита от инъекций.
- Внешний прокси с whitelisted-маршрутами.
- JSONL-аудит.
- Tool-calling loop (guided mode + autonomous mode).
- Внешние источники (`/tree`, `/files`, `/dump`).
- Batch-режим с structural validation.
- REPL с командами `/sources`, `/tree`, `/files`, `/dump`,
  `/reload`, `/batch`, `/reset`, `/quit`.
- Документация в `docs/`.
- 9 юнит-тестов + 2 smoke, CI на Python 3.10/3.11/3.12.
