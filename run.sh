#!/usr/bin/env bash
# Запуск mini-harness. Одна команда — без Docker, без сертификатов.
set -euo pipefail

cd "$(dirname "$0")"

# ── Отказ от root ─────────────────────────────────────────────────────────
if [[ "${EUID}" -eq 0 ]]; then
    echo "[!] run.sh не должен запускаться от root." >&2
    echo "    Запустите от своего пользователя: ./run.sh" >&2
    exit 1
fi

# ── Проверка python3 и venv-модуля ────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo "[!] python3 не найден в PATH." >&2
    echo "    Установите: sudo apt install -y python3" >&2
    exit 1
fi

# ensurepip — то, чего не хватает без pythonX.Y-venv на Debian/Ubuntu.
# Проверяем его наличие до создания venv, чтобы дать внятный совет.
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
HOST="$(grep -E '^OLLAMA_HOST=' .env | cut -d= -f2- || true)"
HOST="${HOST:-http://127.0.0.1:11434}"
if command -v curl >/dev/null 2>&1; then
    if ! curl -sf "${HOST}/api/tags" >/dev/null 2>&1; then
        echo "[i] Локальный сервер не отвечает на ${HOST}."
        echo "    Убедитесь, что он запущен, и что модель из .env загружена."
    fi
fi

mkdir -p workspace/input workspace/output workspace/notes workspace/drafts logs

exec ./.venv/bin/python -m harness.main "$@"
