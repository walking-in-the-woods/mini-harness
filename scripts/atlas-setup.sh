#!/usr/bin/env bash
# atlas-setup.sh — управление семантическим графом Project Atlas
# без запуска REPL и без сервера инференса.
#
# Зачем нужен отдельный скрипт, если есть /atlas-команды:
#   1. /atlas работает только внутри REPL, а REPL требует бэкенд
#      (ollama/llama.cpp) — иначе смысла в нём нет.
#   2. Проиндексировать проект удобно ДО первого запуска REPL,
#      чтобы старт был мгновенным (auto_index_on_start: false).
#   3. Из cron / CI / git hook / из редактора.
#
# Команды:
#   init | rebuild   полная переиндексация (создаёт БД, если её нет)
#   refresh          инкрементальная переиндексация (по SHA-256)
#   stats            статистика: файлы, узлы, рёбра, размер БД
#   clean            удалить graph.db и atlas.log (с запросом)
#   help             эта справка
#
# Не требует: llama-server, ollama, GPU, сети. Только Python + PyYAML.
# Используется .venv/bin/python, если он есть, иначе python3.
#
# Использование:
#   scripts/atlas-setup.sh init
#   scripts/atlas-setup.sh refresh
#   scripts/atlas-setup.sh stats
#   scripts/atlas-setup.sh clean
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

die() { echo "[!] $*" >&2; exit 1; }

# ── Python ─────────────────────────────────────────────────────────────────
if [[ -x .venv/bin/python ]]; then
    PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    die "python3 не найден в PATH и .venv отсутствует."
fi

# ── Проверка импорта ───────────────────────────────────────────────────────
if ! "$PYTHON" -c "import harness.atlas.cli" >/dev/null 2>&1; then
    die "harness.atlas.cli не импортируется.
    Один раз выполните ./run.sh (создаст .venv и поставит зависимости),
    либо установите вручную:
        $PYTHON -m pip install -r requirements.txt"
fi

# ── Справка ────────────────────────────────────────────────────────────────
if [[ $# -eq 0 ]] || [[ "$1" == "help" ]] || \
   [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi

# ── clean с подтверждением ─────────────────────────────────────────────────
if [[ "$1" == "clean" ]]; then
    read -r -p "Удалить graph.db и atlas.log? [y/N] " reply
    case "$reply" in
        y|Y|yes|YES) ;;
        *) echo "[atlas] cancelled"; exit 0 ;;
    esac
fi

# ── Остальные команды — в Python ───────────────────────────────────────────
exec "$PYTHON" -m harness.atlas.cli "$@"
