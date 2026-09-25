# Changelog

Все значимые изменения проекта документируются в этом файле.

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Планируется

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
