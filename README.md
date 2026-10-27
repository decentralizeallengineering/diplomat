# diplomat

[![tests](https://github.com/decentralizeallengineering/diplomat/actions/workflows/tests.yml/badge.svg)](https://github.com/decentralizeallengineering/diplomat/actions/workflows/tests.yml) ![license](https://img.shields.io/badge/license-Apache--2.0-blue)

One idle Claude Code session per machine, plus the small tools it needs. Stdlib-only Python, Linux with cgroup v2.

## Why

A fleet of machines does not need a busy agent on each one. It needs a session that costs nothing while idle, wakes on one event (an alarm line), looks at the machine in one call, and can take the cheapest relief action without killing anything. diplomat is that kit: a CLAUDE.md template, an event watcher, a triage report, a pause and resume pair, and CPU efficiency caps with a saved baseline.

## Install

    pip install .          # or run in place: python3 -m diplomat <cmd>

Python 3.9 or newer, no dependencies. Installs `diplomat` and `cpu-efficiency`.

## Configure

All environment variables are optional.

| Variable | Default | Meaning |
|---|---|---|
| `DIPLOMAT_STATE_DIR` | `$XDG_STATE_HOME/diplomat` | inbox, pause records |
| `ALARM_FILE` | `<state>/alarms.log` | alarm log that `watch` follows |
| `DIPLOMAT_INBOX` | `<state>/inbox` | spool file for matched lines |
| `DIPLOMAT_TEMPS_FILE` / `DIPLOMAT_TEMPS_CMD` | `sensors` | temperature source for `triage` |
| `DIPLOMAT_JOBS_CMD` | `jobq ls` | queued-jobs source for `triage` |
| `DIPLOMAT_CGROUP_ROOT` | `/sys/fs/cgroup` | cgroup v2 mount |
| `DIPLOMAT_HEAVY_SLICE` / `DIPLOMAT_HEAVY_DIR` | `heavy.slice` | where heavy work runs; the dir overrides the search |
| `DIPLOMAT_CRITICAL` | `sshd\|^ssh\|libvirt\|zfs\|nas\|systemd-\|dbus\|claude\|diplomat` | regex of names `relieve` never pauses |
| `CPUFREQ_DIR`, `CPU_EFFICIENCY_GOVERNOR`, `_EPP`, `_BOOST`, `_MAX_KHZ`, `_BASELINE` | see `cpu-efficiency --help` source | CPU caps; defaults `powersave`, `power`, `0`, unset |

## Commands

- `diplomat template` prints the CLAUDE.md for the session: act only on an ask, this node only, no polling, one event monitor.
- `diplomat watch` runs `tail -F` on the alarm log and, for every line matching `ESCALATE` (change with `--pattern`), prints it and appends it to the inbox. Run it as the session's one Monitor so each alarm wakes the session. `diplomat inbox` prints and clears the spool.
- `diplomat triage` prints temperatures, the top leaf cgroups by CPU and by RAM, and queued jobs, in one call.
- `diplomat relieve` pauses the newest non-critical cgroup under the heavy slice that has processes. It writes `cgroup.freeze`, or falls back to SIGSTOP on every process. `--dry-run` only names the target. `diplomat resume` undoes the latest pause, `--all` undoes every one. It never kills and never reboots.
- `cpu-efficiency apply|revert|status` (also `diplomat cpu-efficiency ...`, needs root) sets governor, energy preference and boost, and optionally a max frequency. The first `apply` saves a baseline, a second `apply` keeps it, and `revert` restores it and deletes it. `systemd/cpu-efficiency.service` applies at boot and reverts on stop.

## Session unit

`systemd/diplomat-session.service` is a user unit that keeps one Claude Code session running. Copy it to `~/.config/systemd/user/`, put the template in the working directory as `CLAUDE.md`, adjust `ExecStart` to your Claude Code install, then `systemctl --user enable --now diplomat-session`. Start `diplomat watch` from the session as its single monitor.

## Mesh notes

Run one session per node and give each its own alarm log. Cross-node access uses per-node ssh with each machine's own key and `known_hosts`; there are no shared secrets, shared tokens or shared accounts. A session reports to the operator and acts only on its own node. Changes to sshd or other node-level services stay with the operator.

## Example

    $ diplomat watch &                      # prints ESCALATE lines as they arrive
    2026-01-01T12:00:00 node-a nvme 71 ESCALATE
    $ diplomat triage --top 3
    $ diplomat relieve
    PAUSED render-7.scope mode=freeze pids=15 (undo: diplomat resume)
    $ diplomat resume
    RESUMED render-7.scope mode=freeze

## Tests

    python3 -m unittest discover -s tests
