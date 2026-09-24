#!/usr/bin/env bash
# llama-server.sh — единый запускатель llama-server для нескольких
# моделей. Параметры каждой модели — в таблице MODELS ниже.
#
# Команды:
#   list              список моделей и их статус
#   status            что сейчас запущено
#   <alias>           запустить в foreground (Ctrl+C останавливает)
#   start <alias>     запустить в фоне (лог в $LOG_DIR/<alias>.log)
#   use <alias>       start + обновить .env (LLAMACPP_HOST, MODEL, NUM_CTX)
#   stop <alias>      остановить фоновый сервер
#   stop-all          остановить все серверы, запущенные через скрипт
#   logs <alias>      tail -f лога фонового сервера
#   env <alias>       напечатать строки для .env (без записи)
#
# Alias — короткое имя модели: 3b, 4b, 7b, ... (см. таблицу MODELS).

set -euo pipefail

# ── CONFIG ────────────────────────────────────────────────────────────────
LLAMA_DIR="${LLAMA_DIR:-$HOME/as-dev/llama.cpp}"
SERVER_BIN="$LLAMA_DIR/build/bin/llama-server"
MODELS_DIR="$LLAMA_DIR/models"

# Директория проекта mini-harness (родитель scripts/).
MINI_HARNESS_DIR="${MINI_HARNESS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="$MINI_HARNESS_DIR/.env"

# Куда писать PID, порт и лог фоновых серверов.
STATE_DIR="${XDG_RUNTIME_DIR:-/tmp}/llama-server"
mkdir -p "$STATE_DIR"
LOG_DIR="$STATE_DIR/logs"
mkdir -p "$LOG_DIR"

# Таймаут ожидания health-эндпоинта (секунды). 4B Q4 с --load-mode
# mlock стартует 15-25 с; 7B Q3 — 30-40 с.
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"

# Открывать Web UI в браузере после старта (true/false).
OPEN_BROWSER="${OPEN_BROWSER:-true}"

# Диапазон портов для fallback, если базовый порт занят.
PORT_SCAN_RANGE="${PORT_SCAN_RANGE:-10}"

# ── ТАБЛИЦА МОДЕЛЕЙ ───────────────────────────────────────────────────────
# Формат (6 полей, разделитель |):
#   alias | файл (в $MODELS_DIR) | base_port | ctx-size | chat-режим | описание
#
# chat-режим:
#   jinja   встроенный Jinja-шаблон из GGUF (Qwen3 — /no_think, tools)
#   chatml  generic chatml (Qwen2.5-Coder — стабильнее, чем jinja)
#   none    без явного шаблона
#
# base_port — предпочтительный порт. Если занят, скрипт возьмёт
# следующий свободный в диапазоне [base_port, base_port+PORT_SCAN_RANGE].
# Фактический порт сохраняется и используется командой `use`.
MODELS=(
  "3b|qwen2.5-coder-3b-instruct-q4_k_m.gguf|8080|4096|chatml|Qwen2.5-Coder 3B Q4_K_M — быстрая"
  "4b|Qwen3-4B-Instruct-2507-Q4_K_M.gguf|8081|4096|jinja|Qwen3-4B-Instruct-2507 Q4_K_M — batch, /no_think"
  "7b|Qwen2.5-Coder-7B-Instruct-Q3_K_M.gguf|8082|2048|chatml|Qwen2.5-Coder 7B Q3_K_M — максимум качества"
)

# ── УТИЛИТЫ ───────────────────────────────────────────────────────────────

die()  { echo "[!] $*" >&2; exit 1; }
info() { echo "[*] $*"; }
ok()   { echo "[+] $*"; }

lookup_model() {
    local want="$1"
    for row in "${MODELS[@]}"; do
        IFS='|' read -r alias file port ctx chat desc <<< "$row"
        if [[ "$alias" == "$want" ]]; then
            printf '%s|%s|%s|%s|%s\n' "$file" "$port" "$ctx" "$chat" "$desc"
            return 0
        fi
    done
    return 1
}

pid_file()  { echo "$STATE_DIR/$1.pid";  }
port_file() { echo "$STATE_DIR/$1.port"; }
log_file()  { echo "$LOG_DIR/$1.log";    }

