#!/usr/bin/env bash
# Запуск mini-harness. Одна команда — без Docker, без сертификатов.
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
    echo "[*] Создаю .venv и устанавливаю зависимости..."
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip
    ./.venv/bin/pip install -r requirements.txt
fi

if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "[+] .env создан из .env.example."
fi

# Информационная проверка: отвечает ли локальный сервер.
# Не блокирует запуск — REPL сам сообщит об ошибке, если сервер лёг.
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
