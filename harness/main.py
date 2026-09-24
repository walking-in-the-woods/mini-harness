"""
Точка входа: REPL поверх HarnessAgent.

Читает .env (скаляры) и config.yaml (политики и маршруты).
Конкретных моделей и марок в коде нет — всё приходит из .env.

Бэкенд инференса выбирается через HARNESS_BACKEND:
  * ollama   (по умолчанию) — нативный ollama-python,
    адрес в OLLAMA_HOST.
  * llamacpp — прямой OpenAI-совместимый HTTP, адрес в
    LLAMACPP_HOST (обычно http://127.0.0.1:8080 или :8082).

Команды пользователя (не модели):
  /sources                     список источников
  /tree <name> [subpath]       дерево -> input/_tree_<name>.md
  /files <name> [subpath]      плоский список -> input/_files_<name>.md
  /dump <name> [subpath]       содержимое -> input/_dump_<name>.md
  /reload                      перечитать sources.yaml
  /batch <src> <glob> <prompt> [<target>]
                               пакетная обработка директории
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from harness.agent import HarnessAgent
from harness.audit import AuditLog
from harness.backends import BackendError, ChatBackend, build_backend
from harness.batch import BatchRunner
from harness.confirm import ConfirmSession
from harness.fs_guard import FileSystemGuard
from harness.proxy import ApiProxy
from harness.sources import SourceError, SourceRegistry


BANNER_TEMPLATE = """
============================================================
  Mini Harness — локальный ассистент без контейнеров

  backend:      {backend}
  model:        {model}
  host:         {host}
  num_ctx:      {num_ctx}
  num_predict:  {num_predict}
  keep_alive:   {keep_alive}
  sources:      {sources}

  /quit  — выход
  /reset — новая сессия (сброс состояния)
  /sources, /tree, /files, /dump, /reload — внешние источники
  /batch <src> <glob> <prompt> [<target>] — пакетная обработка
