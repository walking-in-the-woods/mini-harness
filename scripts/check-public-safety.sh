#!/usr/bin/env bash
# check-public-safety.sh — аудит проекта перед публикацией.
#
# Ищет в проекте потенциально чувствительные данные, которые не
# должны попасть в публичный репозиторий:
#
#   * реальные имена пользователей в путях (/home/<user>/, /Users/<user>/)
#   * API-ключи, токены, пароли в коде
#   * PEM-сертификаты и приватные ключи
#   * содержимое .env с реальными секретами (не placeholder)
#   * не закоммичены ли игнорируемые файлы (workspace/output, logs)
#
# Не заменяет ручную проверку, но ловит типичные утечки.
#
# ВАЖНО: запускать БЕЗ sudo. Скрипт только читает файлы, sudo не нужен
# и может испортить права доступа к рабочему дереву git.
#
# Использование:
#   bash scripts/check-public-safety.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ "${EUID}" -eq 0 ]]; then
    echo "[!] Не запускайте скрипт через sudo." >&2
    echo "    Только чтение файлов, root не нужен." >&2
    exit 1
fi

FOUND=0
warn() { echo "[!] $*"; FOUND=1; }
ok()   { echo "[+] $*"; }
info() { echo "[*] $*"; }

echo "============================================================"
echo "  Аудит безопасности перед публикацией"
echo "============================================================"
echo

# ── 1. Git status: не закоммичены ли чувствительные файлы ─────────────────
info "1. Проверка git status"
if command -v git >/dev/null 2>&1 && [[ -d .git ]]; then
    tracked_env=$(git ls-files .env 2>/dev/null || true)
    if [[ -n "$tracked_env" ]]; then
        warn ".env закоммичен в репозиторий"
    else
        ok ".env не закоммичен"
    fi

    tracked_logs=$(git ls-files 'logs/*' 2>/dev/null | grep -v '.gitkeep' || true)
    if [[ -n "$tracked_logs" ]]; then
        warn "в git отслеживаются логи: $tracked_logs"
    else
        ok "логи не закоммичены (кроме .gitkeep)"
    fi

    tracked_ws=$(git ls-files 'workspace/*' 2>/dev/null | grep -v '.gitkeep' || true)
    if [[ -n "$tracked_ws" ]]; then
        warn "в git отслеживается содержимое workspace/: $tracked_ws"
    else
        ok "workspace/ не закоммичен (кроме .gitkeep)"
    fi

    tracked_venv=$(git ls-files '.venv/*' 2>/dev/null | head -5 || true)
    if [[ -n "$tracked_venv" ]]; then
        warn ".venv закоммичен в репозиторий"
    else
        ok ".venv не закоммичен"
    fi
else
    info "git-репозиторий не найден — пропускаю проверку"
fi
echo

# ── 2. Имена пользователей в путях ────────────────────────────────────────
info "2. Поиск реальных имён пользователей в путях"
user_leaks=$(grep -rEn '/home/([a-z][a-z0-9_-]*)/' \
                --include='*.py' --include='*.sh' --include='*.md' \
                --include='*.yaml' --include='*.yml' --include='*.txt' \
                --exclude-dir=.git --exclude-dir=.venv \
                --exclude-dir=node_modules --exclude-dir=workspace \
                . 2>/dev/null \
             | grep -vE '/home/(user|USER|<user>|example|username|yourname)/' \
             || true)
if [[ -n "$user_leaks" ]]; then
    warn "найдены пути с потенциальным именем пользователя:"
    echo "$user_leaks" | sed 's/^/    /'
else
    ok "путей с /home/<name>/ не найдено"
fi

mac_leaks=$(grep -rEn '/Users/([a-z][a-z0-9_-]*)/' \
                --include='*.py' --include='*.sh' --include='*.md' \
                --include='*.yaml' --include='*.yml' --include='*.txt' \
                --exclude-dir=.git --exclude-dir=.venv \
                --exclude-dir=node_modules --exclude-dir=workspace \
                . 2>/dev/null \
             | grep -vE '/Users/(user|USER|<user>|example|username|yourname)/' \
             || true)
if [[ -n "$mac_leaks" ]]; then
    warn "найдены пути с потенциальным именем пользователя (macOS):"
    echo "$mac_leaks" | sed 's/^/    /'
fi
echo

# ── 3. Секреты: API-ключи, токены, пароли ─────────────────────────────────
info "3. Поиск захардкоженных секретов"
secret_hits=$(grep -rEn \
    '(api[_-]?key|secret|token|password|passwd|pwd)[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9_-]{16,}' \
    --include='*.py' --include='*.sh' --include='*.yaml' --include='*.yml' \
    --exclude-dir=.git --exclude-dir=.venv \
    --exclude-dir=node_modules --exclude-dir=workspace \
    --exclude-dir=docs \
    . 2>/dev/null \
    | grep -vE '(replace|placeholder|your-|example|xxxx|XXXX|<.*>|\$\{)' \
    || true)
if [[ -n "$secret_hits" ]]; then
    warn "найдены подозрительные присваивания:"
    echo "$secret_hits" | sed 's/^/    /'
