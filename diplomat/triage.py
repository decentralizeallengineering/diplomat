"""diplomat triage: one call that prints temperatures, top cgroups by CPU and RAM, and queued jobs.

CPU and RAM come from cgroup v2 files, sampled over a short interval, leaf cgroups only so nothing is counted
twice. Temperatures come from DIPLOMAT_TEMPS_FILE (its last lines) or DIPLOMAT_TEMPS_CMD (default `sensors`).
Queued jobs come from DIPLOMAT_JOBS_CMD (default `jobq ls`).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from . import common

MAX_DEPTH = 6


def leaves(root: Path) -> list[Path]:
    """Leaf cgroup directories (no child cgroup) that expose cpu.stat and memory.current."""
    out = []

    def walk(d: Path, depth: int):
        try:
            subs = sorted(x for x in d.iterdir() if x.is_dir() and not x.is_symlink())
        except OSError:
            return
        if subs and depth < MAX_DEPTH:
            for c in subs:
                walk(c, depth + 1)
        elif d != root and (d / "cpu.stat").exists():
            out.append(d)

    walk(root, 0)
    return out


def _int(path: Path, key: str | None = None) -> int:
    try:
        text = path.read_text()
        if key is None:
            return int(text.split()[0])
        m = re.search(rf"^{key} (\d+)", text, re.MULTILINE)
        return int(m.group(1)) if m else 0
    except (OSError, ValueError, IndexError):
        return 0


def cpu_usec(dirs) -> dict:
    return {d: _int(d / "cpu.stat", "usage_usec") for d in dirs}


def mem_bytes(dirs) -> dict:
    return {d: _int(d / "memory.current") for d in dirs}


def rank_cpu(before: dict, after: dict, dt: float) -> list[tuple[float, Path]]:
    """CPU percent of one core per cgroup over dt seconds, highest first."""
    if dt <= 0:
        return sorted(((0.0, d) for d in after), key=lambda t: str(t[1]))
    rows = [((after[d] - before.get(d, after[d])) / (dt * 1e6) * 100.0, d) for d in after]
    return sorted(rows, key=lambda t: (-t[0], str(t[1])))


def rank_mem(mem: dict) -> list[tuple[int, Path]]:
    return sorted(((v, d) for d, v in mem.items()), key=lambda t: (-t[0], str(t[1])))


def temps_text(limit: int = 20) -> str:
    f = os.environ.get("DIPLOMAT_TEMPS_FILE")
    if f:
        try:
            return "".join(Path(f).read_text().splitlines(True)[-limit:]).rstrip("\n")
        except OSError:
            pass
    out = common.run(os.environ.get("DIPLOMAT_TEMPS_CMD") or "sensors")
    if out is None:
        return "unavailable"
    keep = [l for l in out.splitlines() if re.search(r"\d\s*(°C|C\b)", l)] or out.splitlines()
    return "\n".join(keep[:limit]) or "unavailable"


def jobs_text(limit: int = 20) -> str:
    out = common.run(os.environ.get("DIPLOMAT_JOBS_CMD") or "jobq ls")
    if out is None:
        return "unavailable"
    return "\n".join(out.splitlines()[:limit]) or "none"


def triage(sample: float = 1.0, top: int = 5, root: Path | None = None) -> str:
    root = root or common.cgroup_root()
    dirs = leaves(root)
    before = cpu_usec(dirs)
    t0 = time.monotonic()
    if sample > 0:
        time.sleep(sample)
    after = cpu_usec(dirs)
    dt = time.monotonic() - t0 if sample > 0 else 0.0
    rel = lambda d: str(d.relative_to(root))
    lines = ["== temps", *("  " + l for l in temps_text().splitlines())]
    lines.append("== top cgroups by CPU (percent of one core)")
    lines += [f"  {p:6.1f}%  {rel(d)}" for p, d in rank_cpu(before, after, dt)[:top]]
    lines.append("== top cgroups by RAM")
    lines += [f"  {v / 2**20:8.1f} MiB  {rel(d)}" for v, d in rank_mem(mem_bytes(dirs))[:top]]
    lines += ["== queued jobs", *("  " + l for l in jobs_text().splitlines())]
    return "\n".join(lines)
