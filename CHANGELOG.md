# Changelog

Все значимые изменения проекта документируются в этом файле.

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Планируется

- Smoke-тест для llama.cpp (`tests/smoke/test_llamacpp_smoke.py`) —
  проверка `/health`, `/v1/models` и минимального chat-запроса.
  Существующий smoke требует `ollama.show()` и не подходит для
  llama-server.
- Retry-транспорт для `LlamaCppBackend` на случай обрыва соединения
  с удалённым сервером инференса (для localhost не нужно).
- Унификация вывода `backend_error` в REPL: сейчас показывается
  `[backend error] <raw exception>`, хочется человекочитаемое
  сообщение с подсказкой.

---

## [0.2.0] — 2026-09-24

Абстракция бэкенда инференса. Проект теперь работает как с
Ollama, так и с llama.cpp, переключаясь одной переменной в `.env`.
Логика guided mode, autonomous tool-loop, batch и все политики
безопасности не изменились — они не зависят от движка инференса.

### Added

- **`harness/backends/`** — новый пакет, абстрагирующий клиента
  инференса:
  - `base.py` — `ChatBackend` (ABC) и `BackendError`.
    Единый контракт: `chat(...) -> {"message": {"content", "tool_calls"}}`,
    `health() -> bool`, `list_models() -> list[str]`.
  - `ollama_backend.py` — обёртка вокруг `ollama.Client`.
    Сохраняет нативные `think`, `keep_alive`, `options.num_ctx`.
  - `llamacpp_backend.py` — HTTP-клиент к `llama-server`
    (`/v1/chat/completions`, `/health`, `/v1/models`).
    Нормализует OpenAI-ответ в формат Ollama. Аргументы tool_call
    (`function.arguments`) парсит из JSON-строки в dict.
  - `__init__.py` — фабрика `build_backend(cfg)`.
- **`docs/backends.md`** — документация по бэкендам: выбор, различия,
  подводные камни llama.cpp, диагностика, инструкция по написанию
  своего бэкенда.
- **Новые переменные в `.env`:**
  - `HARNESS_BACKEND` — `ollama` (по умолчанию) | `llamacpp`.
  - `LLAMACPP_HOST` — адрес `llama-server` (дефолт
    `http://127.0.0.1:8080`).
  - `LLAMACPP_API_KEY` — токен Bearer, если сервер за
    reverse-proxy с auth (по умолчанию пусто).
  - `LLAMACPP_TIMEOUT` — таймаут HTTP-запроса в секундах
    (дефолт `300` — под медленную генерацию 7B Q4 на N100).
- **Параметр `backend=` в `HarnessAgent.__init__`** — явная
  подстановка готового бэкенда. Используется в `main.py` и в
  будущих тестах llama.cpp.
- **Диагностика `_backend_diagnose(backend, cfg)` в `main.py`** —
  информационная проверка сервера инференса при старте REPL.
  Не блокирует запуск. Подсказка зависит от бэкенда: для ollama
  ссылается на `ollama serve`, для llama.cpp — на
  `~/Desktop/run-coder*.sh`.

### Changed

- **`HarnessAgent`** использует `self.backend` (тип `ChatBackend`)
  вместо `self.client` (тип `ollama.Client`). Атрибут `self.client`
  удалён. Параметр `client=` конструктора сохранён для обратной
  совместимости — готовый объект оборачивается в
  `OllamaBackend.from_client(...)`.
- **`HarnessAgent._transform_text()`** и **`_run_autonomous()`**
  вызывают `self.backend.chat(...)` с плоскими именованными
  аргументами (`temperature=`, `num_predict=`, `num_ctx=`,
  `keep_alive=`, `think=`) вместо вложенного `options={...}`.
  Бэкенд сам решает, что поддерживается.
- **Событие аудита `ollama_error` переименовано в `backend_error`.**
  Добавлено поле `backend` со значением `"ollama"` или `"llamacpp"`.
  Старое имя удалено из `_DURABLE_EVENTS`; новое добавлено.
- **Строка ответа пользователю `[ollama error] ...` заменена на
  `[backend error] ...`** в autonomous mode.
- **Баннер REPL** теперь показывает бэкенд и адрес сервера
  (`backend:` и `host:`), а не только модель.
- **`main.py:load_runtime_config()`** читает новые переменные;
  `HARNESS_MODEL` становится необязательным при
  `HARNESS_BACKEND=llamacpp` (подставляется метка
  `llamacpp-local`, потому что `llama-server` игнорирует поле
  `model` в запросе).
- **`main.py:main()`** строит бэкенд **до** баннера и до создания
  `SourceRegistry` — при ошибке конфигурации фатальный выход
  происходит за миллисекунды.
- **`session_start` в аудите** содержит поле `backend`.
- **`run.sh`** проверяет сервер инференса по выбранному бэкенду:
  для `llamacpp` — `GET /health`, для `ollama` — `GET /api/tags`.
  Проверка по-прежнему информационная и не блокирует REPL.
- **`config.yaml`** — обновлены комментарии в шапке (описано
  отношение к бэкенду). Содержимое `fs:` и `proxy:` не изменилось.
- **`.github/workflows/ci.yml`** — `py_compile harness/*.py
  harness/backends/*.py`; import-check добавлены четыре модуля
  нового пакета.
