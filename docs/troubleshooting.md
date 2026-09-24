# Диагностика

## `ensurepip is not available`

**Симптом:** `run.sh` падает с сообщением

```text
The virtual environment was not created successfully because ensurepip is not
available.  On Debian/Ubuntu systems, you need to install the python3-venv
package using the following command.
```

**Причина:** на Debian/Ubuntu пакет `python3-venv` не входит в базовую
поставку Python. Без него `python -m venv` создаёт директорию, но не
может развернуть в ней `pip` — модуль `ensurepip` отсутствует.

**Решение:**

```bash
sudo apt install -y python3.12-venv
rm -rf .venv
./run.sh
```

Если `python3.12-venv` недоступен, посмотрите список:

```bash
apt-cache search python3-venv
```

**Важно:** `.venv` после неудачной попытки нужно удалить вручную.
Свежая версия `run.sh` делает это автоматически (проверяет импорт
зависимостей, а не факт существования `.venv`).

## `run.sh` не запускается от root

**Симптом:** `run.sh` выходит с сообщением
«run.sh не должен запускаться от root».

**Решение:**

```bash
./run.sh
# Если уже что-то создано от root:
sudo chown -R "$USER:$USER" .venv workspace logs
```

## `ModuleNotFoundError: No module named 'yaml'`

**Симптом:** `python -m harness.main` падает на первом импорте.

**Решение:**

```bash
rm -rf .venv
./run.sh
```

## Модель не отвечает

**Симптом:** `[backend error] ...` или пустой ответ.

### Если `HARNESS_BACKEND=ollama`

```bash
grep OLLAMA_HOST .env
curl -sf "${OLLAMA_HOST}/api/tags" | head -c 200
ollama list
```

**Частые причины:**

| Причина | Решение |
| --- | --- |
| `ollama serve` не запущен | Запустить сервис |
| Модель не загружена | `ollama pull <имя из HARNESS_MODEL>` |
| `HARNESS_MODEL` не совпадает | Поправить `.env`, перезапустить REPL |
| Модель не влезает в RAM | Уменьшить модель или `HARNESS_NUM_CTX` |

### Если `HARNESS_BACKEND=llamacpp`

```bash
grep LLAMACPP_HOST .env
curl -sf "${LLAMACPP_HOST}/health"
```

**Частые причины:**

| Причина | Решение |
| --- | --- |
| `llama-server` не запущен | `~/Desktop/run-coder3b.sh` |
| Порт в `.env` не совпадает | Скрипт 3B = 8080, 7B = 8082; поправить `LLAMACPP_HOST` |
| Модель не загрузилась | Смотреть лог в терминале сервера |
| Таймаут | Увеличить `LLAMACPP_TIMEOUT`, дефолт 300 с |
| `Connection refused` | Сервер стартует; на N100+7B ждать 20–40 с |

## `[backend error] unknown backend 'foo'`

**Что произошло:** в `.env` `HARNESS_BACKEND=foo` — неизвестное имя.

**Допустимые значения:** `ollama`, `llamacpp`, `llama.cpp`,
`llama-cpp`, `llama_cpp`. Регистр не важен.

**Решение:** поправить `.env`.

## llama.cpp: модель отвечает текстом, но не вызывает tools

**Причина:** запущен с `--chat-template chatml` (без `--jinja`).
В этом режиме `llama-server` не формирует `tool_calls` в структуре
ответа. Harness имеет fallback (`_extract_tool_calls_from_content`),
но он ловит только JSON-объекты в начале строки.

**Решение:**

1. Используйте guided mode (запрос вида «прочитай X и напиши Y»).
   В нём tools не нужны — модель вызывается без них.
2. Или добавьте `--jinja` в скрипт запуска `llama-server`.
   Если пойдут галлюцинации tool `get_info` — верните `chatml`.
3. Или используйте бэкенд `ollama` для задач с tool calling.

## llama.cpp: `[backend error] llama.cpp HTTP 500`

**Что означает:** `llama-server` вернул 500. Типично — переполнение
контекста или слишком длинный запрос.

**Решение:**

1. Проверить `HARNESS_NUM_CTX` в `.env` — должно быть ≤ `--ctx-size`
   сервера.