# PID живого процесса для alias, или ничего.
running_pid() {
    local alias="$1" pf pid
    pf="$(pid_file "$alias")"
    [[ -f "$pf" ]] || return 1
    pid="$(cat "$pf" 2>/dev/null || true)"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null || { rm -f "$pf"; return 1; }
    grep -q 'llama-server' "/proc/$pid/cmdline" 2>/dev/null || {
        rm -f "$pf"; return 1;
    }
    echo "$pid"
}

# Фактический порт: если сервер запущен — из port_file; иначе base_port.
actual_port_of() {
    local alias="$1" pf row base
    pf="$(port_file "$alias")"
    if running_pid "$alias" >/dev/null; then
        if [[ -f "$pf" ]]; then
            cat "$pf"; return 0
        fi
        row="$(lookup_model "$alias")" || return 1
        IFS='|' read -r _ base _ _ _ <<< "$row"
        echo "$base"; return 0
    fi
    return 1
}

port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$port\$"
    else
        (echo >"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1
    fi
}

# Первый свободный порт в диапазоне [base, base+range].
find_free_port() {
    local base="$1" range="${2:-$PORT_SCAN_RANGE}" p
    for ((p = base; p <= base + range; p++)); do
        if ! port_in_use "$p"; then
            echo "$p"; return 0
        fi
    done
    return 1
}

wait_for_health() {
    local port="$1" pid="$2" timeout="$3"
    local url="http://127.0.0.1:${port}/health"
    printf '[*] Ожидаю готовности'
    for ((i=1; i<=timeout; i++)); do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo; die "llama-server завершился неожиданно (PID $pid)"
        fi
        if curl -sf -o /dev/null "$url" 2>/dev/null; then
            echo " OK (${i} с)"; return 0
        fi
        printf '.'; sleep 1
    done
    echo; die "таймаут ${timeout} с — сервер не ответил на $url"
}

# Обновить ключ в .env. Если строки нет — добавить в конец.
set_env() {
    local key="$1" value="$2" file="$3"
    if grep -q "^${key}=" "$file"; then
        # sed с разделителем | — value не должен содержать |
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$file"
    fi
}

# Подготовка аргументов chat-template.
chat_args_for() {
    local chat="$1"
    case "$chat" in
        jinja)  echo "--jinja" ;;
        chatml) echo "--chat-template chatml" ;;
        none)   echo "" ;;
        *) die "неизвестный chat-режим '$chat'" ;;
    esac
}

# Запуск сервера. Возвращает PID через глобальную LAST_PID.
# Аргумент $1=alias, $2=foreground|background.
spawn_server() {
    local alias="$1" mode="$2"
    local row
    row="$(lookup_model "$alias")" || die "unknown model alias: $alias"
    IFS='|' read -r file base_port ctx chat desc <<< "$row"

    local model_path="$MODELS_DIR/$file"
    [[ -f "$model_path" ]] || die "файл не найден: $model_path"
    [[ -x "$SERVER_BIN" ]] || die "llama-server не найден: $SERVER_BIN"

    # Найти свободный порт, начиная с base_port.
    local port
    port="$(find_free_port "$base_port")" \
        || die "нет свободного порта в диапазоне $base_port..$((base_port+PORT_SCAN_RANGE))"
    if [[ "$port" != "$base_port" ]]; then
        info "порт $base_port занят, использую $port"
    fi

    # shellcheck disable=SC2206  # splat по словам — это желаемое поведение
    local chat_args=( $(chat_args_for "$chat") )

    info "Запускаю $alias ($desc)"
    info "  модель: $model_path"
    info "  порт:   $port  (base: $base_port)"
    info "  ctx:    $ctx"
    info "  шаблон: $chat"
    echo

    cd "$LLAMA_DIR"

    if [[ "$mode" == "background" ]]; then
        local log; log="$(log_file "$alias")"
        nohup "$SERVER_BIN" \
            -m "$model_path" \
            -t 4 \
            --ctx-size "$ctx" \
            --load-mode mlock \
            --host 127.0.0.1 \
            --port "$port" \
            "${chat_args[@]}" \
            --flash-attn auto \
            >"$log" 2>&1 &
        LAST_PID=$!
        disown
        info "лог: $log"
    else
        "$SERVER_BIN" \
            -m "$model_path" \
            -t 4 \
            --ctx-size "$ctx" \
            --load-mode mlock \
            --host 127.0.0.1 \
            --port "$port" \
            "${chat_args[@]}" \
            --flash-attn auto &
        LAST_PID=$!
    fi

    echo "$LAST_PID" > "$(pid_file "$alias")"
    echo "$port"      > "$(port_file "$alias")"

    wait_for_health "$port" "$LAST_PID" "$HEALTH_TIMEOUT"

    if [[ "$OPEN_BROWSER" == "true" ]] && command -v xdg-open >/dev/null 2>&1; then
        xdg-open "http://127.0.0.1:$port" >/dev/null 2>&1 &
    fi
}

