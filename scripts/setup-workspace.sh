#!/usr/bin/env bash
# setup-workspace.sh — инициализация рабочего окружения mini-harness.
#
# На свежем клоне git директория workspace/ пуста: всё её содержимое
# матчится `workspace/*` в .gitignore. Этот скрипт создаёт то, что
# нужно для запуска:
#
#   workspace/{input,output,notes,drafts}/
#   workspace/input/prompts/
#   workspace/sources.yaml.example          — шаблон источников
#   workspace/sources.yaml                  — рабочий файл (не коммитится)
#   workspace/input/prompts/tree-summary.md — минимальный prompt-файл
#   workspace/input/prompts/tree-summary-detailed.md — полная версия
#   workspace/.gitkeep, logs/.gitkeep
#
# Идемпотентен: повторный запуск не перезаписывает существующие файлы.
# Флаг --force перезаписывает пользовательские файлы (sources.yaml,
# prompt-файлы).
#
# Использование:
#   bash scripts/setup-workspace.sh [--force]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -h|--help)
            sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "[!] неизвестный флаг: $arg" >&2
            exit 2
            ;;
    esac
done

cd "${PROJECT_ROOT}"
echo "[*] Проект: ${PROJECT_ROOT}"

# ── Дерево каталогов ───────────────────────────────────────────────────────
for d in \
    workspace \
    workspace/input \
    workspace/input/prompts \
    workspace/output \
    workspace/notes \
    workspace/drafts \
    logs ; do
    mkdir -p "$d"
done
echo "[+] Дерево workspace/ и logs/ готово"

# ── .gitkeep ───────────────────────────────────────────────────────────────
for keep in workspace/.gitkeep logs/.gitkeep; do
    if [[ ! -f "$keep" ]]; then
        : > "$keep"
        echo "[+] Создан $keep"
    fi
done

# ── workspace/sources.yaml.example ─────────────────────────────────────────
SOURCES_EXAMPLE="workspace/sources.yaml.example"
if [[ ! -f "$SOURCES_EXAMPLE" ]]; then
    cat > "$SOURCES_EXAMPLE" <<'YAML_EOF'
# ── Внешние источники для harness ─────────────────────────────────────────
#
# Каждый source — read-only корень ВНЕ workspace. Harness читает файлы
# оттуда и складывает результат в workspace/input/, где его видит
# модель. Сами внешние файлы не модифицируются ни в каком режиме.
#
# Команды REPL (это команды пользователя, не модели):
#   /sources                     список источников
#   /tree <name> [subpath]       дерево     -> input/_tree_<name>.md
#   /files <name> [subpath]      список путей -> input/_files_<name>.md
#   /dump <name> [subpath]       содержимое -> input/_dump_<name>.md
#   /reload                      перечитать этот файл

defaults:
  # Глубина обхода. 0 = без ограничений (по умолчанию).
  # От symlink-циклов защищает детектор visited.
  max_depth: 0

  # /tree — без ограничения на количество записей.
  max_tree_entries: 0

  # /files — плоский список путей с размерами.
  max_entries: 500

  # /dump — содержимое файлов. Лимиты жёсткие.
  max_files: 200
  max_file_bytes: 50000
  max_total_bytes: 200000

  ignore:
    dirs:
      - "**/.git/**"
      - "**/node_modules/**"
      - "**/__pycache__/**"
      - "**/.venv/**"
      - "**/dist/**"
      - "**/build/**"
    files:
      - "**/*.lock"
      - "**/package-lock.json"
      - "**/*.min.js"

sources: []
YAML_EOF
    echo "[+] Создан $SOURCES_EXAMPLE"
else
    echo "[=] $SOURCES_EXAMPLE уже существует"
fi

# ── workspace/sources.yaml ─────────────────────────────────────────────────
SOURCES_FILE="workspace/sources.yaml"
if [[ ! -f "$SOURCES_FILE" ]] || [[ "$FORCE" -eq 1 ]]; then
    cp "$SOURCES_EXAMPLE" "$SOURCES_FILE"
    echo "[+] Создан $SOURCES_FILE"
    echo "    Откройте и добавьте источники в секцию 'sources:'"
else
    echo "[=] $SOURCES_FILE уже существует, не трогаю"
fi

# ── workspace/input/prompts/tree-summary.md ────────────────────────────────
TREE_MIN="workspace/input/prompts/tree-summary.md"
if [[ ! -f "$TREE_MIN" ]] || [[ "$FORCE" -eq 1 ]]; then
    cat > "$TREE_MIN" <<'PROMPT_EOF'
