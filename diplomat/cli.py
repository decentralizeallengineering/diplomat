"""diplomat command line: watch, inbox, triage, relieve, resume, cpu-efficiency, template."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import cpu, relieve, triage, watch


def template_path() -> Path:
    return Path(__file__).resolve().parent / "CLAUDE.template.md"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="diplomat", description="Tools for one idle Claude Code session per machine.")
    sub = p.add_subparsers(dest="name", required=True)
    w = sub.add_parser("watch", help="tail -F the alarm log; print and spool ESCALATE lines")
    w.add_argument("--alarm-file", default=None, help="alarm log (default $ALARM_FILE)")
    w.add_argument("--inbox", default=None, help="spool file (default $DIPLOMAT_INBOX)")
    w.add_argument("--pattern", default="ESCALATE")
    w.add_argument("--replay", action="store_true", help="also match lines already in the file")
    w.add_argument("--max", type=int, default=0, help="exit after this many matches (0: run forever)")
    sub.add_parser("inbox", help="print and clear the spool file")
    t = sub.add_parser("triage", help="temps, top cgroups by CPU and RAM, queued jobs")
    t.add_argument("--sample", type=float, default=1.0, help="seconds to sample CPU (0: skip)")
    t.add_argument("--top", type=int, default=5)
    r = sub.add_parser("relieve", help="pause the newest non-critical heavy cgroup")
    r.add_argument("--mode", choices=["auto", "freeze", "stop"], default="auto")
    r.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("resume", help="undo the latest pause")
    s.add_argument("--all", action="store_true")
    c = sub.add_parser("cpu-efficiency", help="apply|revert|status CPU caps")
    c.add_argument("action", choices=["apply", "revert", "status"])
    sub.add_parser("template", help="print the CLAUDE.md template for the session")
    a = p.parse_args(argv)
    if a.name == "watch":
        n = watch.watch(Path(a.alarm_file) if a.alarm_file else watch.alarm_file(),
                        Path(a.inbox) if a.inbox else watch.inbox_file(), a.pattern, a.replay, a.max,
                        out=lambda l: print(l, flush=True))
        return 0 if n or not a.max else 1
    if a.name == "inbox":
        print(watch.read_inbox(watch.inbox_file()), end="")
        return 0
    if a.name == "triage":
        print(triage.triage(a.sample, a.top))
        return 0
    if a.name == "relieve":
        msg = relieve.relieve(a.mode, a.dry_run)
        print(msg or "no candidate: nothing non-critical is running in the heavy slice")
        return 0 if msg else 1
    if a.name == "resume":
        done = relieve.resume(a.all)
        print("\n".join(done) or "nothing to resume")
        return 0
    if a.name == "cpu-efficiency":
        return cpu.main([a.action])
    if a.name == "template":
        print(template_path().read_text(), end="")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
