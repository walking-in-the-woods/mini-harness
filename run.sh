#!/usr/bin/env bash
# Запуск mini-harness. Одна команда — без Docker, без сертификатов.
#
# Что делает:
#   1. Отказывается запускаться от root (иначе файлы в workspace/
#      и logs/ получат владельца root:root).
#   2. Проверяет наличие python3 и модуля ensurepip. На Debian/Ubuntu
#      без пакета pythonX.Y-venv модуль отсутствует, и создание venv
#      падает с непонятным трейсбеком. Здесь диагностика явная.
#   3. Проверяет, что .venv рабочий (импортируются все три
#      зависимости), а не только существует.
#   4. Создаёт .env из .env.example, если файла нет.
#   5. Информационно проверяет, отвечает ли сервер инференса.
#      Не блокирует запуск.
#   6. Инициализирует workspace через scripts/setup-workspace.sh:
#      дерево каталогов, sources.yaml.example, sources.yaml,
#      prompt-файлы. Идемпотентно: при повторных запусках —
#      ничего не перезаписывает.
#   7. Запускает REPL.
set -euo pipefail

cd "$(dirname "$0")"

# ── Отказ от root ─────────────────────────────────────────────────────────
if [[ "${EUID}" -eq 0 ]]; then
    echo "[!] run.sh не должен запускаться от root." >&2
    echo "    Запустите от своего пользователя: ./run.sh" >&2
    echo "    Если уже что-то создано от root:" >&2
    echo "        sudo chown -R \"\$USER:\$USER\" .venv workspace logs" >&2
    exit 1
fi

# ── Проверка python3 ──────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo "[!] python3 не найден в PATH." >&2
    echo "    Установите: sudo apt install -y python3" >&2
    exit 1
fi

# ── Проверка ensurepip ────────────────────────────────────────────────────
# ensurepip — то, чего не хватает без pythonX.Y-venv на Debian/Ubuntu.
# Проверяем до создания venv, чтобы дать внятный совет, а не трейсбек
# из недр venv.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    echo "[!] Модуль ensurepip недоступен — venv не сможет установить pip." >&2
    echo "" >&2
    echo "    На Debian/Ubuntu установите пакет:" >&2
    echo "        sudo apt install -y python${PY_VER}-venv" >&2
    echo "" >&2
    echo "    Если такого пакета нет, посмотрите доступные:" >&2
    echo "        apt-cache search python3-venv" >&2
    exit 1
fi

# ── Проверка, что .venv рабочий ───────────────────────────────────────────
# Тестируем импорт всех трёх зависимостей. Если хотя бы одна не
# импортируется — venv считается сломанным (например, прерванный
# pip install оставил пустую директорию) и пересоздаётся.
venv_ok=0
if [[ -x .venv/bin/python ]]; then
    if ./.venv/bin/python -c \
        "import yaml, ollama, httpx" >/dev/null 2>&1; then
        venv_ok=1
    else
        echo "[i] .venv существует, но зависимости не импортируются."
        echo "    Пересоздаю."
        rm -rf .venv
    fi
fi

if [[ "$venv_ok" -eq 0 ]]; then
    echo "[*] Создаю .venv и устанавливаю зависимости..."
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip
    ./.venv/bin/pip install -r requirements.txt
fi

# ── .env ──────────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "[+] .env создан из .env.example."
fi

# ── Информационная проверка сервера инференса ─────────────────────────────
# Не блокирует запуск: если сервер поднялся через 10 секунд после
# run.sh, REPL уже ждёт и следующий ввод сработает. Если curl нет —
# проверка пропускается.
HOST="$(grep -E '^OLLAMA_HOST=' .env | cut -d= -f2- || true)"
HOST="${HOST:-http://127.0.0.1:11434}"
if command -v curl >/dev/null 2>&1; then
    if ! curl -sf "${HOST}/api/tags" >/dev/null 2>&1; then
        echo "[i] Локальный сервер не отвечает на ${HOST}."
        echo "    Убедитесь, что он запущен, и что модель из .env загружена."
    fi
fi

# ── Инициализация workspace ───────────────────────────────────────────────
# На первом запуске после клона workspace/ пуст — всё содержимое
# матчится `workspace/*` в .gitignore. setup-workspace.sh создаёт
# дерево каталогов, sources.yaml.example, sources.yaml и prompt-файлы.
# Идемпотентен: при повторных запусках ничего не перезаписывает.
if [[ -f scripts/setup-workspace.sh ]]; then
    bash scripts/setup-workspace.sh
else
    # Fallback для старых клонов, где скрипта ещё нет.
    mkdir -p workspace/input workspace/output \
             workspace/notes workspace/drafts logs
fi

# ── Запуск REPL ───────────────────────────────────────────────────────────
exec ./.venv/bin/python -m harness.main "$@"