2. Уменьшить размер источника (`HARNESS_MAX_READ_BYTES`).
3. Посмотреть лог сервера в терминале — там будет конкретная причина.

## Модель не вызывает инструменты

**Симптом:** модель отвечает текстом, но не читает файлы и не
предлагает запись.

**Причина:** модель слишком мала или не поддерживает tool calling.

**Решение:** минимум для уверенной работы — модели от 1.7B с явной
поддержкой tool calling. Для llama.cpp с `chatml` tool calling
не нативный — используйте guided mode.

## `ACCESS DENIED: not in whitelist`

**Что произошло:** модель обратилась к пути, которого нет в
`whitelist`.

**Решение:** проверить `config.yaml:fs.whitelist`. По умолчанию
`["**"]` — всё видно.

## `ACCESS DENIED: in blacklist`

**Частая причина:** попытка прочитать `.env`, `.git/config`,
`secret.key`, `Makefile`. Они в чёрном списке по умолчанию.

**Решение:** точечно закомментировать в `config.yaml:fs.blacklist`.

## `ACCESS DENIED: файл записан в этой сессии`

**Что произошло:** модель пытается прочитать файл, который сама же
предложила записать в этой сессии (read-after-write block).

**Решение:** `/reset` и повторить.

## `REJECTED: path not in writable list`

**Что произошло:** модель предложила запись вне `writable`.

**По умолчанию разрешены:** `output/**`, `notes/**`, `drafts/**`,
`*.md`, `*.txt`.

**Решение:** попросить модель переписать в `output/`.

## `REJECTED: extension '.py' blocked`

**Что произошло:** модель предлагает записать `.py`, `.js`, `.sh`
или другой исполняемый файл.

**Решение:** попросить сохранить как `.txt`:

```text
сохрани этот скрипт как output/script.txt, я запущу вручную
```

Или разрешить директорию через `fs.ext_allow_paths` в `config.yaml`
(см. `docs/policies.md`).

## `[CANCELLED] Код не совпал`

**Ничего не сломалось.** Файл не записан. Повторите или отмените.

## `ERROR: file too large`

**Лимит:** `HARNESS_MAX_READ_BYTES`, дефолт 200 КБ.

**Решение:** разбить файл на части или поднять лимит в `.env`.

## `[BLOCKED] Ввод похож на попытку промпт-инъекции`

**Решение:** переформулировать простыми словами. Если это
легитимный вопрос **про** инъекции — оберните фразу в кавычки.

## `[STOP] Бюджет вызовов инструментов исчерпан`

**Решение:** разбить задачу на несколько, повысить
`HARNESS_MAX_TOOL_ROUNDS` (но осторожно — маленькие модели начинают
выдумывать после 3–4 раундов).

## `ERROR: upstream request failed`

**Что произошло:** `api_call` не смог достучаться до внешнего API.

**Проверьте:**

```bash
curl -sS "https://api.open-meteo.com/v1/forecast?latitude=55.75&longitude=37.62&current=temperature_2m" | head
```

Маршруты перечислены в `config.yaml:proxy.routes`.

## Медленно / OOM

**Для Ollama:**

1. Уменьшить `HARNESS_NUM_CTX` (2048 → 1024).
2. Уменьшить `HARNESS_KEEP_ALIVE` (`5m` → `30s`).
3. Уменьшить `HARNESS_NUM_PREDICT`.
4. Взять модель меньшего размера.

**Для llama.cpp:**

1. Уменьшить `--ctx-size` в скрипте запуска сервера
   (перезапустить сервер).
2. Взять 3B вместо 7B.
3. Использовать Q4_K_M вместо Q5_K_M.
4. Проверить, что не запущены одновременно два сервера.

## `Permission denied` при записи в `workspace/`

**Решение:**

```bash
sudo chown -R "$USER:$USER" workspace logs
```

## Куда смотреть в первую очередь

1. `logs/audit.jsonl` — что модель вызывала, какие `backend_error`.
2. Stdout REPL — сообщения `[error]`, `[BLOCKED]`, `[STOP]`.
3. `.env` — совпадает ли `HARNESS_BACKEND`, `HARNESS_MODEL` с
   реальностью.
4. `config.yaml` — политики путей.
