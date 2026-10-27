"""Tests on stub cgroup trees, stub sysfs and stub commands. Run: python3 -m unittest discover -s tests"""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from diplomat import triage


def mk_cgroup(root, rel, pids=(), mem=0, cpu=0, mtime=None):
    d = Path(root) / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "cgroup.procs").write_text("".join(f"{p}\n" for p in pids))
    (d / "cgroup.freeze").write_text("0")
    (d / "memory.current").write_text(f"{mem}\n")
    (d / "cpu.stat").write_text(f"usage_usec {cpu}\nuser_usec 0\n")
    if mtime:
        os.utime(d, (mtime, mtime))
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cg = self.d / "cg"
        self.cg.mkdir()
        self.env = {**os.environ, "PYTHONPATH": str(ROOT), "DIPLOMAT_STATE_DIR": str(self.d / "state"),
                    "DIPLOMAT_CGROUP_ROOT": str(self.cg), "CPUFREQ_DIR": str(self.d / "cpufreq"),
                    "CPU_EFFICIENCY_BASELINE": str(self.d / "baseline.json")}

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, env=None, mod="diplomat"):
        return subprocess.run([sys.executable, "-m", mod, *args], check=False, env={**self.env, **(env or {})},
                              capture_output=True, text=True, timeout=60)

    def stub(self, name, body):
        p = self.d / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)
        return str(p)


