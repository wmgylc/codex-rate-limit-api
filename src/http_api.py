import argparse
import json
import os
import queue
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping
from urllib.parse import parse_qs, urlsplit

from .adapters import codex
from .adapters import codex_auth_usage
from .adapters.types import RateLimits


JsonValue = str | int | float | bool | None | dict[str, "JsonValue"] | list["JsonValue"]


class RateLimitCache:
    def __init__(self, loader: Callable[[], RateLimits | None], refresh_seconds: float) -> None:
        self._loader = loader
        self._refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._value: RateLimits | None = None

    def get(self) -> RateLimits | None:
        with self._lock:
            return self._value

    def refresh(self) -> RateLimits | None:
        if not self._refresh_lock.acquire(blocking=False):
            return self.get()
        try:
            value = self._loader()
            if value is not None:
                with self._lock:
                    self._value = value
            return self.get()
        finally:
            self._refresh_lock.release()

    def start(self, initial_refresh: bool = True) -> None:
        if initial_refresh:
            self.refresh()
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rate-limit-cache-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.wait(self._refresh_seconds):
            self.refresh()


def _refresh_seconds_from_env() -> float:
    value = float(os.environ.get("CODEX_RATE_LIMIT_REFRESH_SECONDS", "300"))
    return max(1.0, value)


_RATE_LIMIT_CACHE = RateLimitCache(codex.load_rate_limits, _refresh_seconds_from_env())


def _iso_from_epoch(epoch_seconds: int | None) -> str | None:
    if epoch_seconds is None:
        return None
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def _limit_payload(used_percent: float | None, resets_at: int | None) -> dict[str, JsonValue]:
    return {
        "used_percent": used_percent,
        "reset_at": resets_at,
        "reset_at_iso": _iso_from_epoch(resets_at),
    }


def build_codex_rate_limit_payload(
    load_rate_limits: Callable[[], RateLimits | None] = codex.load_rate_limits,
) -> dict[str, JsonValue]:
    rate_limits = load_rate_limits()
    if rate_limits is None:
        return {
            "agent": "codex",
            "available": False,
            "5h": _limit_payload(None, None),
            "wk": _limit_payload(None, None),
            "five_hour": _limit_payload(None, None),
            "weekly": _limit_payload(None, None),
            "model": "",
            "updated_at": "",
            "source_dir": os.path.expanduser("~/.codex"),
        }

    five_hour = _limit_payload(rate_limits.five_hour_pct, rate_limits.five_hour_resets_at)
    weekly = _limit_payload(rate_limits.seven_day_pct, rate_limits.seven_day_resets_at)
    return {
        "agent": "codex",
        "available": True,
        "5h": five_hour,
        "wk": weekly,
        "five_hour": five_hour,
        "weekly": weekly,
        "model": rate_limits.model,
        "updated_at": rate_limits.updated_at,
        "source_dir": os.path.expanduser("~/.codex"),
    }


def measure_usage_latency_ms(
    measure_latency: Callable[[float], int] = codex_auth_usage.measure_usage_request_latency_ms,
    timeout_seconds: float = 5.0,
) -> int:
    result: queue.Queue[int] = queue.Queue(maxsize=1)
    timeout_ms = round(timeout_seconds * 1000)

    def run() -> None:
        try:
            result.put_nowait(measure_latency(timeout_seconds))
        except Exception:
            result.put_nowait(timeout_ms)

    thread = threading.Thread(target=run, name="usage-latency-probe", daemon=True)
    thread.start()
    try:
        return result.get(timeout=timeout_seconds)
    except queue.Empty:
        return timeout_ms


def parse_latency_timeout(path: str) -> float:
    query = parse_qs(urlsplit(path).query)
    raw = query.get("timeout", ["5"])[0]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 5.0
    return max(0.1, min(value, 60.0))


def encode_json_response(payload: Mapping[str, JsonValue], status: int = 200) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return status, {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
    }, body


class CodexRateLimitHandler(BaseHTTPRequestHandler):
    server_version = "codex-rate-limit-api/0.1"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json({"ok": True})
            return
        if path in ("/api/codex/rate-limits", "/api/codex/usage"):
            self._send_json(build_codex_rate_limit_payload(_RATE_LIMIT_CACHE.get))
            return
        if path == "/api/codex/usage/latency":
            self._send_text(str(measure_usage_latency_ms(timeout_seconds=parse_latency_timeout(self.path))))
            return
        self._send_json({"error": "not_found"}, status=404)

    def log_message(self, fmt: str, *args: object) -> None:
        if os.environ.get("TT_HTTP_ACCESS_LOG") == "1":
            super().log_message(fmt, *args)

    def _send_json(self, payload: Mapping[str, JsonValue], status: int = 200) -> None:
        status, headers, body = encode_json_response(payload, status)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    initial_refresh = os.environ.get("CODEX_RATE_LIMIT_INITIAL_REFRESH", "1") != "0"
    _RATE_LIMIT_CACHE.start(initial_refresh=initial_refresh)
    httpd = ThreadingHTTPServer((host, port), CodexRateLimitHandler)
    print(f"Codex rate limit API listening on http://{host}:{port}", flush=True)
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Codex rate limit usage over HTTP")
    parser.add_argument("--host", default=os.environ.get("TT_HTTP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TT_HTTP_PORT", "8080")))
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
