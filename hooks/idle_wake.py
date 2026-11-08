#!/usr/bin/env python3
"""idle_wake.py: StopFailure and Notification hook, wakes a Claude Code tmux session shortly after it stalls.

Purpose: events StopFailure (a turn ended on an API error) and Notification with notification_type idle_prompt,
  permission_prompt or elicitation_dialog. Only when cwd is under WAKE_DIR (unset: any cwd) and tmux session
  WAKE_TMUX (default claude-main) exists. Spawns a detached background process (setsid, never blocks the hook)
  that sleeps WAKE_DELAY seconds (default 30), checks the pane is idle (no "esc to interrupt" in the last
  lines), then types WAKE_LINE + Enter. idle_prompt wakes only while WAKE_STATE_FILE still contains
  WAKE_MARKER (default "In flight"). Lock file: one wake per 60 s.
Fire log: $HOOK_FIRES_LOG (default ~/.claude/hook-fires.log): utc, wake, schedule|sent|skip: event detail.
Fails open, exit 0 always. Pair it with backstop/session_backstop.py, which covers stalls no hook sees.
"""
import os
import subprocess
import sys
import time

import json
import re

ROOT = os.path.realpath(os.environ["WAKE_DIR"]) if os.environ.get("WAKE_DIR") else ""
SESSION = os.environ.get("WAKE_TMUX", "claude-main")
DELAY = os.environ.get("WAKE_DELAY", "30")
LINE = os.environ.get("WAKE_LINE", "Resume: check STATE.md open goals")
MARKER = os.environ.get("WAKE_MARKER", "In flight")
STATE_FILE = os.environ.get("WAKE_STATE_FILE") or os.path.join(ROOT or os.getcwd(), "STATE.md")


def fire(hook, action):
    try:
        log = os.environ.get("HOOK_FIRES_LOG") or os.path.expanduser("~/.claude/hook-fires.log")
        with open(log, "a") as f:
            f.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), hook,
                                       re.sub(r"\s+", " ", str(action))[:300]))
    except Exception:
        pass


def read_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}
NOTIFY = {"idle_prompt", "permission_prompt", "elicitation_dialog"}
LOCK = os.path.expanduser("~/.claude/wake.lock") + "." + SESSION

WORKER = r'''
sleep "$1"
pane=$(tmux capture-pane -p -t "$2" 2>/dev/null | tail -8)
if printf '%s' "$pane" | grep -q "esc to interrupt"; then
  printf '%s\t%s\t%s\n' "$(date -u +%FT%TZ)" wake "skip: pane busy ($3)" >> "$4"; exit 0
fi
tmux send-keys -t "$2" "$5" Enter && printf '%s\t%s\t%s\n' "$(date -u +%FT%TZ)" wake "sent: $3 -> $2" >> "$4"
'''


def main():
    d = read_input()
    ev = d.get("hook_event_name")
    if ev not in ("StopFailure", "Notification"):
        return
    if ev == "Notification" and d.get("notification_type") not in NOTIFY:
        return
    cwd = os.path.realpath(d.get("cwd") or os.getcwd())
    if ROOT and not (cwd == ROOT or cwd.startswith(ROOT + "/")):
        return
    if ev == "Notification" and d.get("notification_type") == "idle_prompt":
        try:
            if MARKER not in open(STATE_FILE).read():
                return
        except OSError:
            return
    if subprocess.run(["tmux", "has-session", "-t", "=" + SESSION], capture_output=True).returncode != 0:
        return
    try:
        if time.time() - os.path.getmtime(LOCK) < 60:
            fire("wake", "skip: locked (%s)" % ev)
            return
    except OSError:
        pass
    open(LOCK, "w").close()
    detail = ev + ":" + str(d.get("notification_type") if ev == "Notification" else d.get("error"))
    fire("wake", "schedule: %s in %ss -> %s" % (detail, DELAY, SESSION))
    log = os.environ.get("HOOK_FIRES_LOG") or os.path.expanduser("~/.claude/hook-fires.log")
    subprocess.Popen(["setsid", "-f", "bash", "-c", WORKER, "wake", DELAY, SESSION, detail, log, LINE],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
