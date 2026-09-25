# Mini Harness

Упрощённая версия paranoid-harness для слабых машин (8–16 ГБ RAM,
~1.5 ГБ свободно под модель). Без Docker, без mTLS, без гейтвея.
Один Python-процесс, локальный сервер инференса, одна модель.

Поддерживаются два бэкенда инференса:

- **Ollama** (по умолчанию) — нативный `ollama serve`.
- **llama.cpp** — OpenAI-совместимый `llama-server`, собранный
  под конкретный CPU (см. `docs/backends.md`).

Выбор бэкенда — одной переменной в `.env`. Всё остальное
работает одинаково.

## Что осталось от защиты

- **Файлы** — только внутри `./workspace`, whitelist/blacklist/writable,
  защита от traversal, symlink escape, NFKC-гомоглифов.
- **Запись** — только с подтверждением одноразовым кодом,
  одна на сессию, атомарная, `chmod 0o644`.
- **Read-after-write block** — записанный в этой сессии файл нельзя
  прочитать в той же сессии.
- **Инъекции** — все tool-результаты проходят нейтрализацию тегов.
- **Внешние API** — только через маршруты из `config.yaml`.
- **Аудит** — `logs/audit.jsonl`, `fsync` для критичных событий.

## Что потеряно (осознанно)

- Нет сетевой изоляции процесса инференса.
- Нет аутентификации клиентов — один пользователь на машине.
- Нет container hardening.

## Какой бэкенд выбрать

| Сценарий | Бэкенд |
| --- | --- |
| Уже стоит `ollama serve`, модели через `ollama pull` | **ollama** |
| Свой CPU, хочется собрать `llama.cpp` под AVX2/FMA/F16C | **llamacpp** |
| Хочется Web UI на порту 8080/8081/8082 (встроен в `llama-server`) | **llamacpp** |
| Нужен нативный `think=False` для qwen3 | **ollama** |

## Установка

1. Убедитесь, что локальный сервер инференса запущен и модель
   загружена.
   - Для ollama: `ollama serve` и `ollama pull <model>`.
   - Для llama.cpp: `scripts/llama-server.sh use 4b`
     (см. `scripts/README.md`).
2. Запуск:

   ```bash
   chmod +x run.sh
   ./run.sh
   ```

## Использование

```bash
>>> прочитай input/report.md и напиши резюме в output/summary.md
```

При предложении записи покажется diff и одноразовый код.

- `/quit` — выход
- `/reset` — новая сессия

## Batch-обработка директории

```bash
>>> /batch input/batch '*.py' input/prompts/add-docstrings.md
```

Non-chunked режим: файлы уходят в модель целиком. Если файл
больше контекстного бюджета — помечается `failed` с сообщением
«use --chunk».

**Chunked режим** для длинных файлов:

```bash
>>> /batch input/batch '*.py' input/prompts/add-docstrings.md --mode=code --chunk
>>> /batch input/docs '*.md' input/prompts/translate.md --mode=docs --chunk
```

Файл разбивается на чанки, каждый обрабатывается отдельно.
Подробности — в `docs/processing.md`.

## Запуск моделей

Для Ollama — `ollama serve` + `ollama pull <model>`.

Для llama.cpp — единый скрипт `scripts/llama-server.sh`:

```bash
# Запустить модель и переключить .env одной командой
scripts/llama-server.sh use 4b

# Список моделей и портов
scripts/llama-server.sh list

# Остановить
scripts/llama-server.sh stop 4b
```

Полная инструкция — в [scripts/README.md](scripts/README.md).

## Настройка

Все скалярные параметры — в `.env`. Политики путей — в `config.yaml`.
Chunked-режим — в `config/processing.yaml`. Правки подхватываются
при новом запуске.

**Для машин с ограниченной памятью (Ollama):**

- `HARNESS_NUM_CTX` — главный параметр по расходу RAM.
- `HARNESS_KEEP_ALIVE` — выгрузка модели через N секунд.
- `HARNESS_NUM_PREDICT` — ограничение длины ответа.
- `HARNESS_MAX_TOOL_ROUNDS` — для небольших моделей 4 безопаснее.

**Для llama.cpp:** `NUM_CTX` и `KEEP_ALIVE` игнорируются.
Согласуйте `HARNESS_NUM_CTX` в `.env` с `--ctx-size` сервера.

## Что делать при проблеме

- **`[backend error]`** — сервер не запущен или недоступен.
- **`[BLOCKED]`** — ввод похож на инъекцию.
- **`ACCESS DENIED`** — путь в blacklist или вне whitelist.
- **`REJECTED: extension ...`** — сохраняйте скрипты как `.txt`.
- **`FAILED: source N bytes > budget M`** — используйте `--chunk`.
- **Медленно / OOM** — уменьшите `HARNESS_NUM_CTX` или возьмите
  модель меньшего размера.

Полный список — в `docs/troubleshooting.md`.
