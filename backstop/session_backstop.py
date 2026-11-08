#!/usr/bin/env python3
"""session_backstop.py: timer backstop, wakes an idle Claude Code tmux session that still has work in flight.

Purpose: every 30 s (systemd user timer) check whether the session is quiet for more than BACKSTOP_IDLE_SECS
  while the state file lists "In flight" items; if so type a resume line + Enter into the tmux session.
  Hooks can miss a stall; a timer outside the session is the last line of defence.
  Activity = `claude agents --json` (any session with cwd under BACKSTOP_DIR and status busy) AND the mtime of
  the newest file anywhere under the session's transcript dir (recursive, incl. subagents): busy if either is
  active. Also requires an idle pane (no "esc to interrupt") and no background shell jobs. Max one wake per
  BACKSTOP_MIN_GAP seconds (stamp file).
Config (all optional): BACKSTOP_DIR (default $PWD), BACKSTOP_TMUX (default claude-main), BACKSTOP_STATE_FILE
  (default $BACKSTOP_DIR/STATE.md), BACKSTOP_MARKER (default "In flight"), BACKSTOP_LINE (resume line),
  BACKSTOP_PROJ_DIR (transcript dir; default ~/.claude/projects/<BACKSTOP_DIR with / replaced by ->),
  BACKSTOP_LOG (TSV: utc, host, symptom, fix, by; default ~/.local/state/diplomat/heal.tsv),
  BACKSTOP_IDLE_SECS (300), BACKSTOP_MIN_GAP (600), BACKSTOP_STAMP, BACKSTOP_NO_AGENTS=1 (skip the agents check).
Never raises; exit 0 always.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time

ROOT = os.path.realpath(os.environ.get("BACKSTOP_DIR") or os.getcwd())
SESSION = os.environ.get("BACKSTOP_TMUX", "claude-main")
STATE_FILE = os.environ.get("BACKSTOP_STATE_FILE") or os.path.join(ROOT, "STATE.md")
MARKER = os.environ.get("BACKSTOP_MARKER", "In flight")
PROJ = os.environ.get("BACKSTOP_PROJ_DIR") or os.path.expanduser("~/.claude/projects/" + ROOT.replace("/", "-"))
IDLE = float(os.environ.get("BACKSTOP_IDLE_SECS", 300))
GAP = float(os.environ.get("BACKSTOP_MIN_GAP", 600))
STAMP = os.environ.get("BACKSTOP_STAMP", os.path.expanduser("~/.claude/backstop.last")) + "." + SESSION
HEAL = os.environ.get("BACKSTOP_LOG") or os.path.expanduser("~/.local/state/diplomat/heal.tsv")
LINE = os.environ.get("BACKSTOP_LINE", "Resume: check STATE.md open goals")


def fire(hook, action):
    """One audit line in ~/.claude/hook-fires.log (HOOK_FIRES_LOG overrides)."""
    try:
        log = os.environ.get("HOOK_FIRES_LOG") or os.path.expanduser("~/.claude/hook-fires.log")
        with open(log, "a") as f:
            f.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), hook,
                                       re.sub(r"\s+", " ", str(action))[:300]))
    except Exception:
        pass


def in_flight():
    try:
        for l in open(STATE_FILE):
            m = re.search(re.escape(MARKER) + r"[^:\n]*[:)]?\s*(.*)", l, re.I)
            if m and re.search(r"\w", m.group(1)):
                return True
    except OSError:
        pass
    return False


def agents_busy():
    """True busy, False not busy, None unknown."""
    try:
        r = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=20)
        for a in json.loads(r.stdout):
            if str(a.get("cwd", "")).startswith(ROOT) and a.get("status") == "busy":
                return True
        return False
    except Exception:
        return None


def transcript_age():
    try:
        ts = [os.path.getmtime(os.path.join(dp, f)) for dp, _, fs in os.walk(PROJ)
              for f in fs]
        return time.time() - max(ts)
    except Exception:
        return None


def shells_busy():
    """True when the claude process in the pane has child processes (background shell jobs the session waits on)."""
    r = subprocess.run(["tmux", "list-panes", "-t", SESSION, "-F", "#{pane_pid}"], capture_output=True, text=True)
    for pid in r.stdout.split():
        kids = subprocess.run(["pgrep", "-P", pid], capture_output=True, text=True).stdout.split()
        # the claude pane process itself, or a shell whose child is claude
        for k in kids + [pid]:
            if subprocess.run(["pgrep", "-P", k, "-f", "bash -c"], capture_output=True).returncode == 0:
                return True
    return False


def main():
    if subprocess.run(["tmux", "has-session", "-t", "=" + SESSION], capture_output=True).returncode != 0:
        return
    if shells_busy():
        return
    if not in_flight():
        return
    age = transcript_age()
    if (not os.environ.get("BACKSTOP_NO_AGENTS") and agents_busy() is True) or age is None or age < IDLE:
        return
    pane = subprocess.run(["tmux", "capture-pane", "-p", "-t", SESSION], capture_output=True, text=True).stdout
    if "esc to interrupt" in "\n".join(pane.splitlines()[-8:]):
        return
    try:
        if time.time() - os.path.getmtime(STAMP) < GAP:
            return
    except OSError:
        pass
    open(STAMP, "w").close()
    ok = subprocess.run(["tmux", "send-keys", "-t", SESSION, LINE, "Enter"], capture_output=True).returncode == 0
    os.makedirs(os.path.dirname(HEAL), exist_ok=True)
    with open(HEAL, "a") as f:
        f.write("%s\t%s\t%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), socket.gethostname(),
                "session idle %ds with %s items in %s" % (age, MARKER, os.path.basename(STATE_FILE)),
                ("typed wake line into tmux %s" % SESSION) if ok else "tmux send-keys failed", "backstop"))
    fire("backstop", "%s idle=%ds tmux=%s" % ("wake" if ok else "wake-failed", age, SESSION))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
