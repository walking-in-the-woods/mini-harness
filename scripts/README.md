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
и используется командой `use` для записи в `.env`. То есть вы можете
не следить за номерами портов — скрипт сам разберётся.

Диапазон сканирования задаётся переменной `PORT_SCAN_RANGE` (по
умолчанию 10).

### Где живут PID, порт и лог

```text
$XDG_RUNTIME_DIR/llama-server/         (обычно /run/user/1000/llama-server/)
├── 3b.pid                              PID фонового процесса
├── 3b.port                             фактический порт
├── 4b.pid
├── 4b.port
└── logs/
    ├── 3b.log
    └── 4b.log
```

Каталог чистится при перезагрузке — это нормально, PID-файлы
нужны только в пределах сессии.

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

Проверка:

```bash
cd ~/as-dev/mini-harness

ls -lh ~/as-dev/llama.cpp/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
ls -la workspace/input/batch/
ls -la workspace/input/prompts/add-docstrings-generic.md
grep -A3 'ext_allow_paths' config.yaml
```

### Шаг 1. Запустить сервер и обновить `.env`

```bash
~/as-dev/mini-harness/scripts/llama-server.sh use 4b
```

Что произойдёт:

1. Сервер 4B стартует в фоне на порту 8081 (или следующем свободном).
2. `.env` обновится: `HARNESS_BACKEND=llamacpp`, `HARNESS_MODEL=4b`,
   `LLAMACPP_HOST=http://127.0.0.1:<порт>`, `HARNESS_NUM_CTX=4096`.
3. Откроется Web UI в браузере.
4. Через 15–25 секунд покажет `OK`.

Проверка сервера отдельно:

```bash
curl -sf http://127.0.0.1:8081/health && echo
```

### Шаг 2. Запустить harness

```bash
cd ~/as-dev/mini-harness
./run.sh
```

В баннере должно быть:

```sh
backend:      llamacpp
model:        4b
host:         http://127.0.0.1:8081
num_ctx:      4096
```

### Шаг 3. Запустить batch

В REPL:

```sh
>>> /batch input/batch '*.py' input/prompts/add-docstrings-generic.md
```

Ожидаемый сценарий:

```sh
[*] batch: 3 файлов, prompt: input/prompts/add-docstrings-generic.md, target: output/batch/2026-09-24-160000/
[*] [1/3] input/batch/sample_a.py
    -> ok, 456 chars, 24.3s
[*] [2/3] input/batch/sample_b.py
    -> ok, 612 chars, 31.7s
[*] [3/3] input/batch/sample_c.py
    -> ok, 502 chars, 27.1s

================================================================
 BATCH PREVIEW — 3 items
================================================================

  [ ]  1. input/batch/sample_a.py                456 chars
  [ ]  2. input/batch/sample_b.py                612 chars
  [ ]  3. input/batch/sample_c.py                502 chars

Будет записано: 3 из 3
================================================================
 Введите код для применения: A1B2C3D4
 '<номер> skip' — исключить файл из записи
 Любой другой ввод — отмена
================================================================

code> A1B2C3D4
[*] Применяю batch...
  [+] output/batch/2026-09-24-160000/sample_a.py (456 chars)
  [+] output/batch/2026-09-24-160000/sample_b.py (612 chars)
  [+] output/batch/2026-09-24-160000/sample_c.py (502 chars)

[OK] Записано: 3, ошибок: 0
```

Время для 3 файлов на 4B Q4: **7–10 минут**. Это норма для N100.

### Шаг 4. Проверить результат

```bash
cd ~/as-dev/mini-harness

# Найти последний batch
LATEST=$(ls -td workspace/output/batch/*/ | head -1)
echo "Latest: $LATEST"

# Синтаксис
for f in "$LATEST"sample_*.py; do
  python3 -m py_compile "$f" && echo "OK: $f" || echo "FAIL: $f"
done

# Diff с оригиналом — ожидаются только добавленные строки (знак >)
for f in sample_a sample_b sample_c; do
  echo "═══ $f ═══"
  diff workspace/input/batch/$f.py "$LATEST$f.py"
done
```

**Идеальный diff** — только строки `>` (добавленные docstrings),
без `<` (удалённых строк оригинала). Если увидите `<` — модель
потеряла код, структурная проверка не поймала. Присылайте diff.

Функциональный тест:

```bash
LATEST=$(ls -td workspace/output/batch/*/ | head -1)

python3 -c "
import sys
sys.path.insert(0, '${LATEST}')
from sample_a import add, multiply
from sample_b import is_even, factorial
from sample_c import first_char, reverse
assert add(2, 3) == 5
assert multiply(4, 5) == 20
assert is_even(4) is True
assert is_even(5) is False
assert factorial(5) == 120
assert first_char('abc') == 'a'
assert reverse('abc') == 'cba'
print('all logic OK')
"
```

### Шаг 5. Остановить сервер

```bash
# Если работали в foreground — Ctrl+C в его терминале.
# Если в фоне:
~/as-dev/mini-harness/scripts/llama-server.sh stop 4b

# Или всё сразу:
~/as-dev/mini-harness/scripts/llama-server.sh stop-all
```

Проверка, что чисто:

```bash
~/as-dev/mini-harness/scripts/llama-server.sh status
pgrep -af llama-server || echo clean
free -h
```

### Что может пойти не так

**Провал структурной проверки:**

```sh
[*] [1/3] input/batch/sample_a.py
    [!] structure: body of 'add' lost (was 1 statements); retry с хинтом
    [!] structure: body of 'add' lost (was 1 statements)
    -> FAILED: structure: ...
```

Модель потеряла тело функции при вставке docstring, retry не помог.
Файл не записан. Если так со всеми тремя — попробовать 7B:

