# Разработка

## Тесты

```bash
# Установить dev-зависимости
pip install -r requirements.txt -r requirements-dev.txt

# Юнит-тесты (без модели)
pytest tests/ --ignore=tests/smoke -v

# Smoke (нужен запущенный сервер инференса с моделью)
OLLAMA_HOST=http://127.0.0.1:11434 \
SMOKE_MODEL=<имя модели из .env> \
SMOKE_MODEL_SMALL=<маленькая модель> \
SMOKE_TOOL_CAPABLE=true \
  pytest tests/smoke -m smoke -v -s
```

Если `SMOKE_MODEL`/`SMOKE_MODEL_SMALL` не заданы — smoke-тесты
скипаются, а не падают. Это сделано специально: CI не должен
требовать модель на каждом прогоне.

## Что покрыто

| Файл | Что проверяет |
|---|---|
| `test_glob_to_regex.py` | Семантика `*`, `**`, `?` |
| `test_fs_guard.py` | Path traversal, symlink, NFKC, списки, расширения, пустой whitelist |
| `test_injection_guard.py` | Нормализация, детекция, `neutralize_data_block`, `scan_payload` |
| `test_confirm.py` | Nonce, diff, атомарная запись, mode 0644 |
| `test_audit.py` | JSONL-формат, устойчивость к ошибкам ФС |
| `test_agent_tools.py` | Все tool-функции, `_redact_args`, `_wrap_tool_result`, read-after-write |
| `test_proxy.py` | Route→URL, лимиты, `follow_redirects`, https-only |
| `smoke/test_model_smoke.py` | Базовая работоспособность модели |
| `smoke/test_harness_roundtrip.py` | End-to-end tool calling |

## CI

`.github/workflows/ci.yml`, два job'а:

- **`static-checks`** — `bash -n` для `deploy.sh`/`run.sh`,
  `python -m py_compile` для `harness/*.py`, импорт модулей с пустым
  `.env`.
- **`tests`** — pytest на Python 3.10/3.11/3.12.

Docker-smoke нет: mini работает без Docker.

## Стиль

- **Docstring — на русском, имена и код — на английском.**
- **Инварианты — в комментариях** там, где их легко потерять при
  рефакторинге.
- **Тест-инвариант добавляется вместе с правкой**, которая его
  вводит.
- **Имена файлов и тестов говорящие.** Однобуквенные параметры
  публичных функций запрещены.

## Куда смотреть, если меняете

- **`harness/agent.py`** — поведение инструментов, формат
  `<tool_result>`, лимиты. После правок — прогнать smoke.
- **`harness/fs_guard.py`** — политики путей и расширений.
  После правок — убедиться, что тесты `_glob_to_regex` и
  `test_fs_guard` проходят.
- **`harness/injection_guard.py`** — эвристики и нейтрализация.
  Не убирать `neutralize_data_block` без замены. Расширяя `_TAG_RE`,
  добавлять тесты в `test_neutralize_escapes_extended_tag_set`.
- **`harness/confirm.py`** — nonce и атомарность. `chmod 0o644` —
  намеренно.
- **`harness/audit.py`** — не добавлять `fsync` для информационных
  событий.
- **`harness/proxy.py`** — маршруты, лимиты.
- **`harness/main.py`** — парсинг `.env`, сборка `cfg`. Здесь
  скаляры из env превращаются в плоский dict для `HarnessAgent`.

## Что не делать

- **Не полагаться на эвристики `InjectionGuard` как на основную
  защиту.** Архитектурная нейтрализация важнее.
- **Не расширять `SYSTEM_PROMPT`.** Он минимальный намеренно.
- **Не логировать `content`/`body`/`params` в audit без
  редактирования.**
- **Не хранить секреты в `workspace/`** — модель их прочитает.

## Проверка перед коммитом

```bash
pytest tests/ --ignore=tests/smoke -q
bash -n run.sh deploy.sh
python -m py_compile harness/*.py
```
