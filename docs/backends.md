# Бэкенды инференса

Harness умеет работать с двумя движками:

- **Ollama** (`ollama serve`) — нативный API, нативный tool calling,
  нативный `think=False`, нативный `keep_alive`.
- **llama.cpp** (`llama-server`) — OpenAI-совместимый endpoint
  `/v1/chat/completions`, встроенный Web UI, никаких Python-зависимостей
  кроме `httpx`.

Выбор — через `HARNESS_BACKEND` в `.env`. Значение по умолчанию —
`ollama`.

## Как переключаться

### На ollama

```env
HARNESS_BACKEND=ollama
HARNESS_MODEL=qwen2.5-coder:7b
OLLAMA_HOST=http://127.0.0.1:11434
HARNESS_NUM_CTX=2048
HARNESS_KEEP_ALIVE=5m
```

Убедитесь, что `ollama serve` запущен, а модель загружена
(`ollama pull qwen2.5-coder:7b`).

### На llama.cpp

```env
HARNESS_BACKEND=llamacpp
HARNESS_MODEL=llamacpp-local
LLAMACPP_HOST=http://127.0.0.1:8080
LLAMACPP_TIMEOUT=300
HARNESS_NUM_CTX=4096
```

`HARNESS_MODEL` — произвольная метка. `llama-server` игнорирует
поле `model` в запросе (сервер обслуживает одну модель, загруженную
при старте). Если оставить пусто, harness подставит `llamacpp-local`.

Запустите `llama-server` скриптом из инструкции:

```bash
~/Desktop/run-coder3b.sh     # порт 8080, ctx-size 4096
~/Desktop/run-coder7b.sh     # порт 8082, ctx-size 2048
```

Если используете второй скрипт — поправьте `LLAMACPP_HOST` на
`http://127.0.0.1:8082`.

## Что унифицировано, а что — нет

| Аспект | Ollama | llama.cpp |
| --- | --- | --- |
| Формат ответа | `{"message": {...}}` | `{"choices": [{"message": ...}]}` |
| Нормализация | бэкенд оборачивает нативный ответ | бэкенд разворачивает OpenAI-формат |
| Аргументы tool_call | dict (Python) | **JSON-строка** — бэкенд парсит в dict |
| `think=False` | нативный флаг | **игнорируется** (chat-template решает) |
| `keep_alive` | нативный | **игнорируется** (модель всегда в RAM) |
| `num_ctx` | `options.num_ctx` | **игнорируется** (флаг `--ctx-size` сервера) |
| `num_predict` | `options.num_predict` | `max_tokens` |
| `temperature` | `options.temperature` | `temperature` |
| Health-check | `client.list()` | GET `/health` |
| Список моделей | `client.list()` | GET `/v1/models` |

Все различия скрыты внутри `harness/backends/`. `HarnessAgent`
видит единый интерфейс `ChatBackend.chat(...)` и единый формат
ответа — код guided mode, autonomous tool-loop, retry по reasoning
и batch не знает, какой бэкенд работает под капотом.

## Что важно знать про llama.cpp

### Контекст задаётся на старте сервера

`llama-server --ctx-size 4096` — это размер KV-cache, выделяемого
при загрузке модели. Изменить его в рантайме нельзя. `HARNESS_NUM_CTX`
в `.env` **не передаётся** на сервер, но используется harness'ом для
обрезки источника в guided mode (`max_chars = NUM_CTX * 3`).

Практика: держите `HARNESS_NUM_CTX` **равным или меньшим**
`--ctx-size` на сервере. Если в `.env` стоит 4096, а сервер стартовал
с `--ctx-size 2048` — вы обрежете источник позже, чем нужно, и
получите ошибку переполнения контекста от сервера.

### Модель всегда в памяти

У llama.cpp нет аналога `keep_alive`. Модель остаётся загруженной,
пока работает сервер. Чтобы освободить RAM — остановите сервер
(`Ctrl+C` в его терминале).

### Reasoning-фазы нет

`llama-server` не поддерживает параметр `think`. Если ваша модель
умеет рассуждать (DeepSeek-R1-Distill и родственные) — она будет
рассуждать, если так задумано в её chat-template. Отключить это
из harness'а нельзя.

Для задач, где reasoning-leakage критичен, используйте модели без
встроенной reasoning-фазы: `Qwen2.5-Coder-*`, `Llama-3.2-*`.

### Tool calling требует `--jinja`

В текущей сборке `llama.cpp` (проверено на N100 + Qwen2.5-Coder-3B)
`--jinja` иногда приводит к галлюцинации несуществующего tool
`get_info`. Скрипты из инструкции используют `--chat-template chatml`,
что даёт стабильный вывод, но tool calls могут приходить **текстом
в content** вместо `tool_calls` в структуре.

Harness это переносит: в `agent.py` есть fallback
`_extract_tool_calls_from_content`, который вылавливает JSON-объекты
вида `{"name": "read_file", "arguments": {...}}` из текста и
превращает их в tool calls. Работает неидеально (если модель
выводит JSON в середине фразы — не подхватит), но для типичных
tool-calling моделей достаточно.

Если хотите включить нативный tool calling — добавьте `--jinja`
в скрипт запуска `llama-server` и протестируйте. При галлюцинациях
вернитесь к `--chat-template chatml`.

### Таймаут

На N100+7B Q4 генерация идёт 2–4 t/s. Ответ на 1024 токена —
4–8 минут. Дефолтный `LLAMACPP_TIMEOUT=300` секунд — разумный
минимум. Уменьшать не рекомендуется: обрыв HTTP-соединения посреди
генерации оставит сервер в неопределённом состоянии.

## Диагностика

### Проверить, что сервер отвечает

```bash
# ollama
curl -sf "${OLLAMA_HOST:-http://127.0.0.1:11434}/api/tags" | head -c 200

# llama.cpp
curl -sf "http://127.0.0.1:8080/health"
```

### Посмотреть, какая модель загружена

```bash
# ollama
ollama list

# llama.cpp — модель указана в скрипте запуска
grep MODEL ~/Desktop/run-coder3b.sh
```

### Посмотреть, что harness видит

При старте REPL печатается баннер:

```sh
============================================================
  Mini Harness — локальный ассистент без контейнеров

  backend:      llamacpp
  model:        llamacpp-local
  host:         http://127.0.0.1:8080
  num_ctx:      4096
  ...
```

Если `backend:` не совпадает с ожидаемым — проверьте `.env`,
поле `HARNESS_BACKEND`. Регистр значения не важен, но пробелы
в начале строки — важны.

### Включить отладку бэкенда

```bash
# Ollama
OLLAMA_DEBUG=1 ./run.sh

# llama.cpp — смотреть лог сервера в его терминале
```

## Написать свой бэкенд

Интерфейс минимален:

```python
from harness.backends.base import ChatBackend

class MyBackend(ChatBackend):
    @property
    def name(self) -> str:
        return "mybackend"

    def chat(self, *, model, messages, tools=None,
             temperature=0.1, num_predict=1024,
             num_ctx=2048, keep_alive=None, think=False) -> dict:
        # вернуть {"message": {"content": str, "tool_calls": list}}
        ...

    def health(self) -> bool:
        ...
```

Зарегистрировать — в `harness/backends/__init__.py:build_backend`.

Что важно помнить:

1. **Аргументы tool_call — всегда dict.** Если ваш транспорт отдаёт
   JSON-строкой, парсите до возврата.
2. **Health не должен бросать.** Любое исключение внутри — `False`.
3. **Ошибки сети — исключения.** `HarnessAgent` ловит их и логирует
   как `backend_error` в аудит.
