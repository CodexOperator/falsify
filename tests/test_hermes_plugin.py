"""hermes_plugin: the thin Hermes layer (a fake ctx; no Hermes install needed)."""
import json
import re
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import hermes_plugin as P  # noqa: E402


class FakeState:
    def __init__(self, d):
        self.data_dir = pathlib.Path(d)


class FakeCtx:
    def __init__(self, d):
        self.state, self.tools, self.hooks, self.skills = FakeState(d), {}, {}, {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = handler

    def register_hook(self, name, fn):
        self.hooks.setdefault(name, []).append(fn)

    def register_skill(self, name, path, description=""):
        self.skills[name] = path


class TestHermesPlugin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = FakeCtx(self.tmp.name)
        P.register(self.ctx)
        self.work = pathlib.Path(self.tmp.name) / "w"
        self.work.mkdir()
        (self.work / "f.py").write_text("X = 1\n")
        self.marker = self.work / "ran"
        chk = {"argv": [sys.executable, "-c", f"open({str(self.marker)!r},'a').write('c');import f;assert f.X==1"],
               "cwd": str(self.work)}
        mut = dict(chk, patch={"path": "f.py", "old": "X = 1", "new": "X = 2"})
        self.rid = json.loads(self.ctx.tools["falsify_register"]({"claim": "X is 1", "check": chk, "mutant": mut}))["record"]

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name, args):
        return json.loads(self.ctx.tools[name](args))

    def approve_hook(self):
        (fn,) = self.ctx.hooks["pre_tool_call"]
        return fn(tool_name="falsify_judge", args={"record": self.rid})

    def human(self, choice):
        (fn,) = self.ctx.hooks["post_approval_response"]
        fn(command="<falsify_judge>", description="", pattern_key="plugin_rule:falsify:" + self.rid,
           session_key="s", choice=choice)

    def test_registers_exactly_the_manifest_surface(self):
        y = (pathlib.Path(P.__file__).parent / "plugin.yaml").read_text()
        decl = lambda key: re.findall(r"^  - (\S+)$", re.split(r"\n(?=\S)", y.split(key + ":\n")[1])[0], re.M)
        self.assertEqual(sorted(self.ctx.hooks), sorted(decl("provides_hooks")))
        self.assertEqual(sorted(self.ctx.tools), sorted(decl("provides_tools")))
        self.assertIn("falsify", self.ctx.skills)

    def test_pre_tool_call_asks_with_the_exact_argv_and_record_rule_key(self):
        d = self.approve_hook()
        self.assertEqual(d["action"], "approve")
        self.assertEqual(d["rule_key"], "falsify:" + self.rid)
        self.assertIn("MUTANT", d["message"])
        self.assertIn(self.rid[:12], d["message"])
        self.assertIsNone(self.ctx.hooks["pre_tool_call"][0](tool_name="terminal", args={"command": "ls"}))

    def test_auto_approved_run_without_a_human_answer_is_void_not_run(self):
        r = self.call("falsify_judge", {"record": self.rid})
        self.assertEqual(r["verdict"], "void")
        self.assertIn("not run", r["why"])
        self.assertFalse(self.marker.exists())

    def test_a_human_deny_runs_nothing(self):
        self.human("deny")
        r = self.call("falsify_judge", {"record": self.rid})
        self.assertEqual(r["verdict"], "void")
        self.assertFalse(self.marker.exists())

    def test_human_once_proves_and_the_grant_is_one_shot(self):
        self.human("once")
        r = self.call("falsify_judge", {"record": self.rid})
        self.assertEqual(r["verdict"], "proved", r)
        r2 = self.call("falsify_judge", {"record": self.rid})
        self.assertEqual(r2["verdict"], "void")
        self.assertIn("not run", r2["why"])

    def test_another_records_approval_grants_nothing(self):
        (fn,) = self.ctx.hooks["post_approval_response"]
        fn(pattern_key="plugin_rule:falsify:" + "0" * 64, choice="once")
        fn(pattern_key="dangerous:rm", choice="once")
        r = self.call("falsify_judge", {"record": self.rid})
        self.assertIn("not run", r["why"])

    def test_pre_verify_nudges_until_done_then_lets_finish(self):
        (pv,) = self.ctx.hooks["pre_verify"]
        self.assertEqual(pv(attempt=0)["action"], "continue")
        self.human("once")
        res = self.call("falsify_judge", {"record": self.rid})
        rid_res = res["result"]
        self.call("falsify_done", {"claim": "X is 1", "result": rid_res})
        self.assertIsNone(pv(attempt=0))

    def test_plugin_never_imports_subprocess(self):
        src = (pathlib.Path(P.__file__)).read_text()
        self.assertNotIn("subprocess", src.replace("never calls subprocess", ""))


if __name__ == "__main__":
    unittest.main()