# ── КОМАНДЫ ───────────────────────────────────────────────────────────────

cmd_list() {
    printf '%-6s %-45s %-6s %-6s %-8s %s\n' \
        "ALIAS" "FILE" "PORT" "CTX" "CHAT" "STATUS"
    for row in "${MODELS[@]}"; do
        IFS='|' read -r alias file base_port ctx chat desc <<< "$row"
        local status="stopped" port="$base_port"
        if pid="$(running_pid "$alias")"; then
            port="$(actual_port_of "$alias" 2>/dev/null || echo "$base_port")"
            status="running (PID $pid, port $port)"
        fi
        local extra=""
        [[ -f "$MODELS_DIR/$file" ]] || extra=" [file missing]"
        printf '%-6s %-45s %-6s %-6s %-8s %s%s\n' \
            "$alias" "$file" "$port" "$ctx" "$chat" "$status" "$extra"
    done
}

cmd_status() {
    echo "[*] Через этот скрипт:"
    local found=0
    for row in "${MODELS[@]}"; do
        IFS='|' read -r alias _ _ _ _ desc <<< "$row"
        if pid="$(running_pid "$alias")"; then
            local port; port="$(actual_port_of "$alias" 2>/dev/null || echo '?')"
            echo "    $alias: PID $pid, port $port — $desc"
            found=1
        fi
    done
    [[ "$found" -eq 1 ]] || echo "    (ничего не запущено)"
    echo
    echo "[*] Чужие llama-server (не через скрипт):"
    pgrep -af llama-server 2>/dev/null || echo "    (нет)"
}

cmd_foreground() {
    local alias="$1"
    local pid
    if pid="$(running_pid "$alias")"; then
        die "$alias уже запущен (PID $pid). Используйте stop или другую модель."
    fi
    spawn_server "$alias" foreground
    ok "Сервер работает. Ctrl+C для остановки."
    ok "Health: curl -sf http://127.0.0.1:$(actual_port_of "$alias")/health"
    echo

    # Trap для корректного завершения по Ctrl+C
    trap 'echo; info "Останавливаю $alias (PID $LAST_PID)"; kill "$LAST_PID" 2>/dev/null; rm -f "$(pid_file "$alias")" "$(port_file "$alias")"; ok "Готово."' EXIT INT TERM
    wait "$LAST_PID"
}

cmd_start() {
    local alias="$1"
    local pid
    if pid="$(running_pid "$alias")"; then
        info "$alias уже запущен (PID $pid, порт $(actual_port_of "$alias"))"
        return 0
    fi
    spawn_server "$alias" background
    local port; port="$(actual_port_of "$alias")"
    ok "$alias запущен в фоне (PID $LAST_PID, порт $port)"
    ok "Health:   curl -sf http://127.0.0.1:$port/health"
    ok "Лог:      $(log_file "$alias")"
    ok "Остановка: $(basename "$0") stop $alias"
}