class Tests(Base):
    def test_watch_spools_only_escalate_lines_and_template_has_rules(self):
        alarm, inbox = self.d / "alarms.log", self.d / "inbox"
        alarm.write_text("t1 node-a cpu 66 warn\nt2 node-a nvme 71 ESCALATE\nt3 node-a cpu 50 restored\n")
        r = self.run_cli("watch", "--alarm-file", str(alarm), "--inbox", str(inbox), "--replay", "--max", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "t2 node-a nvme 71 ESCALATE")
        self.assertIn("nvme 71 ESCALATE", inbox.read_text())
        self.assertEqual(len(inbox.read_text().splitlines()), 1)
        self.assertIn("nvme 71", self.run_cli("inbox", env={"DIPLOMAT_INBOX": str(inbox)}).stdout)
        self.assertEqual(self.run_cli("inbox", env={"DIPLOMAT_INBOX": str(inbox)}).stdout, "")
        tpl = self.run_cli("template").stdout
        for rule in ("Act only on an ask", "this node only", "No polling", "One event monitor"):
            self.assertIn(rule.lower(), tpl.lower())

    def test_triage_prints_temps_cgroups_and_jobs_from_stubs(self):
        mk_cgroup(self.cg, "heavy.slice/a.service", mem=300 * 2**20, cpu=10)
        mk_cgroup(self.cg, "heavy.slice/b.service", mem=900 * 2**20, cpu=20)
        temps = self.stub("temps", 'echo "cpu: +51.0°C"; echo "label noise"')
        jobs = self.stub("jobs", 'echo "job-1 queued"')
        r = self.run_cli("triage", "--sample", "0", env={"DIPLOMAT_TEMPS_CMD": temps, "DIPLOMAT_JOBS_CMD": jobs})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("+51.0°C", out)
        self.assertNotIn("label noise", out)
        self.assertIn("job-1 queued", out)
        ram = out.split("== top cgroups by RAM")[1].split("== queued jobs")[0]
        self.assertLess(ram.index("b.service"), ram.index("a.service"))
        ranked = triage.rank_cpu({Path("x"): 0, Path("y"): 0}, {Path("x"): 500_000, Path("y"): 2_000_000}, 2.0)
        self.assertEqual([str(d) for _, d in ranked], ["y", "x"])
        self.assertAlmostEqual(ranked[0][0], 100.0)

    def test_relieve_freezes_newest_noncritical_and_resume_undoes(self):
        now = time.time()
        old = mk_cgroup(self.cg, "heavy.slice/old.service", pids=[999991], mtime=now - 300)
        new = mk_cgroup(self.cg, "heavy.slice/new.service", pids=[999992], mtime=now - 100)
        mk_cgroup(self.cg, "heavy.slice/sshd.service", pids=[999993], mtime=now - 10)    # critical: skipped
        mk_cgroup(self.cg, "heavy.slice/idle.service", pids=[], mtime=now - 5)           # empty: skipped
        r = self.run_cli("relieve", "--mode", "freeze")
        self.assertIn("PAUSED new.service mode=freeze", r.stdout)
        self.assertEqual((new / "cgroup.freeze").read_text(), "1")
        self.assertIn("PAUSED old.service", self.run_cli("relieve", "--mode", "freeze").stdout)
        self.assertEqual(self.run_cli("relieve", "--mode", "freeze").returncode, 1)      # nothing left to pause
        self.assertIn("RESUMED old.service", self.run_cli("resume").stdout)
        self.assertEqual((old / "cgroup.freeze").read_text(), "0")
        self.assertEqual((new / "cgroup.freeze").read_text(), "1")
        self.assertIn("RESUMED new.service", self.run_cli("resume", "--all").stdout)
        self.assertEqual((new / "cgroup.freeze").read_text(), "0")
        self.assertEqual(self.run_cli("resume").stdout.strip(), "nothing to resume")

    def test_relieve_falls_back_to_sigstop_and_resume_continues(self):
        child = subprocess.Popen(["sleep", "60"])
        try:
            d = mk_cgroup(self.cg, "heavy.slice/job.service", pids=[child.pid])
            (d / "cgroup.freeze").unlink()
            (d / "cgroup.freeze").mkdir()       # not writable as a file: the freezer path fails, SIGSTOP takes over
            state = lambda: Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            self.assertIn("mode=stop", self.run_cli("relieve").stdout)
            time.sleep(0.3)
            self.assertEqual(state(), "T")
            self.run_cli("resume")
            time.sleep(0.3)
            self.assertNotEqual(state(), "T")
        finally:
            child.send_signal(signal.SIGKILL)
            child.wait()

    def test_cpu_efficiency_apply_keeps_first_baseline_and_revert_restores(self):
        base = self.d / "cpufreq"
        for n in ("policy0", "policy1"):
            (base / n).mkdir(parents=True)
            (base / n / "scaling_governor").write_text("performance\n")
            (base / n / "energy_performance_preference").write_text("balance_performance\n")
            (base / n / "scaling_max_freq").write_text("4700000\n")
        (base / "boost").write_text("1\n")
        env = {"CPU_EFFICIENCY_MAX_KHZ": "3600000"}
        self.assertEqual(self.run_cli("apply", env=env, mod="diplomat.cpu").returncode, 0)
        self.assertEqual((base / "policy1" / "energy_performance_preference").read_text(), "power")
        self.assertEqual((base / "policy1" / "scaling_governor").read_text(), "powersave")
        self.assertEqual((base / "policy0" / "scaling_max_freq").read_text(), "3600000")
        self.assertEqual((base / "boost").read_text(), "0")
        self.run_cli("apply", env=env, mod="diplomat.cpu")                    # second apply must not overwrite the baseline
        self.assertEqual(self.run_cli("revert", mod="diplomat.cpu").returncode, 0)
        self.assertEqual((base / "policy0" / "scaling_governor").read_text(), "performance")
        self.assertEqual((base / "policy0" / "energy_performance_preference").read_text(), "balance_performance")
        self.assertEqual((base / "policy1" / "scaling_max_freq").read_text(), "4700000")
        self.assertEqual((base / "boost").read_text(), "1")
        self.assertFalse((self.d / "baseline.json").exists())
        self.assertEqual(self.run_cli("revert", mod="diplomat.cpu").returncode, 1)


if __name__ == "__main__":
    unittest.main()
