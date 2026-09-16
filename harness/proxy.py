"""
In-process HTTP-клиент к whitelisted внешним API.

Хост берётся из config.yaml (routes), НЕ из аргументов модели.
follow_redirects=False. Лимиты на request/response body.
"""

from __future__ import annotations

import json

import httpx


class ApiProxy:

    def __init__(self, cfg: dict, *, timeout: float,
                 max_request: int, max_response: int):
        self.routes: dict[str, str] = cfg.get("routes", {})
        self.timeout = float(timeout)
        self.max_req = int(max_request)
        self.max_resp = int(max_response)

    def call(self, route: str, method: str,
             params: dict | None, body: dict | None) -> str:
        upstream = self.routes.get(route)
        if not upstream:
            return f"ERROR: unknown route {route!r}"

        if method not in ("GET", "POST"):
            return "ERROR: method must be GET or POST"

        if body is not None:
            try:
                body_bytes = json.dumps(body).encode("utf-8")
            except (TypeError, ValueError) as e:
                return f"ERROR: bad body: {e}"
            if len(body_bytes) > self.max_req:
                return f"ERROR: request body too large ({len(body_bytes)} bytes)"

        try:
            with httpx.Client(timeout=self.timeout,
                              follow_redirects=False) as client:
                if method == "GET":
                    resp = client.get(upstream, params=params or {})
                else:
                    resp = client.post(upstream, params=params or {},
                                       json=body)
        except httpx.RequestError as e:
            return f"ERROR: upstream request failed: {e}"

        text = resp.text[: self.max_resp]
        return json.dumps(
            {"status": resp.status_code, "body": text},
            ensure_ascii=False,
        )
