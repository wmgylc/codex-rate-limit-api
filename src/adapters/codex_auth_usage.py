import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .types import RateLimits


AUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def load_rate_limits_from_host_auth() -> RateLimits | None:
    auth = fetch_host_auth()
    if _auth_needs_refresh(auth):
        auth = refresh_auth(auth)
        write_host_auth(auth)
    return fetch_usage_rate_limits(auth)


def measure_usage_request_latency() -> dict[str, Any]:
    started = time.perf_counter()
    auth_started = time.perf_counter()
    auth = fetch_host_auth()
    auth_ms = _elapsed_ms(auth_started)
    refreshed = False
    refresh_ms = None

    if _auth_needs_refresh(auth):
        refresh_started = time.perf_counter()
        auth = refresh_auth(auth)
        write_host_auth(auth)
        refresh_ms = _elapsed_ms(refresh_started)
        refreshed = True

    usage_started = time.perf_counter()
    usage = fetch_usage_response(auth)
    usage_ms = _elapsed_ms(usage_started)
    rate_limits = parse_usage_response(usage)

    return {
        "ok": rate_limits is not None,
        "source": "chatgpt_wham_usage",
        "url": os.environ.get("CODEX_USAGE_URL", USAGE_URL),
        "auth_read_ms": auth_ms,
        "auth_refreshed": refreshed,
        "auth_refresh_ms": refresh_ms,
        "usage_request_ms": usage_ms,
        "total_ms": _elapsed_ms(started),
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "5h": {
            "used_percent": rate_limits.five_hour_pct if rate_limits else None,
            "reset_at": rate_limits.five_hour_resets_at if rate_limits else None,
        },
        "wk": {
            "used_percent": rate_limits.seven_day_pct if rate_limits else None,
            "reset_at": rate_limits.seven_day_resets_at if rate_limits else None,
        },
    }


def measure_usage_request_latency_ms(max_seconds: float = 3.0) -> int:
    auth = fetch_host_auth()
    usage_started = time.perf_counter()
    fetch_usage_response(auth, timeout=max_seconds)
    elapsed = _elapsed_ms(usage_started)
    return elapsed if elapsed < 999 else 999


def fetch_host_auth() -> dict[str, Any]:
    output = _ssh_read_auth_json()
    data = json.loads(output)
    if not isinstance(data, dict):
        raise RuntimeError("Codex auth.json is not an object")
    return data


def fetch_usage_rate_limits(auth: dict[str, Any]) -> RateLimits | None:
    usage = fetch_usage_response(auth)
    return parse_usage_response(usage)


def fetch_usage_response(auth: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    tokens = auth.get("tokens") or {}
    access_token = tokens.get("access_token")
    if not access_token:
        raise RuntimeError("Codex auth.json does not contain an access token")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "codex-rate-limit-api/0.1",
    }
    account_id = tokens.get("account_id")
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id

    request = urllib.request.Request(os.environ.get("CODEX_USAGE_URL", USAGE_URL), headers=headers)
    timeout_seconds = timeout if timeout is not None else float(os.environ.get("CODEX_USAGE_TIMEOUT", "30"))
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Codex usage request failed with HTTP {exc.code}") from exc

    usage = json.loads(body)
    if not isinstance(usage, dict):
        raise RuntimeError("Codex usage response is not an object")
    return usage


def parse_usage_response(usage: dict[str, Any]) -> RateLimits | None:
    rate_limit = usage.get("rate_limit") or {}
    primary = rate_limit.get("primary_window") or {}
    secondary = rate_limit.get("secondary_window") or {}
    if not primary and not secondary:
        return None

    return RateLimits(
        five_hour_pct=_number(primary.get("used_percent")),
        five_hour_resets_at=_integer(primary.get("reset_at")),
        seven_day_pct=_number(secondary.get("used_percent")),
        seven_day_resets_at=_integer(secondary.get("reset_at")),
        updated_at=datetime.now(timezone.utc).isoformat(),
        source="chatgpt_wham_usage",
    )


