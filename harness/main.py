"""
Точка входа: REPL поверх HarnessAgent.

Читает .env (скаляры) и config.yaml (политики и маршруты).
Конкретных моделей и марок в коде нет — всё приходит из .env.

Приоритет источников: .env выигрывает у унаследованного окружения.
Если переменная уже была в os.environ с другим значением, файл её
перезапишет и напечатает warning. Это сделано специально: правка
.env должна применяться без «магических» условий. Пользователь,
который хочет переопределить значение на один запуск, может
отредактировать .env или задать переменную после загрузки
(например, через отдельный wrapper-скрипт).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from harness.agent import HarnessAgent
from harness.audit import AuditLog
from harness.confirm import ConfirmSession
from harness.fs_guard import FileSystemGuard
from harness.proxy import ApiProxy


BANNER_TEMPLATE = """
============================================================
  Mini Harness — локальный ассистент без контейнеров

  model:        {model}
  host:         {host}
  num_ctx:      {num_ctx}
  num_predict:  {num_predict}
  keep_alive:   {keep_alive}

  /quit  — выход
  /reset — новая сессия (сброс состояния)
============================================================
"""


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"
ENV_PATH = BASE_DIR / ".env"


def _load_env(path: Path) -> None:
    """Простейший парсер .env: KEY=VALUE, # комментарии, пустые строки.

    .env ИМЕЕТ ПРИОРИТЕТ над унаследованным окружением. Если
    переменная уже была в os.environ с другим значением — печатаем
    warning и перезаписываем. Это защищает от ситуации «поменял
    .env, а модель та же»: раньше унаследованное значение молча
    оставалось, а источник ошибки было не найти.

    Кавычки вокруг значения снимаются. Комментарии после значения
    не поддерживаются (только целая строка, начинающаяся с #).
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        inherited = os.environ.get(key)
        if inherited is not None and inherited != value:
            print(f"[warn] {key}: .env={value!r} переопределяет "
                  f"унаследованное окружение {inherited!r}",
                  file=sys.stderr)
        os.environ[key] = value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[warn] {name}={raw!r} не число, использую {default}",
              file=sys.stderr)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[warn] {name}={raw!r} не число, использую {default}",
              file=sys.stderr)
        return default


def load_runtime_config() -> dict:
    _load_env(ENV_PATH)

    if not CONFIG_PATH.is_file():
        print(f"[fatal] config not found: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)

    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    fs_cfg = raw.get("fs") or {}
    proxy_cfg = raw.get("proxy") or {}

    model = os.environ.get("HARNESS_MODEL", "").strip()
    if not model:
        print("[fatal] HARNESS_MODEL не задан в .env", file=sys.stderr)
        sys.exit(1)

    return {
        "model": model,
        "ollama_host": os.environ.get(
            "OLLAMA_HOST", "http://127.0.0.1:11434"
        ),
        "num_ctx": _env_int("HARNESS_NUM_CTX", 2048),
        "num_predict": _env_int("HARNESS_NUM_PREDICT", 1024),
        "keep_alive": os.environ.get("HARNESS_KEEP_ALIVE", "5m"),
        "temperature": _env_float("HARNESS_TEMPERATURE", 0.1),
        "workspace_root": os.environ.get(
            "HARNESS_WORKSPACE", "./workspace"
        ),
        "audit_path": os.environ.get(
            "HARNESS_AUDIT_PATH", "./logs/audit.jsonl"
        ),
        "max_tool_rounds": _env_int("HARNESS_MAX_TOOL_ROUNDS", 4),
        "max_read_bytes": _env_int("HARNESS_MAX_READ_BYTES", 200_000),
        "max_write_bytes": _env_int("HARNESS_MAX_WRITE_BYTES", 1_000_000),
        "max_list_entries": _env_int("HARNESS_MAX_LIST_ENTRIES", 500),
        "fs": fs_cfg,
        "proxy": proxy_cfg,
        "proxy_timeout": _env_float("HARNESS_PROXY_TIMEOUT", 30.0),
        "proxy_max_request": _env_int(
            "HARNESS_PROXY_MAX_REQUEST_BYTES", 100_000
        ),
        "proxy_max_response": _env_int(
            "HARNESS_PROXY_MAX_RESPONSE_BYTES", 200_000
        ),
    }


def main() -> None:
    cfg = load_runtime_config()

    # Баннер печатается ПОСЛЕ загрузки конфига, чтобы показать
    # фактически применённые значения. Пользователь видит модель
    # до первого запроса, а не только в [*]-строке от агента.
    print(BANNER_TEMPLATE.format(
        model=cfg["model"],
        host=cfg["ollama_host"],
        num_ctx=cfg["num_ctx"],
        num_predict=cfg["num_predict"],
        keep_alive=cfg["keep_alive"],
    ))

    workspace = (BASE_DIR / cfg["workspace_root"]).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    fs_policy = {
        "root": str(workspace),
        "whitelist": cfg["fs"].get("whitelist", ["**"]),
        "blacklist": cfg["fs"].get("blacklist", []),
        "writable": cfg["fs"].get("writable", []),
    }
    try:
        guard = FileSystemGuard(fs_policy)
    except Exception as e:
        print(f"[fatal] cannot initialize fs guard: {e}", file=sys.stderr)
        sys.exit(1)

    proxy = ApiProxy(
        cfg["proxy"],
        timeout=cfg["proxy_timeout"],
        max_request=cfg["proxy_max_request"],
        max_response=cfg["proxy_max_response"],
    )

    audit_path = (BASE_DIR / cfg["audit_path"]).resolve()
    audit = AuditLog(str(audit_path))
    audit.write("session_start", model=cfg["model"])

    agent = HarnessAgent(cfg, guard, proxy, audit)

    while True:
        try:
            user_input = input("\n>>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[exit]")
            break

        if not user_input:
            continue
        if user_input in ("/quit", "/exit"):
            break
        if user_input == "/reset":
            agent = HarnessAgent(cfg, guard, proxy, audit)
            print("[session reset]")
            continue

        try:
            result = agent.run(user_input)
        except Exception as e:
            print(f"[error] {type(e).__name__}: {e}")
            continue

        print("\n--- MODEL ---")
        print(result["text"])

        if result["pending_writes"]:
            session = ConfirmSession(workspace, guard)
            print()
            print(session.render_preview(result["pending_writes"]))

            try:
                code = input("code> ").strip()
            except (EOFError, KeyboardInterrupt):
                code = ""
                print()

            try:
                outcomes = session.apply(result["pending_writes"], code)
            except Exception as e:
                outcomes = [f"[ERROR] apply failed: "
                            f"{type(e).__name__}: {e}"]

            print()
            for line in outcomes:
                print(line)

            audit.write(
                "apply_result",
                outcomes=outcomes,
                nonce_matched=all(
                    "[CANCELLED]" not in o for o in outcomes
                ),
            )

            for line, w in zip(outcomes, result["pending_writes"]):
                if line.startswith("[OK]"):
                    agent.mark_written(w["canonical"])

    audit.write("session_end")


if __name__ == "__main__":
    main()