============================================================
"""


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"
ENV_PATH = BASE_DIR / ".env"


def _load_env(path: Path) -> None:
    """Простейший парсер .env: KEY=VALUE, # комментарии.

    .env ИМЕЕТ ПРИОРИТЕТ над унаследованным окружением. При
    конфликте печатает warning и перезаписывает.
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

    backend = os.environ.get("HARNESS_BACKEND", "ollama").strip().lower()

    # Модель обязательна только для ollama: llama-server обслуживает
    # одну модель, загруженную при старте, и поле "model" в запросе
    # фактически игнорируется. Пользователю всё равно нужна метка —
    # она летит в теле запроса и попадает в аудит.
    model = os.environ.get("HARNESS_MODEL", "").strip()
    if not model:
        if backend in ("llamacpp", "llama.cpp", "llama-cpp", "llama_cpp"):
            model = "llamacpp-local"
            print("[i] HARNESS_MODEL не задан; использую метку "
                  "'llamacpp-local' (llama-server игнорирует поле model)",
                  file=sys.stderr)
        else:
            print("[fatal] HARNESS_MODEL не задан в .env", file=sys.stderr)
            sys.exit(1)

    return {
        "backend": backend,
        "model": model,
        "ollama_host": os.environ.get(
            "OLLAMA_HOST", "http://127.0.0.1:11434"
        ),
        "llamacpp_host": os.environ.get(
            "LLAMACPP_HOST", "http://127.0.0.1:8080"
        ),
        "llamacpp_api_key": os.environ.get("LLAMACPP_API_KEY", "").strip(),
        # 300 секунд, не 30. На N100+7B Q4 генерация идёт ~2–4 t/s;
        # ответ на 1024 токена занимает 4–8 минут. 30 секунд
        # отваливается на середине.
        "llamacpp_timeout": _env_float("LLAMACPP_TIMEOUT", 300.0),
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


# ── Команды внешних источников ────────────────────────────────────────────

def _print_sources(registry: SourceRegistry,
                   workspace: Path) -> None:
    sources = registry.list()
    if not sources:
        print(f"[i] Источников нет. Создайте {workspace / 'sources.yaml'}")
        print(f"    (шаблон: {workspace / 'sources.yaml.example'})")
        return
    for s in sources:
        state = "ok" if s.exists else "недоступен"
        print(f"  {s.name:20} {state:12} {s.root}")
        if s.description:
            print(f"  {'':20} {s.description}")


def _write_to_input(workspace: Path, filename: str, body: str) -> Path:
    """Записать сгенерированный отчёт в workspace/input/.

    Пишем напрямую, минуя FileSystemGuard. Эти команды — привилегия
    пользователя, не модели. Guard применяется к тому, что предлагает
    модель через propose_write.
    """
    input_dir = workspace / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    target = input_dir / filename
    target.write_text(body, encoding="utf-8")
    return target


def _handle_source_command(user_input: str, registry: SourceRegistry,
                            workspace: Path) -> bool:
    """Обрабатывает /sources, /tree, /files, /dump, /reload."""
    if user_input == "/sources":
        _print_sources(registry, workspace)
        return True

    if user_input == "/reload":
        try:
            registry.reload()
            print(f"[+] sources.yaml перечитан "
                  f"({len(registry.list())} источников)")
        except SourceError as e:
            print(f"[!] {e}")
        return True

    for prefix, action in (
        ("/tree ", "tree"),
        ("/files ", "files"),
        ("/dump ", "dump"),
    ):
        if not user_input.startswith(prefix):
            continue
        rest = user_input[len(prefix):].strip()
        parts = rest.split(maxsplit=1)
        if not parts:
            print(f"[!] usage: {prefix}<source> [subpath]")
            return True
        name = parts[0]
        subpath = parts[1] if len(parts) > 1 else ""

        try:
            if action == "tree":
                body, count = registry.build_tree(name, subpath)
                fname = f"_tree_{name}.md"
                header = f"# Tree: {name}"
                if subpath:
                    header += f"/{subpath}"
                report = f"{header}\n\n{body}\n"
                _write_to_input(workspace, fname, report)
                print(f"[+] {fname}  ({count} entries)")
            elif action == "files":
                body, count = registry.build_file_list(name, subpath)
                fname = f"_files_{name}.md"
                header = f"# Files: {name}"
                if subpath:
                    header += f"/{subpath}"
                report = f"{header}\n\n```\n{body}\n```\n"
                _write_to_input(workspace, fname, report)
                print(f"[+] {fname}  ({count} files)")
            else:  # dump
                body, stats = registry.collect_dump(name, subpath)
                fname = f"_dump_{name}.md"
                _write_to_input(workspace, fname, body)
                print(f"[+] {fname}  "
                      f"({stats['files_included']} files, "
                      f"{stats['bytes_total']} bytes)")
                if stats["truncated"]:
                    print("[i] отчёт обрезан — увеличьте лимиты "
                          "в workspace/sources.yaml")
        except SourceError as e:
            print(f"[!] {e}")
        return True

    return False


# ── Команды batch ─────────────────────────────────────────────────────────

def _handle_batch_command(user_input: str, agent: HarnessAgent,
                           guard: FileSystemGuard, workspace: Path,
                           audit: AuditLog) -> bool:
    """Обрабатывает /batch.

    Формат: /batch <source_dir> <glob> <prompt_path> [<target_dir>]
    """
    if not user_input.startswith("/batch"):
        return False

    if user_input == "/batch":
        print("[!] usage: /batch <source_dir> <glob> <prompt_path> "
              "[<target_dir>]")
        print("    пример: /batch input/batch '*.py' "
              "input/prompts/add-docstrings-generic.md")
        return True

    rest = user_input[len("/batch"):].strip()
    parts = rest.split()
    if len(parts) < 3:
        print("[!] нужно минимум 3 аргумента: "
              "<source_dir> <glob> <prompt_path>")
        return True
    if len(parts) > 4:
        print("[!] максимум 4 аргумента: "
              "<source_dir> <glob> <prompt_path> [<target_dir>]")
        return True

    source_dir = parts[0]
    glob_pattern = parts[1].strip("'\"")
    prompt_path = parts[2]
    target_dir = parts[3] if len(parts) == 4 else None

    runner = BatchRunner(agent, guard, workspace, audit,
                          target_dir=target_dir)
    try:
        runner.run(source_dir, glob_pattern, prompt_path)
    except Exception as e:
        print(f"[!] batch failed: {type(e).__name__}: {e}")
    return True


# ── Диагностика бэкенда ───────────────────────────────────────────────────

def _backend_diagnose(backend: ChatBackend, cfg: dict) -> None:
    """Информационная проверка бэкенда. Не блокирует запуск.

    Печатает строку "[i] ..." если сервер не отвечает. Точная
    подсказка зависит от бэкенда: у ollama это "ollama serve
    запущен?", у llama.cpp — "llama-server запущен и модели
    подгружены через --load-mode mlock?".
    """
    try:
        ok = backend.health()
    except Exception:
        ok = False

    if ok:
        return

    if backend.name == "ollama":
        host = cfg.get("ollama_host", "?")
        print(f"[i] Ollama не отвечает на {host}.")
        print("    Убедитесь, что 'ollama serve' запущен, а модель "
              "из HARNESS_MODEL загружена.")
    elif backend.name == "llamacpp":
        host = cfg.get("llamacpp_host", "?")
        print(f"[i] llama-server не отвечает на {host}.")
        print("    Запустите ./Desktop/run-coder3b.sh (порт 8080) "
              "или run-coder7b.sh (порт 8082).")
        print("    Проверьте в браузере: " + host + "/health")
    else:
        print(f"[i] Бэкенд {backend.name!r} не отвечает. "
              f"Проверьте адрес и запуск сервера.")


# ── main ──────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = load_runtime_config()

    workspace = (BASE_DIR / cfg["workspace_root"]).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    # Бэкенд строим до баннера: если конфигурация неверная —
    # пользователь увидит понятную ошибку и не потратит время на
    # ввод запроса, который всё равно упадёт.
    try:
        backend = build_backend(cfg)
    except BackendError as e:
        print(f"[fatal] {e}", file=sys.stderr)
        sys.exit(1)

    try:
        registry = SourceRegistry(
            workspace_dir=workspace,
            global_blacklist=tuple(
                cfg["fs"].get("blacklist") or []
            ),
        )
    except SourceError as e:
        print(f"[fatal] sources.yaml: {e}", file=sys.stderr)
        sys.exit(1)

    host_label = (
        cfg["ollama_host"] if backend.name == "ollama"
        else cfg["llamacpp_host"]
    )
    print(BANNER_TEMPLATE.format(
        backend=backend.name,
        model=cfg["model"],
        host=host_label,
        num_ctx=cfg["num_ctx"],
        num_predict=cfg["num_predict"],
        keep_alive=cfg["keep_alive"],
        sources=len(registry.list()),
    ))

    # Информационная проверка, не блокирует REPL.
    _backend_diagnose(backend, cfg)

    fs_policy = {
        "root": str(workspace),
        "whitelist": cfg["fs"].get("whitelist", ["**"]),
        "blacklist": cfg["fs"].get("blacklist", []),
        "writable": cfg["fs"].get("writable", []),
        "ext_allow_paths": cfg["fs"].get("ext_allow_paths", []),
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
    audit.write("session_start",
                model=cfg["model"],
                backend=backend.name)

    agent = HarnessAgent(cfg, guard, proxy, audit, backend=backend)

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
            agent = HarnessAgent(cfg, guard, proxy, audit,
                                  backend=backend)
            print("[session reset]")
            continue

        if _handle_source_command(user_input, registry, workspace):
            continue

        if _handle_batch_command(user_input, agent, guard, workspace, audit):
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
