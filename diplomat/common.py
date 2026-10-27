"""Shared locations and a bounded command runner."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path


def state_dir() -> Path:
    env = os.environ.get("DIPLOMAT_STATE_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "diplomat"


def cgroup_root() -> Path:
    return Path(os.environ.get("DIPLOMAT_CGROUP_ROOT") or "/sys/fs/cgroup")


def run(cmd: str, timeout: float = 20.0) -> str | None:
    """Run a command line (no shell) and return stdout, or None if it is missing, failed or timed out."""
    try:
        r = subprocess.run(shlex.split(cmd), check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def load_json(path, default):
    try:
        with open(path) as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)
