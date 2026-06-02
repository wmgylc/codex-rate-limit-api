import errno
import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .types import RateLimits


MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

STATUS_LINE_RE = re.compile(
    r"(?P<label>5h|Weekly) limit:\s*\[[^\]]*\]\s*"
    r"(?P<remaining>\d+(?:\.\d+)?)%\s+left\s+"
    r"\(resets\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\s+on\s+"
    r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3})\)",
    re.IGNORECASE,
)


def load_rate_limits_from_host_status() -> RateLimits | None:
    output = fetch_host_status_output()
    return parse_status_output(output)


def fetch_host_status_output() -> str:
    host = os.environ.get("CODEX_HOST_SSH_HOST", "host.docker.internal")
    user = os.environ.get("CODEX_HOST_SSH_USER", "mac")
    password = os.environ.get("CODEX_HOST_SSH_PASSWORD")
    codex_path = os.environ.get("CODEX_HOST_CODEX_PATH", "/opt/homebrew/bin/codex")
    cwd = os.environ.get("CODEX_HOST_CODEX_CWD", "/tmp")

    if not password:
        raise RuntimeError("CODEX_HOST_SSH_PASSWORD is required for Codex TUI status")

    remote_cmd = " ".join([
        "TERM=xterm-256color",
        _shell_quote(codex_path),
        "--no-alt-screen",
        "--disable apps",
        "--disable plugins",
        "--disable computer_use",
        "--disable browser_use",
        "--disable in_app_browser",
        "-C",
        _shell_quote(cwd),
    ])
    cmd = [
        "sshpass",
        "-e",
        "ssh",
        "-tt",
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
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"

    return _run_status_tui(cmd, env, timeout=float(os.environ.get("CODEX_STATUS_TIMEOUT", "40")))


def parse_status_output(output: str) -> RateLimits | None:
    text = _strip_ansi(output)
    matches = list(STATUS_LINE_RE.finditer(text))
    if not matches:
        return None

    timezone_name = os.environ.get("CODEX_HOST_TIMEZONE", "Asia/Shanghai")
    tz = ZoneInfo(timezone_name)

    five = _first_limit(matches, "5h", tz)
    weekly = _first_limit(matches, "Weekly", tz)
    if not five and not weekly:
        return None

    model = _extract_model(text)
    return RateLimits(
        five_hour_pct=_used_percent(five["remaining"]) if five else None,
        five_hour_remaining_pct=five["remaining"] if five else None,
        five_hour_resets_at=five["reset_at"] if five else None,
        seven_day_pct=_used_percent(weekly["remaining"]) if weekly else None,
        seven_day_remaining_pct=weekly["remaining"] if weekly else None,
        seven_day_resets_at=weekly["reset_at"] if weekly else None,
        model=model,
        updated_at=datetime.now().astimezone().isoformat(),
        source="codex_cli_status",
    )


def _run_status_tui(cmd: list[str], env: dict[str, str], timeout: float) -> str:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, close_fds=True, env=env)
    os.close(slave)

    output = b""
    phase = 0
    phase_at = time.time()
    started_at = time.time()
    try:
        while time.time() - started_at < timeout:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 32768)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output += chunk

            clean = _strip_ansi(output.decode("utf-8", "replace"))
            if phase == 0 and _tui_ready(clean, started_at):
                os.write(master, b"/status\r")
                phase = 1
                phase_at = time.time()
            elif phase == 1 and ("refresh requested" in clean or time.time() - phase_at > 8):
                time.sleep(2)
                os.write(master, b"/status\r")
                phase = 2
                phase_at = time.time()
            elif phase == 2 and _has_main_limits(clean):
                break
            elif phase == 2 and time.time() - phase_at > 12:
                break
    finally:
        if proc.poll() is None:
            try:
                os.write(master, b"\x03")
            except OSError:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.close(master)
        except OSError:
            pass

    return output.decode("utf-8", "replace")


def _tui_ready(clean: str, started_at: float) -> bool:
    if "Tip:" in clean and ("directory:" in clean or "Directory:" in clean):
        return True
    return time.time() - started_at > 6


def _has_main_limits(clean: str) -> bool:
    matches = list(STATUS_LINE_RE.finditer(clean))
    return any(match.group("label").lower() == "5h" for match in matches) and any(
        match.group("label").lower() == "weekly" for match in matches
    )


def _strip_ansi(value: bytes | str) -> str:
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[()][A-Za-z0-9]", "", text)
    text = text.replace("\x1b7", "").replace("\x1b8", "").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    return re.sub(r"\n+", "\n", text)


def _first_limit(matches: list[re.Match[str]], label: str, tz: ZoneInfo) -> dict[str, float | int] | None:
    for match in matches:
        if match.group("label").lower() != label.lower():
            continue
        remaining = float(match.group("remaining"))
        return {
            "remaining": remaining,
            "reset_at": _parse_reset_epoch(match, tz),
        }
    return None


def _parse_reset_epoch(match: re.Match[str], tz: ZoneInfo) -> int:
    now = datetime.now(tz)
    month = MONTHS[match.group("month").lower()]
    dt = datetime(
        now.year,
        month,
        int(match.group("day")),
        int(match.group("hour")),
        int(match.group("minute")),
        tzinfo=tz,
    )
    if dt < now.replace(second=0, microsecond=0):
        dt = dt.replace(year=dt.year + 1)
    return int(dt.timestamp())


def _used_percent(remaining_percent: float) -> float:
    return round(max(0.0, min(100.0, 100.0 - remaining_percent)), 1)


def _extract_model(text: str) -> str:
    match = re.search(r"Model:\s*([^\s(]+)", text)
    return match.group(1) if match else ""


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
