"""cpu-efficiency apply|revert|status: cap the CPU governor, energy preference and boost, with a saved baseline.

apply  saves the current values once (a second apply keeps the first baseline), then writes the efficient ones.
revert writes the saved values back and deletes the baseline.
Needs root to write sysfs. Environment (defaults): CPUFREQ_DIR (/sys/devices/system/cpu/cpufreq),
CPU_EFFICIENCY_GOVERNOR (powersave), CPU_EFFICIENCY_EPP (power), CPU_EFFICIENCY_BOOST (0),
CPU_EFFICIENCY_MAX_KHZ (unset: leave scaling_max_freq alone), CPU_EFFICIENCY_BASELINE (<state dir>/cpu-baseline.json).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import common

FILES = ("scaling_governor", "energy_performance_preference", "scaling_max_freq")


def cpufreq_dir() -> Path:
    return Path(os.environ.get("CPUFREQ_DIR") or "/sys/devices/system/cpu/cpufreq")


def baseline_path() -> Path:
    return Path(os.environ.get("CPU_EFFICIENCY_BASELINE") or common.state_dir() / "cpu-baseline.json")


def policies(base: Path) -> list[Path]:
    return sorted(p for p in base.glob("policy*") if p.is_dir())


def read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def snapshot(base: Path) -> dict:
    snap = {"policies": {p.name: {f: read(p / f) for f in FILES if read(p / f) is not None} for p in policies(base)}}
    b = read(base / "boost")
    if b is not None:
        snap["boost"] = b
    return snap


def write_values(base: Path, values: dict) -> None:
    for name, files in values.get("policies", {}).items():
        for f in FILES:
            if f in files:
                (base / name / f).write_text(files[f])
    if "boost" in values:
        (base / "boost").write_text(values["boost"])


def target(base: Path) -> dict:
    gov = os.environ.get("CPU_EFFICIENCY_GOVERNOR", "powersave")
    epp = os.environ.get("CPU_EFFICIENCY_EPP", "power")
    cap = os.environ.get("CPU_EFFICIENCY_MAX_KHZ")
    pol = {}
    for p in policies(base):
        v = {}
        if (p / "scaling_governor").exists() and gov:
            v["scaling_governor"] = gov
        if (p / "energy_performance_preference").exists() and epp:
            v["energy_performance_preference"] = epp
        if cap and (p / "scaling_max_freq").exists():
            v["scaling_max_freq"] = cap
        pol[p.name] = v
    t = {"policies": pol}
    if (base / "boost").exists():
        t["boost"] = os.environ.get("CPU_EFFICIENCY_BOOST", "0")
    return t


def apply() -> int:
    base, bl = cpufreq_dir(), baseline_path()
    if not policies(base):
        print(f"cpu-efficiency: no cpufreq policies under {base}", file=sys.stderr)
        return 1
    if not bl.exists():
        common.save_json(bl, snapshot(base))
    write_values(base, target(base))
    print(f"applied; baseline in {bl}")
    return 0


def revert() -> int:
    base, bl = cpufreq_dir(), baseline_path()
    saved = common.load_json(bl, {})
    if not saved:
        print(f"cpu-efficiency: no baseline at {bl}; nothing to revert", file=sys.stderr)
        return 1
    # governor first, then the preference and the cap, so each write is valid under the restored governor
    write_values(base, saved)
    os.remove(bl)
    print("reverted to baseline")
    return 0


def status() -> int:
    snap = snapshot(cpufreq_dir())
    for name, files in snap["policies"].items():
        print(name, " ".join("{}={}".format(*kv) for kv in files.items()))
    if "boost" in snap:
        print("boost={}".format(snap["boost"]))
    print("baseline: %s" % ("saved" if baseline_path().exists() else "none"))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cpu-efficiency", description="Apply or revert CPU efficiency caps (needs root).")
    p.add_argument("action", choices=["apply", "revert", "status"])
    a = p.parse_args(argv)
    try:
        return {"apply": apply, "revert": revert, "status": status}[a.action]()
    except PermissionError as e:
        print(f"cpu-efficiency: {e} (run as root)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
