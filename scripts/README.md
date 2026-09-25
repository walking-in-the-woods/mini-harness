# Скрипты и запуск моделей

Оглавление:

- [Быстрый старт](#быстрый-старт)
- [llama-server.sh — единый запускатель](#llama-server-единый-запускатель)
- [Полный цикл batch-проверки](#полный-цикл-batch-проверки)
- [Переключение между моделями](#переключение-между-моделями)
- [Диагностика](#диагностика)
- [Добавление новой модели](#добавление-новой-модели)
- [Прочие скрипты](#прочие-скрипты)

---

## Быстрый старт

Запустить llama.cpp-сервер с моделью, обновить `.env` под неё и
работать в REPL:

```bash
# 1. Запустить модель в фоне и переключить .env одной командой
~/as-dev/mini-harness/scripts/llama-server.sh use 4b

# 2. Запустить harness
cd ~/as-dev/mini-harness
./run.sh

# 3. В REPL — обычная работа
>>> прочитай input/article.md и напиши краткое резюме в output/summary.md
>>> /quit

# 4. Остановить сервер (из другого терминала)
~/as-dev/mini-harness/scripts/llama-server.sh stop 4b
```

Alias модели (`4b`, `3b`, `7b`) — см. таблицу в скрипте или:

```bash
~/as-dev/mini-harness/scripts/llama-server.sh list
```

---

## llama-server.sh — единый запускатель

Один скрипт знает параметры всех моделей. Добавить новую модель —
одна строка в таблице `MODELS=(...)` внутри скрипта.

### Команды

| Команда | Что делает |
| --- | --- |
| `list` | Список моделей: alias, файл, порт, ctx, chat-режим, статус |
| `status` | Что сейчас запущено (включая чужие процессы llama-server) |
| `<alias>` | Запустить в **foreground** — Ctrl+C останавливает |
| `start <alias>` | Запустить в **фоне**, лог в файл |
| `use <alias>` | `start` + обновить `.env` под эту модель |
| `stop <alias>` | Остановить фоновый сервер |
| `stop-all` | Остановить все серверы, запущенные через скрипт |
| `logs <alias>` | `tail -f` лога фонового сервера |
| `env <alias>` | Напечатать строки для `.env` (без записи) |

### Примеры

```bash
SCRIPT=~/as-dev/mini-harness/scripts/llama-server.sh

# Список моделей и их статус
$SCRIPT list

# Запустить 4B в фоне, обновить .env одной командой
$SCRIPT use 4b

# Посмотреть лог фонового сервера (Ctrl+C выходит из tail)
$SCRIPT logs 4b

# Переключиться на 7B без остановки 4B (порт другой)
$SCRIPT use 7b

# Что запущено
$SCRIPT status

# Остановить конкретную модель
$SCRIPT stop 4b

# Остановить всё
$SCRIPT stop-all

# Напечатать строки .env вручную (посмотреть, не записывая)
$SCRIPT env 4b
```

### Автоматический выбор порта

У каждой модели есть **базовый порт** в таблице `MODELS`:

| Alias | Базовый порт | Модель |
| --- | --- | --- |
| `3b` | 8080 | Qwen2.5-Coder 3B Q4_K_M |
| `4b` | 8081 | Qwen3-4B-Instruct-2507 Q4_K_M |
| `7b` | 8082 | Qwen2.5-Coder 7B Q3_K_M |

Если базовый порт занят (например, вы запустили `3b` в foreground,
и в другом терминале пытаетесь запустить его же — или на 8081 висит
чужой процесс), скрипт **автоматически** берёт следующий свободный
в диапазоне `[base, base+10]`.

Фактический порт сохраняется в `$XDG_RUNTIME_DIR/llama-server/<alias>.port`
и используется командой `use` для записи в `.env`.

### Где живут PID, порт и лог

```text
$XDG_RUNTIME_DIR/llama-server/
├── 3b.pid
├── 3b.port
├── 4b.pid
├── 4b.port
└── logs/
    ├── 3b.log
    └── 4b.log
```

Каталог чистится при перезагрузке.

### Переменные окружения

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `LLAMA_DIR` | `$HOME/as-dev/llama.cpp` | Где собрана `llama.cpp` |
| `HEALTH_TIMEOUT` | 90 | Секунд ждать `/health` после старта |
| `OPEN_BROWSER` | `true` | Открывать Web UI автоматически |
| `PORT_SCAN_RANGE` | 10 | Сколько портов после базового проверять |
| `MINI_HARNESS_DIR` | авто | Путь к `mini-harness/` (для `.env`) |

Пример с другими значениями:

```bash
HEALTH_TIMEOUT=180 OPEN_BROWSER=false $SCRIPT use 7b
```

---

## Полный цикл batch-проверки

Пример для Qwen3-4B-Instruct-2507 (alias `4b`) на N100.

### Предварительные требования

- `llama.cpp` собран: `~/as-dev/llama.cpp/build/bin/llama-server`
- Модель скачана: `~/as-dev/llama.cpp/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`
- Есть входные файлы: `workspace/input/batch/sample_a.py`, `sample_b.py`, `sample_c.py`
- Есть prompt-файл: `workspace/input/prompts/add-docstrings-generic.md`
- `config.yaml` содержит `output/batch/**` в `writable` и `ext_allow_paths`

### Шаг 1. Запустить сервер и обновить `.env`

```bash
~/as-dev/mini-harness/scripts/llama-server.sh use 4b
```

### Шаг 2. Запустить harness

```bash
cd ~/as-dev/mini-harness
./run.sh
```

### Шаг 3. Запустить batch

В REPL:

```bash
>>> /batch input/batch '*.py' input/prompts/add-docstrings-generic.md
```

Ожидаемое время: 7–10 минут для 3 файлов на 4B Q4.

### Шаг 4. Проверить результат

```bash
cd ~/as-dev/mini-harness

LATEST=$(ls -td workspace/output/batch/*/ | head -1)
echo "Latest: $LATEST"

for f in "$LATEST"sample_*.py; do
  python3 -m py_compile "$f" && echo "OK: $f" || echo "FAIL: $f"
done

for f in sample_a sample_b sample_c; do
  echo "═══ $f ═══"
  diff workspace/input/batch/$f.py "$LATEST$f.py"
done
```

### Шаг 5. Остановить сервер

```bash
~/as-dev/mini-harness/scripts/llama-server.sh stop 4b
# или всё сразу:
~/as-dev/mini-harness/scripts/llama-server.sh stop-all
```

---

## Переключение между моделями

Две модели могут работать параллельно — порты разные, RAM хватает.

### Запустить 4B и 3B одновременно

```bash
~/as-dev/mini-harness/scripts/llama-server.sh start 4b
~/as-dev/mini-harness/scripts/llama-server.sh start 3b

~/as-dev/mini-harness/scripts/llama-server.sh status
```

### Переключить harness на 3B

```bash
~/as-dev/mini-harness/scripts/llama-server.sh use 3b
./run.sh
```

### Остановить одну

```bash
~/as-dev/mini-harness/scripts/llama-server.sh stop 3b
```

---

## Диагностика

### Сервер не отвечает

```bash
~/as-dev/mini-harness/scripts/llama-server.sh status
pgrep -af llama-server
ss -tlnp | grep -E '8080|8081|8082'
curl -sf http://127.0.0.1:8081/health
```

### Порт занят

```bash
ss -tlnp | grep 8081
sudo lsof -i :8081
~/as-dev/mini-harness/scripts/llama-server.sh stop-all
pkill -f llama-server
```

### Что-то странное с .env

Посмотреть, что скрипт пропишет, не записывая:

```bash
~/as-dev/mini-harness/scripts/llama-server.sh env 4b
```

Проверить, что уже в `.env`:

```bash
grep -E '^(HARNESS_BACKEND|HARNESS_MODEL|LLAMACPP_HOST|HARNESS_NUM_CTX|LLAMACPP_TIMEOUT)=' .env
```

---

## Добавление новой модели

1. Скачать GGUF в `~/as-dev/llama.cpp/models/`:

   ```bash
   cd ~/as-dev/llama.cpp/models
   curl -L -C - -o MyModel-Q4_K_M.gguf \
     "https://huggingface.co/.../resolve/main/MyModel-Q4_K_M.gguf?download=true"
   ```

2. Убедиться, что размер совпадает с ожидаемым:

   ```bash
   ls -lh ~/as-dev/llama.cpp/models/MyModel-Q4_K_M.gguf
   ```

3. Открыть `scripts/llama-server.sh`, найти блок `MODELS=(...)`,
   добавить строку:

   ```bash
   "mymodel|MyModel-Q4_K_M.gguf|8083|4096|chatml|Описание модели"
   ```

   Формат: `alias | файл | base_port | ctx-size | chat-режим | описание`.

4. Проверить:

   ```bash
   ~/as-dev/mini-harness/scripts/llama-server.sh list
   ~/as-dev/mini-harness/scripts/llama-server.sh use mymodel
   ```

---

## Прочие скрипты

| Скрипт | Назначение |
| --- | --- |
| `llama-server.sh` | Единый запускатель llama.cpp-серверов (этот файл) |
| `setup-workspace.sh` | Идемпотентная инициализация `workspace/` и `logs/` |
| `setup-experiments.sh` | Готовит структуру `docs/experiments/` для публикации |
| `check-public-safety.sh` | Аудит перед публикацией: секреты, ключи, личные пути |

Все три вызываются из `run.sh` автоматически, отдельный запуск
нужен редко.
