import os
import subprocess

from . import codex_status_tui
from .types import RateLimits


CODEX_DIR = os.path.expanduser("~/.codex")


def load_rate_limits() -> RateLimits | None:
    try:
        return codex_status_tui.load_rate_limits_from_host_status()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