def refresh_auth(auth: dict[str, Any]) -> dict[str, Any]:
    tokens = dict(auth.get("tokens") or {})
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Codex auth.json does not contain a refresh token")

    form = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": os.environ.get("CODEX_OAUTH_CLIENT_ID", DEFAULT_CLIENT_ID),
    }).encode("utf-8")
    request = urllib.request.Request(
        os.environ.get("CODEX_OAUTH_TOKEN_URL", AUTH_TOKEN_URL),
        data=form,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "codex-rate-limit-api/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.environ.get("CODEX_AUTH_REFRESH_TIMEOUT", "30"))) as response:
            refreshed = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Codex auth refresh failed with HTTP {exc.code}") from exc

    if not isinstance(refreshed, dict) or not refreshed.get("access_token"):
        raise RuntimeError("Codex auth refresh response did not include an access token")

    for key in ("access_token", "refresh_token", "id_token", "account_id"):
        if refreshed.get(key):
            tokens[key] = refreshed[key]

    updated = dict(auth)
    updated["tokens"] = tokens
    updated["last_refresh"] = datetime.now(timezone.utc).isoformat()
    return updated


def write_host_auth(auth: dict[str, Any]) -> None:
    payload = json.dumps(auth, ensure_ascii=False, separators=(",", ":"))
    code = (
        "import os,pathlib,sys,tempfile;"
        "home=os.environ.get('CODEX_HOME');"
        "path=(pathlib.Path(home)/'auth.json') if home else (pathlib.Path.home()/'.codex'/'auth.json');"
        "path.parent.mkdir(parents=True,exist_ok=True);"
        "data=sys.stdin.read();"
        "fd,tmp=tempfile.mkstemp(dir=str(path.parent),prefix='.auth.',text=True);"
        "f=os.fdopen(fd,'w');f.write(data);f.write('\\n');f.close();"
        "os.chmod(tmp,0o600);os.replace(tmp,path)"
    )
    _ssh_run(["python3", "-c", code], input_text=payload)


def _auth_needs_refresh(auth: dict[str, Any]) -> bool:
    last_refresh = _parse_datetime(auth.get("last_refresh"))
    if not last_refresh:
        return True
    max_age_days = float(os.environ.get("CODEX_AUTH_REFRESH_MAX_AGE_DAYS", "8"))
    return datetime.now(timezone.utc) - last_refresh > timedelta(days=max_age_days)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ssh_read_auth_json() -> str:
    script = (
        "if [ -n \"$CODEX_HOME\" ] && [ -f \"$CODEX_HOME/auth.json\" ]; then "
        "cat \"$CODEX_HOME/auth.json\"; "
        "elif [ -f \"$HOME/.codex/auth.json\" ]; then "
        "cat \"$HOME/.codex/auth.json\"; "
        "else exit 2; fi"
    )
    return _ssh_run(["sh", "-lc", script])


def _ssh_run(argv: list[str], input_text: str | None = None) -> str:
    host = os.environ.get("CODEX_HOST_SSH_HOST", "host.docker.internal")
    user = os.environ.get("CODEX_HOST_SSH_USER", "mac")
    password = os.environ.get("CODEX_HOST_SSH_PASSWORD")
    if not password:
        raise RuntimeError("CODEX_HOST_SSH_PASSWORD is required for Codex host auth")

    remote_cmd = " ".join(_shell_quote(part) for part in argv)
    cmd = [
        "sshpass",
        "-e",
        "ssh",
        "-T",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        f"ConnectTimeout={os.environ.get('CODEX_HOST_SSH_CONNECT_TIMEOUT', '5')}",
        f"{user}@{host}",
        remote_cmd,
    ]
    env = os.environ.copy()
    env["SSHPASS"] = password
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=float(os.environ.get("CODEX_HOST_SSH_TIMEOUT", "20")),
        env=env,
        check=True,
    )
    return result.stdout


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _integer(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
