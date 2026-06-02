import os
import subprocess

from . import codex_auth_usage, codex_status_tui
from .types import RateLimits


CODEX_DIR = os.path.expanduser("~/.codex")


def load_rate_limits() -> RateLimits | None:
    try:
        rate_limits = codex_auth_usage.load_rate_limits_from_host_auth()
        if rate_limits is not None:
            return rate_limits
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        pass

    try:
        return codex_status_tui.load_rate_limits_from_host_status()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
