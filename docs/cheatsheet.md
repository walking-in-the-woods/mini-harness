# Шпаргалка

## Запуск

```bash
./run.sh
```

Первый запуск создаст `.venv`, скопирует `.env.example` в `.env`,
создаст `workspace/{input,output,notes,drafts}` и `logs/`.

## Выбор бэкенда

В `.env`:

```env
# Ollama (по умолчанию)
HARNESS_BACKEND=ollama
HARNESS_MODEL=qwen2.5-coder:7b
OLLAMA_HOST=http://127.0.0.1:11434

# Или llama.cpp
HARNESS_BACKEND=llamacpp
LLAMACPP_HOST=http://127.0.0.1:8080
LLAMACPP_TIMEOUT=300
```

Баннер при старте показывает выбранный бэкенд и адрес.

## Команды REPL

| Команда | Действие |
| --- | --- |
| `/quit` | Выход |
| `/reset` | Новая сессия |
| `/sources` | Список внешних источников |
| `/tree <name> [sub]` | Дерево источника → `input/_tree_<name>.md` |
| `/files <name> [sub]` | Плоский список → `input/_files_<name>.md` |
| `/dump <name> [sub]` | Содержимое → `input/_dump_<name>.md` |
| `/reload` | Перечитать `sources.yaml` |
| `/batch <src> <glob> <prompt>` | Пакетная обработка |

## Рабочая папка

```text
workspace/
├── input/    ← сюда кладёте файлы (read-only для модели)
├── output/   ← сюда модель пишет результат
├── notes/    ← черновики (writable)
└── drafts/   ← черновики (writable)
```

## Форма задачи (guided mode)

```text
прочитай <путь> и напиши <что> в <путь>
```

Примеры:

```text
прочитай input/report.md и напиши резюме в output/summary.md
прочитай input/code.py и объясни в output/explain.md
прочитай input/data.txt и оформи как таблицу в output/table.md
```

## Подтверждение записи

```sh
============================================================
 PENDING WRITES — ПРОВЕРЬТЕ ПЕРЕД ПОДТВЕРЖДЕНИЕМ
============================================================
--- output/summary.md ---
[NEW FILE, 412 bytes]
# Краткое резюме
...
============================================================
 Введите код для ПРИМЕНЕНИЯ: 4F7A2C19
============================================================

code> 4F7A2C19      ← запись
code> <Enter>       ← отмена
code> <неверный>    ← отмена
```

## Что разрешено записывать

- `output/**`, `notes/**`, `drafts/**`
- `*.md`, `*.txt` в корне `workspace/`
- Всё, кроме `.sh .bash .py .js .ts .rb .go .rs .exe .bat .ps1
  .php .cgi .pl .whl .egg`

## Что запрещено читать

- `.env`, `.git/**`, `.ssh/**`, `.aws/**`, `.kube/**`
- `*.key`, `*.pem`, `id_rsa*`, `id_ed25519*`
- `.bashrc`, `.zshrc`, `.profile`, `.netrc`, `.npmrc`, `.pypirc`
- `Makefile`, `Dockerfile*`, `docker-compose*`, `package.json`,
  `setup.py`, `pyproject.toml`, `.github/workflows/*`

## Логи

```bash
tail -f logs/audit.jsonl
```

## Полезные команды

```bash
# Проверить, что сервер инференса отвечает
# ollama:
curl -sf "${OLLAMA_HOST:-http://127.0.0.1:11434}/api/tags" | head -c 200
# llama.cpp:
curl -sf "http://127.0.0.1:8080/health"

# Запустить тесты без модели
pytest tests/ --ignore=tests/smoke -q

# Smoke для ollama
OLLAMA_HOST=http://127.0.0.1:11434 \
SMOKE_MODEL=<имя модели> \
SMOKE_MODEL_SMALL=<маленькая модель> \
  pytest tests/smoke -m smoke -v -s
```

## Ограничения

| Что | Где | Дефолт |
| --- | --- | --- |
| Размер файла на чтение | `HARNESS_MAX_READ_BYTES` | 200 000 |
| Размер `propose_write` | `HARNESS_MAX_WRITE_BYTES` | 1 000 000 |
| Записей на сессию | — | 1 |
| Раундов с инструментами | `HARNESS_MAX_TOOL_ROUNDS` | 4 |
| Элементов в `list_dir` | `HARNESS_MAX_LIST_ENTRIES` | 500 |
| Таймаут llama.cpp | `LLAMACPP_TIMEOUT` | 300 |

## Что делать при проблеме

1. `logs/audit.jsonl` — что модель вызывала, какие `backend_error`.
2. `docs/troubleshooting.md` — типовые ситуации.
3. `docs/backends.md` — специфика llama.cpp vs ollama.

## Не забыть

- **`workspace/` не должен быть симлинком на `$HOME`.**
- **Не кладите секреты в `workspace/`.**
- **Проверяйте diff перед вводом кода.**
- **`path` в audit.jsonl не редактируется** — учитывайте при
  именовании файлов.
- **При llama.cpp** — `HARNESS_NUM_CTX` в `.env` согласуйте с
  `--ctx-size` сервера вручную.