else
    ok "захардкоженных секретов не найдено"
fi
echo

# ── 4. PEM-ключи и сертификаты ────────────────────────────────────────────
info "4. Поиск приватных ключей и сертификатов"
pem_files=$(find . -type f \
              \( -name '*.pem' -o -name '*.key' -o -name '*.crt' \) \
              -not -path './.git/*' -not -path './.venv/*' \
              -not -path './workspace/*' \
              2>/dev/null || true)
if [[ -n "$pem_files" ]]; then
    warn "найдены файлы ключей/сертификатов:"
    echo "$pem_files" | sed 's/^/    /'
else
    ok "PEM-файлов не найдено"
fi

pem_content=$(grep -rl \
    --include='*.md' --include='*.txt' --include='*.py' --include='*.sh' \
    -E 'BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY' \
    --exclude-dir=.git --exclude-dir=.venv \
    . 2>/dev/null || true)
if [[ -n "$pem_content" ]]; then
    warn "найдены встроенные приватные ключи:"
    echo "$pem_content" | sed 's/^/    /'
fi
echo

# ── 5. .env с реальными секретами ─────────────────────────────────────────
# В .env живут и настройки harness, и секреты. Настройки (числа,
# строки вроде "5m", пути) — не секреты, их не надо помечать.
# Секреты — только те переменные, чьё значение может дать доступ
# к чему-либо: PROXY_SECRET, GATEWAY_HMAC_KEY, *_GATEWAY_TOKEN.
#
# Именно на них и проверяем. Остальные ключи игнорируются явно,
# списком. Если добавляете новую переменную с секретом — добавьте
# её в SENSITIVE_KEYS ниже.
info "5. Проверка .env на реальные секреты"
if [[ -f .env ]]; then
    SENSITIVE_KEYS='^(PROXY_SECRET|GATEWAY_HMAC_KEY|[A-Z_]*GATEWAY_TOKEN)='

    # Отбираем только строки с чувствительными ключами
    sensitive_lines=$(grep -E "$SENSITIVE_KEYS" .env 2>/dev/null || true)

    # Из них — только те, где значение не placeholder
    real_secrets=$(echo "$sensitive_lines" \
        | grep -vE '=(replace|REPLACE|your|YOUR|<|\$\{|$)' \
        || true)

    if [[ -n "$real_secrets" ]]; then
        warn ".env содержит непустые значения в чувствительных переменных:"
        echo "$real_secrets" | sed 's/=.*/=<redacted>/' | sed 's/^/    /'
        echo "    (значения не выводятся, но файл стоит проверить)"
        echo "    Убедитесь, что .env не коммитится (см. пункт 6)."
    else
        ok ".env не содержит реальных секретов в чувствительных переменных"
    fi
else
    ok ".env не существует (нормально для публичного клона)"
fi
echo

# ── 6. Опечатки в .gitignore ──────────────────────────────────────────────
info "6. Проверка .gitignore"
if [[ -f .gitignore ]]; then
    for pattern in '.env' '.venv' 'workspace/*' 'logs/*'; do
        if grep -qF "$pattern" .gitignore; then
            ok ".gitignore содержит '$pattern'"
        else
            warn ".gitignore не содержит '$pattern'"
        fi
    done
else
    warn ".gitignore отсутствует"
fi
echo

# ── 7. Личные данные в тестовых данных ───────────────────────────────────
info "7. Поиск личных email в коде/тестах"
pii_hits=$(grep -rEn \
    '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b' \
    --include='*.py' --include='*.md' --include='*.yaml' \
    --exclude-dir=.git --exclude-dir=.venv \
    --exclude-dir=docs --exclude-dir=workspace \
    . 2>/dev/null | grep -vE '@(example|test|localhost)' || true)
if [[ -n "$pii_hits" ]]; then
    warn "найдены email-адреса в коде/тестах:"
    echo "$pii_hits" | sed 's/^/    /'
else
    ok "личных email в коде/тестах не найдено"
fi
echo

# ── Итог ──────────────────────────────────────────────────────────────────
echo "============================================================"
if [[ "$FOUND" -eq 0 ]]; then
    echo "  Автоматические проверки пройдены."
    echo "  Рекомендуется ручная проверка списка ниже."
else
    echo "  Найдены потенциальные проблемы. Проверьте вывод выше."
fi
echo "============================================================"
echo
echo "Ручные проверки перед публикацией:"
echo "  1. Открыть docs/experiments/ и убедиться, что в логах нет"
echo "     реальных путей (заменены на <project_root>)."
echo "  2. Открыть .env.example и убедиться, что все значения — placeholder."
echo "  3. Проверить git log -p на наличие случайно закоммиченных"
echo "     секретов в истории (git log --all -p | grep -iE 'secret|token|password')."
echo "  4. Проверить README.md и INSTALL-*.md на реальные имена хостов."
echo "  5. Если репозиторий публикуется впервые — проверить .github/workflows"
echo "     на наличие секретов GitHub Actions (Settings → Secrets)."
echo
exit "$FOUND"
