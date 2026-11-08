"""Tests for keepalive/ and backstop/. Run: python3 -m unittest discover -s tests"""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_backstop(**env):
    for k in ("BACKSTOP_MARKER", "BACKSTOP_STATE_FILE", "BACKSTOP_DIR"):
        os.environ.pop(k, None)
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("session_backstop", ROOT / "backstop" / "session_backstop.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Backstop(unittest.TestCase):
    def test_in_flight_marker(self):
        with tempfile.TemporaryDirectory() as d:
            st = Path(d) / "STATE.md"
            m = load_backstop(BACKSTOP_DIR=d, BACKSTOP_STATE_FILE=str(st))
            st.write_text("# State\nIn flight: build the release\n")
            self.assertTrue(m.in_flight())
            st.write_text("# State\nIn flight:\n")
            self.assertFalse(m.in_flight())
            st.write_text("nothing here\n")
            self.assertFalse(m.in_flight())

    def test_custom_marker(self):
        with tempfile.TemporaryDirectory() as d:
            st = Path(d) / "S.md"
            st.write_text("Open work: fix the build\n")
            m = load_backstop(BACKSTOP_DIR=d, BACKSTOP_STATE_FILE=str(st), BACKSTOP_MARKER="Open work")
            self.assertTrue(m.in_flight())

    def test_no_tmux_session_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, BACKSTOP_DIR=d, BACKSTOP_TMUX="no-such-session-xyz", HOOK_FIRES_LOG=d + "/f.log",
                       BACKSTOP_LOG=d + "/heal.tsv")
            r = subprocess.run(["python3", str(ROOT / "backstop" / "session_backstop.py")], env=env, timeout=30)
            self.assertEqual(r.returncode, 0)
            self.assertFalse(os.path.exists(d + "/heal.tsv"))


class IdleWake(unittest.TestCase):
    def run_hook(self, payload, **env):
        with tempfile.TemporaryDirectory() as d:
            e = dict(os.environ, HOOK_FIRES_LOG=d + "/f.log", WAKE_TMUX="no-such-session-xyz", **env)
            r = subprocess.run(["python3", str(ROOT / "hooks" / "idle_wake.py")], input=payload, text=True, env=e, timeout=20)
            return r.returncode, os.path.exists(d + "/f.log")

    def test_garbage_input_exits_zero(self):
        self.assertEqual(self.run_hook("not json")[0], 0)

    def test_missing_session_is_silent(self):
        rc, logged = self.run_hook('{"hook_event_name":"StopFailure","cwd":"/tmp"}')
        self.assertEqual(rc, 0)
        self.assertFalse(logged)


class Keepalive(unittest.TestCase):
    def test_syntax(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / "keepalive" / "session-keepalive.sh")]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
