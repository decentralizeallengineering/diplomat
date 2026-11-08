#!/bin/bash
# session-keepalive.sh - keeps one Claude Code session running in a tmux session.
# Run every 60 s from a systemd user timer (see session-keepalive.timer). Silent when healthy; logs each action
# as a TSV line (utc, host, symptom, fix, by).
# Config (all optional):
#   KEEPALIVE_SESSION  tmux session name (default claude-main)
#   KEEPALIVE_DIR      working directory of the session (default $PWD)
#   KEEPALIVE_LOG      action log (default ${XDG_STATE_HOME:-~/.local/state}/diplomat/heal.tsv)
#   KEEPALIVE_FLAGS    extra flags for claude (default empty)
#   KEEPALIVE_PROMPT   line typed after a restart (default: resume from the state file)
#   KEEPALIVE_CMD      full launch command, used for both attempts (drills and tests)
set -u
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

SESSION="${KEEPALIVE_SESSION:-claude-main}"
DIR="${KEEPALIVE_DIR:-$PWD}"
LOG="${KEEPALIVE_LOG:-${XDG_STATE_HOME:-$HOME/.local/state}/diplomat/heal.tsv}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
HOST=$(hostname -s)
PROMPT="${KEEPALIVE_PROMPT:-Resume: read STATE.md and continue the open goals.}"
LOCK="${XDG_RUNTIME_DIR:-/tmp}/session-keepalive-$SESSION.lock"
FLAGS="${KEEPALIVE_FLAGS:-}"

if [ -n "${KEEPALIVE_CMD:-}" ]; then
  CMD_CONT="$KEEPALIVE_CMD"
  CMD_FRESH="$KEEPALIVE_CMD"
else
  CMD_CONT="cd '$DIR' && claude --continue $FLAGS"
  CMD_FRESH="cd '$DIR' && claude $FLAGS"
fi

log() { # symptom fix
  printf '%s\t%s\t%s\t%s\tby=keepalive\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$HOST" "$1" "$2" >> "$LOG" 2>/dev/null || true
}

# True if the session has a live pane running claude (pane process itself, or a direct child).
claude_in_session() {
  tmux has-session -t "$SESSION" 2>/dev/null || return 1
  local pid dead
  while read -r pid dead; do
    [ -n "$pid" ] || continue
    [ "$dead" = 1 ] && continue
    [ "$(ps -o comm= -p "$pid" 2>/dev/null | tr -d ' ')" = claude ] && return 0
    pgrep -P "$pid" -x claude >/dev/null 2>&1 && return 0
  done < <(tmux list-panes -s -t "$SESSION" -F '#{pane_pid} #{pane_dead}' 2>/dev/null)
  return 1
}

launch() { # command
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux respawn-pane -k -t "$SESSION" "$1"   # session exists but no claude in it
  else
    tmux new -d -s "$SESSION" "$1"
  fi
}

# Wait up to 30 s; returns 1 if the session/pane exits (launch failed). tmux drops a session when its pane exits.
survives_30s() {
  local i
  for i in $(seq 1 15); do
    sleep 2
    tmux has-session -t "$SESSION" 2>/dev/null || return 1
  done
  return 0
}

# 1. Never act in the first 120 s after boot.
up=$(awk '{print int($1)}' /proc/uptime)
[ "$up" -lt 120 ] && exit 0

# 2. Single run at a time (wait up to 2 min for an in-flight run, then re-check health under the lock).
exec 9>"$LOCK" || exit 0
flock -w 120 9 || exit 0

# 3. Healthy -> do nothing, no log.
claude_in_session && exit 0

if tmux has-session -t "$SESSION" 2>/dev/null; then
  SYMPTOM=no_claude_in_session
else
  SYMPTOM=session_missing
fi

# 4. Restart: try --continue first, fall back to a fresh launch if the pane exits within 30 s.
launch "$CMD_CONT"
if survives_30s; then
  FIX=started_continue
else
  launch "$CMD_FRESH"
  if survives_30s; then
    FIX=continue_failed_started_fresh
  else
    log "$SYMPTOM" "restart_failed_pane_exited"
    exit 1
  fi
fi

sleep 20
# Only type the prompt into a pane that really runs claude (never into a bare shell).
if claude_in_session; then
  tmux send-keys -t "$SESSION" -l "$PROMPT"
  tmux send-keys -t "$SESSION" Enter
  log "$SYMPTOM" "$FIX;prompt_sent"
else
  log "$SYMPTOM" "$FIX;prompt_skipped_no_claude_in_pane"
fi
exit 0
