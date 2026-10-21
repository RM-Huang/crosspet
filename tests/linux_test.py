"""Linux 外壳 / 安装 / 接入协议回归；不打开 GUI、不使用真实 AI 配置。"""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/linux"))
from core import Store, desktop_entry, query_codex, read_json, write_json
spec = importlib.util.spec_from_file_location("linux_integrate", ROOT / "tools/integrate.py")
integrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrate)


@unittest.skipUnless(sys.platform == "linux", "Linux only")
class LinuxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="crosspet-test-")
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CROSSPET_DATA_DIR": str(self.root / "data with spaces"),
            "CROSSPET_CONFIG_DIR": str(self.root / "config"),
            "CROSSPET_STATE_DIR": str(self.root / "state"),
        })
        self.env.start()
        self.store = Store()
        for p in (self.store.data, self.store.config, self.store.state):
            p.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def hook(self, event=None):
        args = ["gpt"] if event else ["--ensure"]
        result = subprocess.run([sys.executable, str(ROOT / "integrations/crosspet-hook.py"), *args],
                                input=json.dumps(event) if event else "", text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.store.state / "autostart.txt"
        return log.read_text() if log.exists() else ""

    def test_linux_template_executes_real_hook_and_preserves_other_entries(self):
        # 实际执行生成的命令，验证带空格路径、数据目录和状态目录一起传给子进程。
        import shutil
        shutil.copy2(ROOT / "integrations/crosspet-hook.py", self.store.data / "crosspet-hook.py")
        settings = self.root / "settings.json"
        keep = {"type": "command", "command": "echo user-hook"}
        write_json(settings, {"theme": "custom", "hooks": {"PostToolUse": [{"matcher": "*", "hooks": [
            keep, {"command": "old crosspet-hook.py"}]}]}})
        with patch.object(integrate, "SUPPORT", self.store.data), patch.object(integrate, "STATE_DIR", self.store.state):
            integrate.merge_hooks(settings, ROOT / "integrations/codex/hooks.json")
            first = read_json(settings)
            integrate.merge_hooks(settings, ROOT / "integrations/codex/hooks.json")
            self.assertEqual(first, read_json(settings))
            command = next(h["command"] for g in first["hooks"]["PostToolUse"] for h in g["hooks"] if "crosspet-hook.py" in h["command"])
            event = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "test"}
            result = subprocess.run(shlex.split(command), input=json.dumps(event), text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(read_json(self.store.state / "gpt-state.json")["pose"], "thinking")
            integrate.remove_hooks(settings)
        self.assertEqual(read_json(settings), {"theme": "custom", "hooks": {"PostToolUse": [{"matcher": "*", "hooks": [keep]}]}})

    def test_unknown_config_is_not_overwritten(self):
        path = self.root / "settings.json"
        original = '{"hooks": broken-json}'
        path.write_text(original)
        with self.assertRaises(ValueError):
            integrate.merge_hooks(path, ROOT / "integrations/codex/hooks.json")
        self.assertEqual(path.read_text(), original)

    def test_macos_template_is_unchanged(self):
        template = ROOT / "integrations/codex/hooks.json"
        with patch.object(sys, "platform", "darwin"):
            self.assertEqual(integrate.load_template(template), read_json(template))

    def test_install_copy_is_launchable_and_preserves_user_data(self):
        from install import install
        prefix = self.root / "prefix with spaces"
        share = prefix / "share"
        custom = self.store.data / "characters/custom"
        custom.mkdir(parents=True)
        (custom / "character.json").write_text('{"name":"keep"}')
        self.store.settings["size"] = 1.3
        self.store.save()
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(share)}):
            launcher = install(prefix)
            self.assertTrue((share / "applications/crosspet.desktop").exists())
            result = subprocess.run([str(launcher), "--check"], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            install(prefix)
        self.assertEqual(Store().settings["size"], 1.3)
        self.assertEqual((custom / "character.json").read_text(), '{"name":"keep"}')
        self.assertEqual(read_json(self.store.data / "launch.json")["argv"], [str(launcher)])

    def test_linux_autostart_off_is_honored(self):
        (self.store.data / "ai-autostart-off").write_text("1")
        self.assertIn("设置里关掉", self.hook())

    def test_hook_data_paths_keep_qwen_and_autostart_together(self):
        # 默认路径、XDG 路径和显式覆盖都要同时用于找角色和读自动启动开关。
        for mode in ("default", "xdg", "override"):
            with self.subTest(mode=mode):
                root = self.root / mode
                env = dict(os.environ, HOME=str(root / "home"), CROSSPET_STATE_DIR=str(root / "state"))
                env.pop("CROSSPET_DATA_DIR", None)
                env.pop("XDG_DATA_HOME", None)
                data = root / "home/.local/share/crosspet"
                if mode in ("xdg", "override"):
                    env["XDG_DATA_HOME"] = str(root / "xdg data")
                    data = root / "xdg data/crosspet"
                if mode == "override":
                    data = root / "custom data"
                    env["CROSSPET_DATA_DIR"] = str(data)
                data.mkdir(parents=True)
                (data / "ai-autostart-off").write_text("1")
                (data / "characters").symlink_to(ROOT / "characters")
                event = {"hook_event_name": "SessionStart", "session_id": mode, "model": "qwen3.8-max"}
                result = subprocess.run(
                    [sys.executable, str(ROOT / "integrations/crosspet-hook.py"), "qwen"],
                    input=json.dumps(event), text=True, capture_output=True, env=env, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(read_json(root / "state/qwen-host.json")["character"], "qwen")
                self.assertIn("设置里关掉", (root / "state/autostart.txt").read_text())

    def test_linux_single_instance_uses_lock_not_pid(self):
        with (self.store.data / "instance.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIn("已经在跑", self.hook())

    def test_linux_missing_install_is_explained(self):
        self.assertIn("请先安装", self.hook())

    def test_linux_launcher_receives_state_paths(self):
        marker = self.root / "launched.json"
        script = self.root / "fake launcher.py"
        script.write_text("import os,json,pathlib\npathlib.Path(" + repr(str(marker)) +
                          ").write_text(json.dumps([os.environ['CROSSPET_DATA_DIR'],os.environ['CROSSPET_STATE_DIR']]))\n")
        write_json(self.store.data / "launch.json", {"argv": [sys.executable, str(script)]})
        self.assertIn("已打开", self.hook())
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(read_json(marker), [str(self.store.data), str(self.store.state)])

    def test_manual_quit_suppresses_current_ai(self):
        (self.store.data / "user-quit").write_text("1")
        self.assertIn("手动退出过", self.hook())

    def test_partial_state_is_retried_and_old_state_ignored(self):
        p = self.store.state / "gpt-state.json"
        p.write_text('{"pose":')
        self.assertEqual(list(self.store.changes(["gpt"])), [])
        write_json(p, {"pose": "reading", "ts": time.time()})
        self.assertEqual(list(self.store.changes(["gpt"]))[0][2]["pose"], "reading")
        self.assertEqual(list(self.store.changes(["gpt"])), [])
        os.utime(p, (time.time()-700, time.time()-700))
        self.assertEqual(list(self.store.changes(["gpt"])), [])

    def test_manifest_uses_shared_skin_and_keeps_egg_out_of_menu(self):
        # 链接真实资源，不为了纯数据测试复制全部图片。
        (self.store.data / "characters").symlink_to(ROOT / "characters")
        original, choices = self.store.manifest()
        self.assertIn("gpt", original["characters"])
        skins = next(c for c in choices if c.get("skins"))
        self.store.settings["skins"][skins["id"]] = skins["skins"][1]["id"]
        self.store.settings["names"][skins["id"]] = "测试宠物"
        manifest, choices = self.store.manifest()
        char = manifest["characters"][skins["id"]]
        self.assertEqual(char["skin"], skins["skins"][1]["id"])
        self.assertEqual(char["name"], "测试宠物")
        self.assertTrue(char["poses"]["idle"][0].startswith("file://"))
        self.assertFalse(any(manifest["characters"][c["id"]].get("egg") for c in choices))

    def test_settings_persist_and_validate_bad_scale(self):
        self.store.settings["size"] = 1.3
        self.store.save()
        self.assertEqual(Store().settings["size"], 1.3)
        write_json(self.store.config / "settings.json", {"size": "bad", "textSize": -3})
        self.assertEqual(Store().settings["size"], 1)

    def test_codex_protocol_and_errors(self):
        fake = str(ROOT / "tests/fake_codex.py")
        tracked = []
        result = query_codex(fake, timeout=2, on_process=tracked.append)
        self.assertEqual(result["rateLimits"]["primary"]["usedPercent"], 3)
        self.assertNotIn("accountId", json.dumps(result))
        self.assertIsNone(tracked[-1])
        self.assertIsNotNone(tracked[0].poll())
        for mode in ("auth", "exit", "nomethod"):
            tracked.clear()
            with patch.dict(os.environ, {"FAKE_CODEX_MODE": mode}), self.assertRaises((RuntimeError, BrokenPipeError)):
                query_codex(fake, timeout=1, on_process=tracked.append)
            self.assertIsNone(tracked[-1])
            self.assertIsNotNone(tracked[0].poll())
        tracked.clear()
        with patch.dict(os.environ, {"FAKE_CODEX_MODE": "hang"}), self.assertRaises(TimeoutError):
            query_codex(fake, timeout=.2, on_process=tracked.append)
        self.assertIsNone(tracked[-1])
        self.assertIsNotNone(tracked[0].poll())

    def test_desktop_entry_escapes_field_codes_and_spaces(self):
        result = desktop_entry(["/tmp/space dir/100%/app", "--show"])
        self.assertIn('Exec="/tmp/space dir/100%%/app" "--show"', result)


if __name__ == "__main__":
    unittest.main()
