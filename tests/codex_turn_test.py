"""Codex 的迟到事件（PR #26 @KQYaili 报的，用例参考了他的测试）：一轮结束（Stop）后，子任务、工具的事件还会陆续到，
上一轮的事件也可能在新的一轮里才到。钩子按「会话 + 轮次（turn_id）」记账，已经结束的轮次的事件直接丢掉，不改状态。
不开真的桌宠、不碰用户配置。
    python3 -m unittest tests.codex_turn_test
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "integrations" / "crosspet-hook.py"


class CodexTurnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name, "state")
        self.data = Path(self.tmp.name, "data")
        self.state.mkdir()
        self.data.mkdir()
        (self.data / "ai-autostart-off").write_text("test")

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, name, tid="T1", sid="s1", host="gpt", **extra):
        ev = {"hook_event_name": name, "session_id": sid, "cwd": "/tmp", "model": "gpt-test", "tool_name": "exec_command", **extra}
        if tid is not None:
            ev["turn_id"] = tid
        env = dict(os.environ, CROSSPET_STATE_DIR=str(self.state), CROSSPET_DATA_DIR=str(self.data),
                   CODEX_HOME=str(Path(self.tmp.name, "no-codex")))
        r = subprocess.run([sys.executable, str(HOOK), host], input=json.dumps(ev).encode(), capture_output=True, env=env, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, b"")   # 状态 Hook 不返回批准 / 拒绝等宿主控制指令
        self.assertEqual(r.stderr, b"")
        p = self.state / f"{host}-state.json"
        s = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
        return s.get("pose"), s.get("event")

    def test_late_events_after_stop_are_dropped(self):
        self.send("UserPromptSubmit")
        self.send("PreToolUse")
        self.assertEqual(self.send("Stop"), ("happy", "Stop"))
        for late in ("PostToolUse", "PreToolUse", "SubagentStart", "SubagentStop"):
            with self.subTest(late=late):
                self.assertEqual(self.send(late), ("happy", "Stop"))

    def test_old_turn_cannot_overwrite_new_turn(self):
        self.send("UserPromptSubmit")
        self.send("Stop")
        self.send("UserPromptSubmit", tid="T2")
        self.assertEqual(self.send("PreToolUse", tid="T2"), ("running", "PreToolUse"))
        self.assertEqual(self.send("PostToolUse", tid="T1"), ("running", "PreToolUse"))   # 上一轮迟到的
        self.assertEqual(self.send("Stop", tid="T2")[1], "Stop")

    def test_superseded_turn_without_stop_is_dropped(self):
        # 上一轮没收到结束事件就开始了新一轮：上一轮迟到的事件也不能盖掉新一轮
        self.send("UserPromptSubmit")
        self.send("PreToolUse")
        self.send("UserPromptSubmit", tid="T2")
        self.assertEqual(self.send("PreToolUse", tid="T2", tool_name="apply_patch")[0], "writing")
        self.assertEqual(self.send("PostToolUse", tid="T1")[0], "writing")

    def test_interrupt_stays_finished(self):
        self.send("UserPromptSubmit")
        self.assertEqual(self.send("Interrupt"), ("surprised", "Stop"))
        self.assertEqual(self.send("SubagentStop"), ("surprised", "Stop"))

    def test_subagent_follows_root_turn(self):
        child = dict(tid="C1", agent_id="child-1")
        self.send("UserPromptSubmit")
        self.send("PreToolUse", tool_name="spawn_agent")
        self.assertEqual(self.send("UserPromptSubmit", **child)[1], "SubagentStart")   # 子任务的第一条消息不算你发了消息
        self.assertEqual(self.send("PreToolUse", **child)[0], "running")
        self.assertEqual(self.send("Stop", **child)[1], "SubagentStop")                 # 子任务停了不等于这一轮结束
        self.assertEqual(self.send("Stop")[1], "Stop")

    def test_subagent_after_root_stop_is_dropped(self):
        child = dict(tid="C1", agent_id="child-1")
        self.send("UserPromptSubmit")
        self.send("Stop")
        for late in ("SubagentStart", "UserPromptSubmit", "PreToolUse", "SubagentStop"):
            with self.subTest(late=late):
                self.assertEqual(self.send(late, **child), ("happy", "Stop"))
        self.assertEqual(self.send("UserPromptSubmit", tid="T2"), ("listening", "UserPromptSubmit"))

    def test_manual_compact_is_its_own_turn(self):
        self.send("UserPromptSubmit")
        self.send("Stop")
        self.assertEqual(self.send("PreCompact", tid="K1", trigger="manual")[0], "compact")
        self.assertEqual(self.send("PostCompact", tid="K1", trigger="manual"), ("happy", "Stop"))
        self.assertEqual(self.send("SubagentStop", tid="T1"), ("happy", "Stop"))

    def test_turn_seen_first_mid_way_is_accepted(self):
        # 钩子刚装上、或者桌宠刚开：第一条就是一轮中间的事件，照常演
        self.assertEqual(self.send("PreToolUse", tid="T9")[0], "running")

    def test_sessions_are_independent(self):
        self.send("UserPromptSubmit", sid="a")
        self.send("Stop", sid="a")
        self.send("UserPromptSubmit", sid="b", tid="B1")
        self.assertEqual(self.send("PreToolUse", sid="b", tid="B1")[0], "running")

    def test_events_without_turn_id_are_unaffected(self):
        # Claude Code 等不带 turn_id：和以前一样，来什么演什么
        self.send("UserPromptSubmit", tid=None, host="claude")
        self.send("Stop", tid=None, host="claude")
        self.assertEqual(self.send("PostToolUse", tid=None, host="claude")[0], "thinking")

    def test_codex_template_covers_permission_and_session_end(self):
        template = json.loads((HOOK.parent / "codex/hooks.json").read_text())["hooks"]
        command = template["PreToolUse"][0]["hooks"][0]["command"]
        for name in ("PermissionRequest", "SessionEnd"):
            self.assertEqual(template[name][0]["hooks"][0]["command"], command)
        for name in ("SessionEnd", "Interrupt"):
            self.assertLessEqual(template[name][0]["hooks"][0]["timeout"], 3)

    def test_permission_wait_recovers_on_next_tool_or_end_event(self):
        cases = [("PreToolUse", {}, "running"), ("PostToolUse", {}, "thinking"),
                 ("PostToolUse", {"tool_response": {"is_error": True}}, "oops"),
                 ("Stop", {}, "happy"), ("Interrupt", {}, "surprised")]
        for i, (name, extra, expected) in enumerate(cases):
            with self.subTest(event=name, extra=extra):
                sid = f"s{i}"
                self.send("UserPromptSubmit", sid=sid)
                self.assertEqual(self.send("PermissionRequest", sid=sid)[0], "asking")
                self.assertEqual(self.send(name, sid=sid, **extra)[0], expected)
                self.send("SessionEnd", sid=sid, tid=None)

    def test_session_end_after_stop_removes_session_with_or_without_turn(self):
        for i, tid in enumerate((None, "T1")):
            with self.subTest(turn_id=tid):
                sid = f"s{i}"
                self.send("UserPromptSubmit", sid=sid)
                self.send("Stop", sid=sid)
                self.assertEqual(self.send("SessionEnd", sid=sid, tid=tid), ("sleeping", "SessionEnd"))
                self.assertNotIn(sid, json.loads((self.state / "gpt-sessions.json").read_text()))

    def test_closed_session_cannot_be_revived_by_late_events(self):
        self.send("UserPromptSubmit")
        self.send("PermissionRequest")
        self.send("SessionEnd", tid=None)
        for name in ("PreToolUse", "PostToolUse", "PermissionRequest", "SubagentStop"):
            for tid in ("T1", None):
                with self.subTest(event=name, turn_id=tid):
                    self.assertEqual(self.send(name, tid=tid), ("sleeping", "SessionEnd"))
                    self.assertEqual(json.loads((self.state / "gpt-sessions.json").read_text()), {})
        self.send("SessionStart", tid=None, source="resume")
        self.assertEqual(self.send("UserPromptSubmit", tid="T2")[0], "listening")
        self.assertEqual(self.send("PreToolUse", tid="T2")[0], "running")

    def test_session_end_cleans_up_when_transcript_is_gone(self):
        self.send("UserPromptSubmit")
        self.send("PermissionRequest")
        self.assertEqual(self.send("SessionEnd", tid=None, transcript_path=str(self.state / "gone.jsonl")),
                         ("sleeping", "SessionEnd"))
        self.assertEqual(json.loads((self.state / "gpt-sessions.json").read_text()), {})

    def test_new_user_turn_can_reopen_session_without_session_start(self):
        self.send("UserPromptSubmit")
        self.send("SessionEnd", tid=None)
        self.assertEqual(self.send("UserPromptSubmit"), ("sleeping", "SessionEnd"))
        self.assertEqual(self.send("UserPromptSubmit", tid="T2"), ("listening", "UserPromptSubmit"))
        self.assertEqual(self.send("PreToolUse", tid="T2")[0], "running")

    def test_other_session_events_do_not_hide_permission_wait(self):
        self.send("PermissionRequest", sid="waiting")
        for name in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"):
            with self.subTest(event=name):
                self.assertEqual(self.send(name, sid="other"), ("asking", "PermissionRequest"))
                current = json.loads((self.state / "gpt-state.json").read_text())
                self.assertEqual(current["session_id"], "waiting")
        self.assertEqual(self.send("PostToolUse", sid="waiting")[0], "thinking")

    def test_resolving_one_permission_does_not_clear_another_wait(self):
        self.send("PermissionRequest", sid="a")
        self.send("PermissionRequest", sid="b")
        self.assertEqual(self.send("PostToolUse", sid="a"), ("asking", "PermissionRequest"))
        sessions = json.loads((self.state / "gpt-sessions.json").read_text())
        self.assertEqual(sessions["a"]["pose"], "thinking")
        self.assertEqual(sessions["b"]["pose"], "asking")
        self.assertEqual(self.send("SessionEnd", sid="a", tid=None), ("asking", "PermissionRequest"))
        self.assertEqual(self.send("PostToolUse", sid="b")[0], "thinking")

    def test_end_one_session_preserves_other_state_and_timestamp(self):
        self.send("PreToolUse", sid="working", tool_name="apply_patch")
        before = json.loads((self.state / "gpt-state.json").read_text())
        self.send("PermissionRequest", sid="waiting")
        self.assertEqual(self.send("SessionEnd", sid="waiting", tid=None), ("writing", "PreToolUse"))
        after = json.loads((self.state / "gpt-state.json").read_text())
        self.assertEqual(after, before)
        self.assertEqual(set(json.loads((self.state / "gpt-sessions.json").read_text())), {"working"})

    def test_idle_and_finished_session_do_not_hide_other_work(self):
        self.send("PreToolUse", sid="working", tool_name="apply_patch")
        for name in ("SessionStart", "Stop", "SessionEnd"):
            with self.subTest(event=name):
                self.assertEqual(self.send(name, sid="other"), ("writing", "PreToolUse"))

    def test_after_animation_is_not_borrowed_from_another_session(self):
        self.send("PreToolUse", sid="a", tool_name="apply_patch")
        self.send("PostToolUse", sid="b")
        state = json.loads((self.state / "gpt-state.json").read_text())
        self.assertEqual(state["session_id"], "b")
        self.assertNotIn("after", state)

    def test_tool_counts_are_independent_between_sessions(self):
        self.send("UserPromptSubmit", sid="a")
        for _ in range(8):
            self.send("PreToolUse", sid="a")
        self.send("UserPromptSubmit", sid="b")
        self.send("Stop", sid="a")
        sessions = json.loads((self.state / "gpt-sessions.json").read_text())
        self.assertEqual(sessions["a"]["pose"], "proud")
        self.assertEqual(sessions["b"]["pose"], "listening")

    def test_concurrent_session_end_cannot_be_overwritten_by_inflight_tools(self):
        self.send("UserPromptSubmit", sid="closing")
        self.send("PreToolUse", sid="working", tool_name="apply_patch")
        def send_event(i):
            return self.send("SessionEnd" if i == 0 else "PreToolUse", sid="closing", tid=None if i == 0 else "T1")
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(send_event, range(16)))
        sessions = json.loads((self.state / "gpt-sessions.json").read_text())
        self.assertEqual(set(sessions), {"working"})
        state = json.loads((self.state / "gpt-state.json").read_text())
        self.assertEqual((state["session_id"], state["pose"]), ("working", "writing"))

    def test_child_end_does_not_close_parent_session(self):
        self.send("PermissionRequest")
        self.assertEqual(self.send("SessionEnd", tid="C1", agent_id="child"), ("asking", "PermissionRequest"))
        self.assertIn("s1", json.loads((self.state / "gpt-sessions.json").read_text()))


if __name__ == "__main__":
    unittest.main()
