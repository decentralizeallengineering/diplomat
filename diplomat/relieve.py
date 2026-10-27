"""diplomat relieve / resume: pause the cheapest offender, then undo it.

The cheapest offender is the newest non-critical cgroup under the heavy slice that has processes: the work
started last has lost the least. Pausing prefers the cgroup v2 freezer (cgroup.freeze); if that file is not
writable, every process in the cgroup gets SIGSTOP. Every pause is recorded so `resume` can undo it exactly.
Never kills, never reboots. "Newest" is the directory modification time, which cgroupfs sets at creation.
"""
from __future__ import annotations

import os
import re
import signal
import time
from pathlib import Path

from . import common

DEFAULT_CRITICAL = r"sshd|^ssh|libvirt|zfs|nas|systemd-|dbus|claude|diplomat"


def state_file() -> Path:
    return common.state_dir() / "relieved.json"


def heavy_dir(root: Path) -> Path | None:
    env = os.environ.get("DIPLOMAT_HEAVY_DIR")
    if env:
        return Path(env)
    name = os.environ.get("DIPLOMAT_HEAVY_SLICE", "heavy.slice")
    for dirpath, dirnames, _ in os.walk(root):
        if name in dirnames:
            return Path(dirpath) / name
        if len(Path(dirpath).relative_to(root).parts) >= 4:
            dirnames[:] = []
    return None


def pids_of(d: Path) -> list[int]:
    try:
        return [int(x) for x in (d / "cgroup.procs").read_text().split()]
    except (OSError, ValueError):
        return []


def candidates(heavy: Path, critical: str, skip: set) -> list[Path]:
    """Cgroups at any depth under the heavy slice that hold processes, newest first, minus critical ones.
    A path is critical when any component below the heavy slice matches the pattern."""
    rx = re.compile(critical)
    out = []
    for dirpath, dirnames, _ in os.walk(heavy):
        d = Path(dirpath)
        if d == heavy:
            continue
        if any(rx.search(part) for part in d.relative_to(heavy).parts):
            dirnames[:] = []
            continue
        if str(d) not in skip and pids_of(d):
            out.append(d)
    return sorted(out, key=lambda d: (-d.stat().st_mtime, str(d)))


def relieve(mode: str = "auto", dry_run: bool = False, root: Path | None = None) -> str | None:
    """Pause the cheapest offender and return a description, or None when there is no candidate."""
    root = root or common.cgroup_root()
    heavy = heavy_dir(root)
    if heavy is None or not heavy.is_dir():
        return None
    state = common.load_json(state_file(), [])
    cands = candidates(heavy, os.environ.get("DIPLOMAT_CRITICAL") or DEFAULT_CRITICAL, {e["path"] for e in state})
    if not cands:
        return None
    d = cands[0]
    pids = [p for p in pids_of(d) if p > 1 and p != os.getpid()]
    if dry_run:
        return f"WOULD PAUSE {d.name} pids={len(pids)}"
    used = None
    if mode in ("auto", "freeze"):
        try:
            (d / "cgroup.freeze").write_text("1")
            used = "freeze"
        except OSError:
            if mode == "freeze":
                raise
    if used is None:
        for p in pids:
            try:
                os.kill(p, signal.SIGSTOP)
            except ProcessLookupError:
                pass
        used = "stop"
    state.append({"path": str(d), "mode": used, "pids": pids, "at": int(time.time())})
    common.save_json(state_file(), state)
    return f"PAUSED {d.name} mode={used} pids={len(pids)} (undo: diplomat resume)"


def resume(all_: bool = False) -> list[str]:
    """Undo the latest pause (or every pause). Returns one description per resumed cgroup."""
    state = common.load_json(state_file(), [])
    done = []
    while state:
        e = state.pop()
        if e["mode"] == "freeze":
            try:
                (Path(e["path"]) / "cgroup.freeze").write_text("0")
            except OSError:
                pass
        else:
            for p in e["pids"]:
                try:
                    os.kill(p, signal.SIGCONT)
                except ProcessLookupError:
                    pass
        done.append("RESUMED {} mode={}".format(Path(e["path"]).name, e["mode"]))
        if not all_:
            break
    common.save_json(state_file(), state)
    return done