Опиши проект по дереву файлов. Ответ — три предложения на русском
языке.

Скажи, что это за проект и на каком языке написан. Назови 3-4
значимые директории верхнего уровня (не отдельные файлы) и одной
фразой опиши назначение каждой. Упомяни инструменты и технологии,
которые видны по именам файлов.

ВАЖНО: выведи ТОЛЬКО готовый текст. Не пиши вводных фраз вроде
«Хорошо, мне нужно…», «Сначала определю…», «Теперь нужно…».
Не рассуждай, не задавай вопросов, не проверяй себя. Начни сразу
с первого предложения описания.

Связный текст без заголовков, списков и разметки. Служебные
директории (logs, .git, .venv, workspace) не упоминай. Не выдумывай
директории и файлы, которых нет в дереве.
PROMPT_EOF
    echo "[+] Создан $TREE_MIN"
else
    echo "[=] $TREE_MIN уже существует, не трогаю"
fi

# ── workspace/input/prompts/tree-summary-detailed.md ───────────────────────
TREE_DETAILED="workspace/input/prompts/tree-summary-detailed.md"
if [[ ! -f "$TREE_DETAILED" ]] || [[ "$FORCE" -eq 1 ]]; then
    cat > "$TREE_DETAILED" <<'PROMPT_EOF'
# Описание проекта по дереву файлов

Тебе передано дерево файлов проекта. Опиши проект так, чтобы читатель
понял его, не открывая отдельные файлы. Ответ — три-четыре абзаца
связного текста на русском языке.

## Что осветить

Первый абзац: что это за проект. Тип (библиотека, приложение, CLI,
агент, фреймворк), язык программирования. Определи по ключевым файлам
в корне: `README.md`, `setup.py`, `pyproject.toml`, `requirements.txt`,
`package.json`, `Cargo.toml`, `go.mod`.

Второй абзац: назначение 3-5 ключевых директорий верхнего уровня. Для
каждой — одна короткая фраза.

Третий абзац: стек и инструменты. Какие библиотеки, системы
тестирования, линтеры, CI видны по именам файлов.

Четвёртый абзац: специфика. Если в дереве видно что-то необычное —
упомяни одной фразой. Если нет — пропусти абзац.

## Правила

- Каждая директория упоминается ровно один раз.
- Служебные директории не упоминай: `logs/`, `.github/`, `.venv/`,
  `workspace/`, `.pytest_cache/`.
- Не начинай с «документ описывает», «в файле представлено».
- Не выводи заголовки абзацев типа «Первый абзац» или «Абзац 1».
- Не пересказывай дерево — это описание, не копия.
- Не выдумывай директории и файлы, которых нет в дереве.
- ВАЖНО: выведи ТОЛЬКО готовый текст. Не рассуждай, не проверяй
  себя, не задавай вопросов.

## Примеры

Плохой ответ — повторяет одно и то же:

> Проект содержит директории `src/`, `tests/`, `docs/`. Ключевые
> директории: `src/` с исходным кодом, `tests/` с тестами, `docs/`
> с документацией. Внутри `src/` находится исходный код, в `tests/` —
> тесты, в `docs/` — документация.

Хороший ответ — каждый абзац про новое:

> Это Python-библиотека для работы с HTTP-запросами на asyncio.
>
> Ключевые директории: `src/` содержит публичный API и внутренние
> модули, `tests/` — юнит- и интеграционные тесты, `docs/` —
> документация в формате Sphinx.
>
> Стек: Python 3.12, pytest, httpx, mypy, GitHub Actions для CI.
PROMPT_EOF
    echo "[+] Создан $TREE_DETAILED"
else
    echo "[=] $TREE_DETAILED уже существует, не трогаю"
fi

# ── Итог ───────────────────────────────────────────────────────────────────
echo
echo "[+] Готово."
echo
echo "Дальше:"
echo "  1. Откройте workspace/sources.yaml и добавьте свои источники."
echo "  2. Запустите ./run.sh"
echo "  3. В REPL:"
echo "         /sources"
echo "         /tree myproject"
echo
echo "Prompt-файлы для guided mode:"
echo "  tree-summary.md           минимальная версия (1.7B+)"
echo "  tree-summary-detailed.md  полная версия с примерами (4B+)"