- **`README.md`** — раздел «Какой бэкенд выбрать» с таблицей
  сценариев, обновлённые разделы «Настройка» и «Что делать при
  проблеме».
- **`docs/README.md`**, **`docs/index.md`** — добавлена ссылка на
  `docs/backends.md`.
- **`docs/troubleshooting.md`** — `[ollama error]` → `[backend error]`;
  добавлены секции по llama.cpp: unknown backend, HTTP 500,
  tool calls в тексте, модель не отвечает.
- **`docs/cheatsheet.md`** — раздел «Выбор бэкенда», обновлены
  команды проверки сервера, добавлен `LLAMACPP_TIMEOUT` в таблицу
  лимитов.

### Removed

- **`self.client` в `HarnessAgent`.** Публичный атрибут удалён.
  Внутри проекта обращений не было. Внешний код, если читал
  `agent.client`, должен использовать `agent.backend`.
- **`import ollama` в `harness/agent.py`.** Пакет `ollama` остаётся
  в `requirements.txt` (для `OllamaBackend`), но агент его больше
  не импортирует.

### Fixed

- **Tool-call аргументы от llama.cpp.** OpenAI-совместимый формат
  отдаёт `function.arguments` как JSON-строку, а Ollama — как
  готовый dict. Без нормализации `_parse_tool_call` получал бы
  строку вместо dict и все инструменты падали бы с
  `args.get(...) -> AttributeError`. Теперь нормализация
  выполняется в `LlamaCppBackend._normalize_response`.

### Security

Без изменений. Политики FS, `injection_guard`, `confirm`,
`audit`, `proxy` — не задеты.

`LlamaCppBackend` **не ходит на произвольный URL**: адрес берётся
из `LLAMACPP_HOST` в `.env`, хост не подставляется аргументами
модели. Тот же принцип, что и в `ApiProxy`: маршрут известен
заранее, модель влиять на него не может.

### Migration

Обновление с `0.1.x` до `0.2.0` — без действий для пользователей
с Ollama-бэкендом:

1. Скачать новую версию, `./run.sh` поставит зависимости (список
   не изменился).
2. Если `.env` уже существует — `HARNESS_BACKEND` не задан, будет
   использовано `ollama` по умолчанию.
3. Если хочется переключиться на llama.cpp — добавить в `.env`
   `HARNESS_BACKEND=llamacpp` и `LLAMACPP_HOST=...`.
4. **Обратить внимание:** в старых `logs/audit.jsonl` остались
   записи с событием `ollama_error`. Новые записи будут
   `backend_error`. Если у вас есть внешние скрипты, парсящие
   аудит, — обновите их.

Внешний код, использующий `HarnessAgent` как библиотеку:

- Если вы передавали `client=<ollama.Client>` — работает
  без изменений.
- Если вы читали `agent.client` — заменить на `agent.backend`.
  Для ollama-специфичных операций — `agent.backend._client`
  (приватный, но стабильный для `OllamaBackend`).
- Если вы подписывались на события аудита — добавить обработку
  `backend_error` рядом с прежним `ollama_error` (или вместо).

---

## [0.1.0] — 2026-09-16

Первый публичный релиз.

### Added

- **Файловый шлюз** `harness/fs_guard.py`: whitelist, blacklist,
  writable, защита от path traversal, symlink escape, NFKC-гомоглифов,
  null-byte. Собственная реализация glob→regex с поддержкой `**`.
- **Подтверждение записи** `harness/confirm.py`: nonce, diff, атомарная
  запись через `mkstemp` + `os.replace`, `chmod 0o644`.
- **Read-after-write block**: файл, записанный в текущей сессии,
  нельзя прочитать в той же сессии.
- **Защита от инъекций** `harness/injection_guard.py`: нейтрализация
  XML-тегов ролей, тройных бэктиков, невидимых Unicode-символов,
  гомоглифов. Эвристики на пользовательский ввод.
- **Внешний прокси** `harness/proxy.py`: whitelisted-маршруты из
  `config.yaml`, `follow_redirects=False`, лимиты на request/response.
- **Аудит** `harness/audit.py`: JSONL, `fsync` для критичных событий.
- **Tool-calling loop** `harness/agent.py`: guided mode (модель без
  tools, harness сам читает/пишет) и autonomous mode (модель вызывает
  tools через tool calling).
- **Guided mode**: детектор reasoning-leakage с одним retry и жёстким
  отказом при повторном срабатывании. Пользовательские prompt-файлы
  для кастомных инструкций.
- **Внешние источники** `harness/sources.py`: `/tree`, `/files`,
  `/dump` для read-only корней вне workspace.
- **Batch-режим** `harness/batch.py`: обработка директории одним
  prompt-файлом, skip-and-continue, структурная валидация
  (AST для `.py`, brace balance для C-like, line preservation для
  всех).
- **REPL** `harness/main.py`: команды `/sources`, `/tree`, `/files`,
  `/dump`, `/reload`, `/batch`, `/reset`, `/quit`.
- **Документация** в `docs/`: архитектура, модель угроз, политики,
  prompt-файлы, batch, диагностика, шпаргалка, эксперименты.
- **Тесты**: 9 файлов юнит-тестов + 2 smoke. CI на Python
  3.10/3.11/3.12.
  