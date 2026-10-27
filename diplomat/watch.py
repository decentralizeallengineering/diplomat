"""diplomat watch: follow an alarm log and wake the session when a line matches.

One long-running process is the session's single event monitor. Each matching line is printed on stdout
(a Claude Code Monitor turns every stdout line into a wake-up) and appended to an inbox file, so the line
survives if the session was busy. `diplomat inbox` prints and clears the file.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from . import common


def alarm_file() -> Path:
    return Path(os.environ.get("ALARM_FILE") or common.state_dir() / "alarms.log")


def inbox_file() -> Path:
    return Path(os.environ.get("DIPLOMAT_INBOX") or common.state_dir() / "inbox")


def watch(alarm: Path, inbox: Path, pattern: str, replay: bool = False, max_hits: int = 0, out=print) -> int:
    rx = re.compile(pattern)
    inbox.parent.mkdir(parents=True, exist_ok=True)
    start = "+1" if replay else "0"
    p = subprocess.Popen(["tail", "-n", start, "-F", str(alarm)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    hits = 0
    try:
        for line in p.stdout:
            line = line.rstrip("\n")
            if not rx.search(line):
                continue
            with open(inbox, "a") as f:
                f.write("{} {}\n".format(time.strftime("%Y-%m-%dT%H:%M:%S%z"), line))
            out(line)
            hits += 1
            if max_hits and hits >= max_hits:
                break
    finally:
        p.terminate()
        p.wait()
    return hits


def read_inbox(inbox: Path, clear: bool = True) -> str:
    try:
        text = inbox.read_text()
    except OSError:
        return ""
    if clear and text:
        inbox.write_text("")
    return text
