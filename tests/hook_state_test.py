"""状态损坏恢复、并发写入和诊断日志回归；只使用临时目录，不接触正在运行的桌宠。"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "integrations/crosspet-hook.py"


class HookStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.data = self.root / "data"
        self.state.mkdir()
        self.data.mkdir()
        (self.data / "ai-autostart-off").touch()
        self.env = dict(os.environ, CROSSPET_STATE_DIR=str(self.state), CROSSPET_DATA_DIR=str(self.data))

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, sid="active", name="PreToolUse", **extra):
        event = {"hook_event_name": name, "session_id": sid, "turn_id": "turn-1",
                 "cwd": "/tmp", "model": "gpt-test", "tool_name": "exec_command", **extra}
        result = subprocess.run([sys.executable, str(HOOK), "gpt"], input=json.dumps(event),
                                text=True, capture_output=True, env=self.env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        return result

    def write(self, name, value):
        (self.state / name).write_text(json.dumps(value), encoding="utf-8")

    def read(self, name):
        return json.loads((self.state / name).read_text(encoding="utf-8"))

    def logs(self):
        path = self.state / "hook-errors.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_recovers_mixed_entries_and_preserves_live_sessions(self):
        good = {"pose": "thinking", "event": "PostToolUse", "ts": time.time()}
        broken = {"model": "gpt-test", "null": None, "array": [], "empty": {},
                  "bad-pose": {**good, "pose": None}, "bad-event": {**good, "event": []}}
        for i, value in enumerate((None, "bad", True, float("nan"), float("inf"), -1, 10 ** 400)):
            broken[f"timestamp-{i}"] = {**good, "ts": value}
        self.write("gpt-sessions.json", {"keep": good, "expired": {**good, "ts": time.time() - 601}, **broken})
        self.assertEqual(self.send().stderr, "")
        sessions = self.read("gpt-sessions.json")
        self.assertEqual(set(sessions), {"keep", "active"})
        self.assertEqual(sessions["keep"], good)
        self.assertEqual(sessions["active"]["pose"], "running")
        recovery = [e for e in self.logs() if e["stage"] == "session_entries_recovered"]
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0]["discarded"], len(broken))
        self.send(name="PostToolUse")
        self.assertEqual(len(self.logs()), 1)   # 已恢复的数据不反复报错

    def test_recovers_invalid_json_and_wrong_root_types(self):
        for raw in ('{"incomplete":', '[]', 'null', '"gpt-test"'):
            with self.subTest(raw=raw):
                (self.state / "gpt-sessions.json").write_text(raw)
                self.assertEqual(self.send().stderr, "")
                self.assertEqual(set(self.read("gpt-sessions.json")), {"active"})
                self.assertEqual(self.logs()[-1]["stage"], "state_file_recovered")

    def test_recovers_invalid_model_entries_without_a_model_in_event(self):
        self.write("gpt-models.json", {"active": {"pose": "running"}, "keep": "gpt-test"})
        self.send(model="")
        self.assertEqual(self.read("gpt-models.json"), {"keep": "gpt-test"})
        self.assertIn("active", self.read("gpt-sessions.json"))
        self.assertEqual(self.logs()[0]["stage"], "model_entries_recovered")

    def test_concurrent_hooks_recover_and_keep_all_sessions(self):
        self.write("gpt-sessions.json", {"old": "gpt-test"})
        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(lambda i: self.send(sid=f"s{i}"), range(32)))
        self.assertTrue(all(not result.stderr for result in results))
        sessions = self.read("gpt-sessions.json")
        models = self.read("gpt-models.json")
        self.assertEqual(set(sessions), {f"s{i}" for i in range(32)})
        self.assertEqual(set(models), set(sessions))
        self.assertTrue(all(isinstance(v, dict) and v["pose"] == "running" for v in sessions.values()))
        self.assertTrue(all(v == "gpt-test" for v in models.values()))
        self.assertEqual([e["stage"] for e in self.logs()], ["session_entries_recovered"])

    def test_session_end_removes_only_its_session(self):
        good = {"pose": "thinking", "event": "PostToolUse", "ts": time.time()}
        self.write("gpt-sessions.json", {"active": good, "keep": good, "old": "gpt-test"})
        self.send(name="SessionEnd")
        self.assertEqual(self.read("gpt-sessions.json"), {"keep": good})

    def test_io_failure_is_logged_without_leaking_event_contents(self):
        (self.state / "gpt-sessions.json").mkdir()
        self.assertEqual(self.send(prompt="PRIVATE_PROMPT", tool_input={"command": "PRIVATE_COMMAND"}).stderr, "")
        error = self.logs()[-1]
        self.assertEqual(error["stage"], "update_sessions")
        self.assertEqual(error["error"], "IsADirectoryError")
        self.assertEqual(error["session_id"], "active")
        self.assertEqual(error["hook_event_name"], "PreToolUse")
        self.assertNotIn("PRIVATE_", (self.state / "hook-errors.jsonl").read_text())
        self.assertEqual(self.read("gpt-state.json")["pose"], "running")

    def test_lock_failure_does_not_overwrite_the_session_table(self):
        good = {"keep": {"pose": "thinking", "event": "PostToolUse", "ts": time.time()}}
        self.write("gpt-sessions.json", good)
        (self.state / ".gpt-sessions.lock").mkdir()
        self.send()
        self.assertEqual(self.read("gpt-sessions.json"), good)
        self.assertEqual(self.logs()[-1]["stage"], "update_sessions")

    def test_write_failure_is_logged_and_temporary_file_is_removed(self):
        (self.state / "gpt-state.json").mkdir()
        self.send()
        self.assertEqual(self.logs()[-1]["stage"], "write_state")
        self.assertEqual(list(self.state.glob(".gpt-state.*.json")), [])
        self.assertIn("active", self.read("gpt-sessions.json"))

    def test_concurrent_error_logs_rotate_and_remain_complete(self):
        previous = '{"old":true}\n' * 90000
        (self.state / "hook-errors.jsonl").write_text(previous)
        (self.state / "gpt-sessions.json").mkdir()
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(lambda i: self.send(sid=f"s{i}"), range(16)))
        self.assertEqual((self.state / "hook-errors.jsonl.1").read_text(), previous)
        entries = self.logs()
        self.assertEqual(len(entries), 16)
        self.assertEqual({e["session_id"] for e in entries}, {f"s{i}" for i in range(16)})
        self.assertTrue(all(e["stage"] == "update_sessions" for e in entries))

    def test_log_failure_falls_back_to_stderr(self):
        (self.state / "hook-errors.jsonl").mkdir()
        (self.state / "gpt-sessions.json").mkdir()
        result = self.send()
        self.assertEqual(json.loads(result.stderr)["stage"], "update_sessions")

    def test_normal_events_do_not_emit_diagnostics(self):
        self.assertEqual(self.send().stderr, "")
        self.assertEqual(self.logs(), [])


if __name__ == "__main__":
    unittest.main()
