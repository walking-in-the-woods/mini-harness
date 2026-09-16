# Шпаргалка

## Запуск

```bash
./run.sh
```

Первый запуск создаст `.venv`, скопирует `.env.example` в `.env`,
создаст `workspace/{input,output,notes,drafts}` и `logs/`.

## Команды REPL

| Команда | Действие |
|---|---|
| `/quit` | Выход |
| `/reset` | Новая сессия |

## Рабочая папка

```
workspace/
├── input/    ← сюда кладёте файлы (read-only для модели)
├── output/   ← сюда модель пишет результат
├── notes/    ← черновики (writable)
└── drafts/   ← черновики (writable)
```

Скопировать файл:

```bash
cp ~/file.md workspace/input/
```

Забрать результат:

```bash
cat workspace/output/summary.md
```

## Форма задачи

```
прочитай <путь> и напиши <что> в <путь>
```

Примеры:

```
прочитай input/report.md и напиши резюме в output/summary.md
прочитай input/code.py и объясни в output/explain.md
прочитай input/data.txt и оформи как таблицу в output/table.md
```

## Подтверждение записи

```
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
curl -sf "${OLLAMA_HOST:-http://127.0.0.1:11434}/api/tags" | head -c 200

# Запустить тесты без модели
pytest tests/ --ignore=tests/smoke -q

# Запустить smoke (если есть модель)
OLLAMA_HOST=http://127.0.0.1:11434 \
SMOKE_MODEL=<имя модели> \
SMOKE_MODEL_SMALL=<маленькая модель> \
  pytest tests/smoke -m smoke -v -s
```

## Ограничения

| Что | Где | Дефолт |
|---|---|---|
| Размер файла на чтение | `HARNESS_MAX_READ_BYTES` | 200 000 |
| Размер `propose_write` | `HARNESS_MAX_WRITE_BYTES` | 1 000 000 |
| Записей на сессию | — | 1 |
| Раундов с инструментами | `HARNESS_MAX_TOOL_ROUNDS` | 4 |
| Элементов в `list_dir` | `HARNESS_MAX_LIST_ENTRIES` | 500 |

## Что делать при проблеме

1. `logs/audit.jsonl` — что модель вызывала.
2. `docs/troubleshooting.md` — типовые ситуации.

## Не забыть

- **`workspace/` не должен быть симлинком на `$HOME`.**
- **Не кладите секреты в `workspace/`.**
- **Проверяйте diff перед вводом кода.**
- **`path` в audit.jsonl не редактируется** — учитывайте при
  именовании файлов.