cmd_use() {
    local alias="$1"

    if ! running_pid "$alias" >/dev/null; then
        info "$alias не запущен. Запускаю в фоне..."
        cmd_start "$alias"
    else
        info "$alias уже запущен (PID $(running_pid "$alias"), порт $(actual_port_of "$alias"))"
    fi

    local row
    row="$(lookup_model "$alias")" || die "unknown alias: $alias"
    IFS='|' read -r file base_port ctx chat desc <<< "$row"
    local port; port="$(actual_port_of "$alias")"

    [[ -f "$ENV_FILE" ]] || die ".env не найден: $ENV_FILE"

    info "Обновляю $ENV_FILE:"
    set_env "HARNESS_BACKEND"  "llamacpp"                          "$ENV_FILE"
    set_env "HARNESS_MODEL"    "$alias"                            "$ENV_FILE"
    set_env "LLAMACPP_HOST"    "http://127.0.0.1:$port"            "$ENV_FILE"
    set_env "HARNESS_NUM_CTX"  "$ctx"                              "$ENV_FILE"

    echo
    ok "Активные настройки:"
    grep -E '^(HARNESS_BACKEND|HARNESS_MODEL|LLAMACPP_HOST|HARNESS_NUM_CTX|LLAMACPP_TIMEOUT)=' "$ENV_FILE"
    echo
    ok "Перезапустите harness: ./run.sh"
}

cmd_stop() {
    local alias="$1"
    local pid
    if ! pid="$(running_pid "$alias")"; then
        info "$alias не запущен через этот скрипт."
        rm -f "$(pid_file "$alias")" "$(port_file "$alias")"
        return 0
    fi
    info "Останавливаю $alias (PID $pid)"
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        info "Процесс не завершился, SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$(pid_file "$alias")" "$(port_file "$alias")"
    ok "$alias остановлен."
}

cmd_stop_all() {
    local stopped=0
    for row in "${MODELS[@]}"; do
        IFS='|' read -r alias _ _ _ _ _ <<< "$row"
        if running_pid "$alias" >/dev/null; then
            cmd_stop "$alias"
            stopped=$((stopped + 1))
        fi
    done
    [[ "$stopped" -gt 0 ]] || info "Ничего не запущено через этот скрипт."
}

cmd_logs() {
    local alias="$1"
    local log; log="$(log_file "$alias")"
    [[ -f "$log" ]] || die "лог не найден: $log (сервер в фоне запускался?)"
    info "tail -f $log  (Ctrl+C для выхода)"
    exec tail -f "$log"
}

cmd_env() {
    local alias="$1"
    local row
    row="$(lookup_model "$alias")" || die "unknown alias: $alias"
    IFS='|' read -r file base_port ctx chat desc <<< "$row"
    local port="$base_port"
    running_pid "$alias" >/dev/null && port="$(actual_port_of "$alias")"
    cat <<EOF
HARNESS_BACKEND=llamacpp
HARNESS_MODEL=$alias
LLAMACPP_HOST=http://127.0.0.1:$port
HARNESS_NUM_CTX=$ctx
EOF
}

# ── MAIN ──────────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Использование:
  $(basename "$0") list                 список моделей
  $(basename "$0") status               что сейчас запущено
  $(basename "$0") <alias>              запустить в foreground (Ctrl+C — стоп)
  $(basename "$0") start <alias>        запустить в фоне (лог в файл)
  $(basename "$0") use <alias>          start + обновить .env
  $(basename "$0") stop <alias>         остановить
  $(basename "$0") stop-all             остановить все
  $(basename "$0") logs <alias>         tail -f лога
  $(basename "$0") env <alias>          напечатать строки для .env
EOF
}

main() {
    if [[ $# -eq 0 ]]; then
        usage
        echo
        cmd_list
        exit 1
    fi
    case "$1" in
        list)     cmd_list ;;
        status)   cmd_status ;;
        stop-all) cmd_stop_all ;;
        stop)     [[ $# -ge 2 ]] || die "укажите alias"; cmd_stop "$2" ;;
        start)    [[ $# -ge 2 ]] || die "укажите alias"; cmd_start "$2" ;;
        use)      [[ $# -ge 2 ]] || die "укажите alias"; cmd_use "$2" ;;
        logs)     [[ $# -ge 2 ]] || die "укажите alias"; cmd_logs "$2" ;;
        env)      [[ $# -ge 2 ]] || die "укажите alias"; cmd_env "$2" ;;
        -h|--help|help) usage ;;
        -*)       die "неизвестный флаг: $1" ;;
        *)        cmd_foreground "$1" ;;
    esac
}

main "$@"