```bash
~/as-dev/mini-harness/scripts/llama-server.sh use 7b
# перезапустить ./run.sh и повторить batch
```

**Reasoning-leakage:**

```sh
    [!] reasoning: первая строка — мета: 'Хорошо, мне нужно...'; retry
    -> FAILED: reasoning leaked twice
```

Для `Qwen3-4B-Instruct-2507` это крайне маловероятно — она без
reasoning-фазы, `/no_think` для неё no-op. Если случится:
проверить, что сервер запущен с `--jinja` (см. таблицу `MODELS`
в скрипте, поле chat-режима для `4b` должно быть `jinja`).

**Timeout:**

```sh
-> FAILED: read: ollama ... timeout
```

Увеличить `LLAMACPP_TIMEOUT` в `.env`:

```env
LLAMACPP_TIMEOUT=1200
```

Или уменьшить `HARNESS_NUM_PREDICT`, если файлы очень большие.

---

## Переключение между моделями

Две модели могут работать параллельно — порты разные, RAM хватает.

### Запустить 4B и 3B одновременно

```bash
~/as-dev/mini-harness/scripts/llama-server.sh start 4b
~/as-dev/mini-harness/scripts/llama-server.sh start 3b

~/as-dev/mini-harness/scripts/llama-server.sh status
```

Обе модели теперь слушают: 4B на 8081, 3B на 8080. RAM ~4.5 ГБ
на веса плюс KV-cache, на 16 ГБ комфортно.

### Переключить harness на 3B

```bash
~/as-dev/mini-harness/scripts/llama-server.sh use 3b
./run.sh
```

`use` перезапишет `.env` на 3B (порт 8080). Правки читаются при
следующем запуске `./run.sh`.

### Остановить одну

```bash
~/as-dev/mini-harness/scripts/llama-server.sh stop 3b
```

4B продолжает работать.

---

## Диагностика

### Сервер не отвечает

```bash
# Через скрипт
~/as-dev/mini-harness/scripts/llama-server.sh status

# Напрямую
pgrep -af llama-server
ss -tlnp | grep -E '8080|8081|8082'
curl -sf http://127.0.0.1:8081/health
```

Если `pgrep` находит процесс, но `curl` не отвечает — сервер ещё
грузится (4B через `mlock` = 15–25 с, 7B Q3 = 30–40 с).

Если процесс не найден — смотреть лог фонового сервера:

```bash
~/as-dev/mini-harness/scripts/llama-server.sh logs 4b
```

Или `Ctrl+C` в терминале, где запускали в foreground.

### Порт занят

```bash
ss -tlnp | grep 8081
```

Кто слушает и на каком PID:

```bash
sudo lsof -i :8081
```

Если это ваш старый процесс — остановить:

```bash
~/as-dev/mini-harness/scripts/llama-server.sh stop-all
pkill -f llama-server   # на всякий случай, если что-то осталось
```

Скрипт сам разберётся с fallback-портом, но если хотите явно
зафиксировать — очистите порт и запустите снова.

### Что-то странное с .env

Посмотреть, что скрипт пропишет, **не записывая**:

```bash
~/as-dev/mini-harness/scripts/llama-server.sh env 4b
```

Вывод:

```text
HARNESS_BACKEND=llamacpp
HARNESS_MODEL=4b
LLAMACPP_HOST=http://127.0.0.1:8081
HARNESS_NUM_CTX=4096
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

   Флаг `-C -` включает докачку: если оборвётся, повторите — продолжит.

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

   - `alias` — короткое имя без пробелов, латиница (например `ds7b`).
   - `base_port` — не занятый другим alias (8083, 8084…).
   - `ctx-size` — по объёму RAM. Для 7B Q4 обычно 2048, для 3B/4B — 4096.
   - `chat-режим`: `jinja` для Qwen3, `chatml` для Qwen2.5 и подобных,
     `none` — отдать шаблон из GGUF.

4. Проверить:

   ```bash
   ~/as-dev/mini-harness/scripts/llama-server.sh list
   ~/as-dev/mini-harness/scripts/llama-server.sh use mymodel
   ```

Опционально — создать `.desktop`-ярлык (см. раздел
[Ярлыки GNOME](#ярлыки-gnome) ниже).

### Ярлыки GNOME

Три `.desktop`-файла для 3B, 4B, 7B лежат в
`~/.local/share/applications/`:

- `llama-coder3b.desktop` → `~/Desktop/run-coder3b.sh`
- `llama-qwen3-4b.desktop` → `~/Desktop/run-qwen3-4b.sh`
- `llama-coder7b.desktop` → `~/Desktop/run-coder7b.sh`

Каждая обёртка — две строки:

```bash
#!/usr/bin/env bash
exec "$HOME/as-dev/mini-harness/scripts/llama-server.sh" 4b
```

Для новой модели:

1. Создать `~/Desktop/run-mymodel.sh` по образцу.
2. `chmod +x ~/Desktop/run-mymodel.sh`
3. Создать `~/.local/share/applications/llama-mymodel.desktop`:

   ```ini
   [Desktop Entry]
   Type=Application
   Version=1.0
   Name=My Model (Web UI)
   Comment=MyModel Q4_K_M через llama-server
   Exec=/home/as/Desktop/run-mymodel.sh
   Path=/home/as/as-dev/llama.cpp
   Terminal=true
   Icon=utilities-terminal
   Categories=Development;
   StartupNotify=true
   ```

4. `chmod +x ~/.local/share/applications/llama-mymodel.desktop`
5. `update-desktop-database ~/.local/share/applications`
6. `gnome-session-quit --logout --no-prompt` — после входа иконка
   появится в поиске Activities.

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
