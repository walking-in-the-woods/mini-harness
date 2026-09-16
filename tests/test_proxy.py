"""Тесты ApiProxy. Реальная сеть не используется."""

from __future__ import annotations

import json

import pytest

from harness.proxy import ApiProxy


ROUTES = {
    "weather": "https://api.example-1.test/weather",
    "translate": "https://api.example-2.test/translate",
}


@pytest.fixture
def proxy() -> ApiProxy:
    return ApiProxy(
        {"routes": dict(ROUTES)},
        timeout=5.0, max_request=1000, max_response=2000,
    )


@pytest.fixture
def fake_http(monkeypatch):
    """Подменяет httpx.Client. Возвращает state-словарь для проверок."""
    state = {
        "init_kwargs": {},
        "last_call": {},
        "response": (200, '{"ok":true}'),
    }

    class _Resp:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

    class _Client:
        def __init__(self, **kw):
            state["init_kwargs"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            state["last_call"] = {"method": "GET", "url": url,
                                  "params": params}
            return _Resp(*state["response"])

        def post(self, url, params=None, json=None):
            state["last_call"] = {"method": "POST", "url": url,
                                  "params": params, "json": json}
            return _Resp(*state["response"])

    monkeypatch.setattr("harness.proxy.httpx.Client", _Client)
    return state


# --------------------------------------------------------------------------
# Ошибки конфигурации / валидации
# --------------------------------------------------------------------------

def test_unknown_route_returns_error(proxy: ApiProxy):
    out = proxy.call("does-not-exist", "GET", {}, None)
    assert out.startswith("ERROR")
    assert "unknown route" in out


def test_bad_method_returns_error(proxy: ApiProxy):
    out = proxy.call("weather", "DELETE", {}, None)
    assert out.startswith("ERROR")
    assert "GET or POST" in out


def test_request_body_too_large(proxy: ApiProxy):
    big = {"data": "x" * 5000}
    out = proxy.call("weather", "POST", {}, big)
    assert out.startswith("ERROR")
    assert "too large" in out


# --------------------------------------------------------------------------
# Успешный путь
# --------------------------------------------------------------------------

def test_get_uses_route_host(proxy: ApiProxy, fake_http):
    out = proxy.call("weather", "GET", {"lat": "55"}, None)

    assert fake_http["last_call"]["method"] == "GET"
    assert fake_http["last_call"]["url"] == ROUTES["weather"]
    assert fake_http["last_call"]["params"] == {"lat": "55"}

    payload = json.loads(out)
    assert payload["status"] == 200
    assert payload["body"] == '{"ok":true}'


def test_post_uses_route_host(proxy: ApiProxy, fake_http):
    proxy.call("translate", "POST", {"langpair": "en|ru"},
               {"text": "hello"})
    assert fake_http["last_call"]["method"] == "POST"
    assert fake_http["last_call"]["url"] == ROUTES["translate"]
    assert fake_http["last_call"]["json"] == {"text": "hello"}


def test_follow_redirects_disabled(proxy: ApiProxy, fake_http):
    proxy.call("weather", "GET", {}, None)
    assert fake_http["init_kwargs"].get("follow_redirects") is False


def test_timeout_passed_to_client(proxy: ApiProxy, fake_http):
    proxy.call("weather", "GET", {}, None)
    assert fake_http["init_kwargs"].get("timeout") == 5.0


# --------------------------------------------------------------------------
# Лимит ответа
# --------------------------------------------------------------------------

def test_response_truncated(proxy: ApiProxy, fake_http):
    fake_http["response"] = (200, "x" * 5000)
    out = proxy.call("weather", "GET", {}, None)
    payload = json.loads(out)
    assert len(payload["body"]) == 2000


def test_all_routes_are_https():
    """Любой маршрут из config.yaml — только https."""
    from pathlib import Path
    import yaml
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    for name, url in (cfg.get("proxy") or {}).get("routes", {}).items():
        assert url.startswith("https://"), f"route {name}: not https"
