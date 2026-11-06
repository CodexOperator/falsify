"""hermes_plugin: the thin Hermes layer (a fake ctx; no Hermes install needed)."""
import json
import re
import os
import subprocess  # the TEST builds a git repo; the plugin itself never imports subprocess
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

import importlib.util

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_like_hermes():
    """Load the repo root as ONE package the way Hermes's loader does (spec_from_file_location +
    submodule_search_locations), so the relative imports are exercised as the host runs them."""
    spec = importlib.util.spec_from_file_location("falsify_plugin_under_test", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return sys.modules[spec.name + ".hermes_plugin"]


P = _load_like_hermes()


class FakeState:
    def __init__(self, d):
        self.data_dir = pathlib.Path(d)


class FakeCtx:
    def __init__(self, d):
        self.state, self.tools, self.hooks, self.skills, self.schemas, self.settings = FakeState(d), {}, {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name], self.schemas[name] = handler, schema

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
        (self.work / "f.py").write_text("X = 0\n")
        g = lambda *a: subprocess.run(["git", "-C", str(self.work), *a], check=True, capture_output=True)
        g("init", "-q")
        g("add", "-A")
        g("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
        self.marker = pathlib.Path(self.tmp.name) / "ran"
        chk = {"argv": [sys.executable, "-c", f"open({str(self.marker)!r},'a').write('c');import f;assert f.X==1"]}
        self.rid = json.loads(self.ctx.tools["falsify_register"]({"claim": "X is 1", "repo": str(self.work), "check": chk}))["record"]
        (self.work / "f.py").write_text("X = 1\n")   # the agent's change, made after registering

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
        y = (ROOT / "plugin.yaml").read_text()
        decl = lambda key: re.findall(r"^  - (\S+)$", re.split(r"\n(?=\S)", y.split(key + ":\n")[1])[0], re.M)
        self.assertEqual(sorted(self.ctx.hooks), sorted(decl("provides_hooks")))
        self.assertEqual(sorted(self.ctx.tools), sorted(decl("provides_tools")))
        self.assertIn("falsify", self.ctx.skills)

    def test_pre_tool_call_asks_with_the_exact_argv_and_record_rule_key(self):
        d = self.approve_hook()
        self.assertEqual(d["action"], "approve")
        self.assertEqual(d["rule_key"], "falsify:" + self.rid)
        self.assertIn("MUTANT", d["message"])
        self.assertIn(repr([sys.executable, "-c", f"open({str(self.marker)!r},'a').write('c');import f;assert f.X==1"]), d["message"])
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

    def test_register_takes_a_bare_list_and_a_bad_check_gets_its_shape(self):
        r = self.call("falsify_register", {"claim": "Y", "repo": str(self.work), "check": [sys.executable, "-c", "pass"]})
        self.assertEqual(len(r["record"]), 64, r)
        err = self.call("falsify_register", {"claim": "Y", "repo": str(self.work), "check": "pytest -q"})["error"]
        self.assertIn("check.argv must be a non-empty list of strings; expected {\"argv\"", err)
        self.assertIn("e.g. {\"argv\": [", err)
        chk = self.ctx.schemas["falsify_register"]["parameters"]["properties"]["check"]
        self.assertIn({"type": "array", "items": {"type": "string"}}, chk["anyOf"])
        self.assertIn("e.g.", chk["description"])

    def test_a_proved_judge_ends_on_the_done_call_and_status_lists_it_first(self):
        other = self.call("falsify_register", {"claim": "Z", "repo": str(self.work), "check": [sys.executable, "-c", "pass"]})["record"]
        self.human("once")
        r = self.call("falsify_judge", {"record": self.rid})
        self.assertEqual(r["verdict"], "proved", r)
        call = f'now call falsify_done(claim="X is 1", result="{r["result"]}") to close this claim'
        self.assertEqual(r["next"], call)
        self.assertTrue(r["why"].endswith(call), r["why"])
        st = self.call("falsify_status", {})["open"]
        self.assertEqual([(o["record"], o["verdict"]) for o in st], [(self.rid, "proved"), (other, "unjudged")])
        self.assertEqual(st[0]["next"], call)
        self.assertNotIn("next", st[1])
        stored, _ = P.Store(self.ctx.state.data_dir / "store").load("results", r["result"])
        self.assertNotIn("falsify_done", stored["why"])   # the stored result is unchanged

    def test_the_drafter_key_comes_from_hermes_secret_scope_never_os_environ(self):
        class Guarded(dict):   # an os.environ that refuses every drafter variable
            def _no(self, k):
                if str(k).startswith("FALSIFY_DRAFT"):
                    raise AssertionError(f"the plugin read {k} from os.environ")
            def __getitem__(self, k):
                self._no(k)
                return super().__getitem__(k)
            def get(self, k, d=None):
                self._no(k)
                return super().get(k, d)
            def __contains__(self, k):
                self._no(k)
                return super().__contains__(k)
        asked, sent = [], []
        secrets = {"FALSIFY_DRAFT_URL": "http://drafter:1", "FALSIFY_DRAFT_KEY": "k-from-scope"}
        ctx = FakeCtx(tempfile.mkdtemp())
        ctx.settings.update(draft_api="anthropic", draft_model="m", draft_k=2)
        mk = lambda url, model, k, api_key=None, **kw: sent.append((url, api_key, model, k, kw["api"])) or "drafter"
        with unittest.mock.patch.object(os, "environ", Guarded({**os.environ, "FALSIFY_DRAFT_KEY": "k-from-ENV"})), \
                unittest.mock.patch.object(P.draft, "make_drafter", mk):
            self.assertEqual(P.drafter_for(ctx, get_secret=lambda n: asked.append(n) or secrets.get(n)), "drafter")
            P.register(ctx, get_secret=secrets.get)
            ctx.tools["falsify_status"]({})          # first use: the LazyStore builds its drafter here
        self.assertEqual(sorted(set(asked)), ["FALSIFY_DRAFT_KEY", "FALSIFY_DRAFT_URL"])
        self.assertEqual(sent, [("http://drafter:1", "k-from-scope", "m", 2, "anthropic")] * 2)
        self.assertIsNone(P.drafter_for(ctx, get_secret=lambda n: None))     # no URL: no drafter, no network
        self.assertIsNone(P.drafter_for(ctx, get_secret=lambda n: 1 / 0))    # an unscoped read fails closed

    def test_the_manifest_declares_the_drafter_secrets(self):
        y = (ROOT / "plugin.yaml").read_text()
        self.assertIn("draft_key: {type: secret, env: FALSIFY_DRAFT_KEY", y)
        self.assertIn("draft_url: {type: secret, env: FALSIFY_DRAFT_URL", y)
        self.assertNotIn("requires_env", y)   # an optional key must never gate loading

    def test_register_writes_nothing(self):
        d = tempfile.mkdtemp()
        ctx = FakeCtx(d)
        P.register(ctx)
        self.assertEqual(os.listdir(d), [])

    def test_plugin_never_imports_subprocess(self):
        src = (pathlib.Path(P.__file__)).read_text()
        self.assertNotIn("subprocess", src.replace("never calls subprocess", ""))


if __name__ == "__main__":
    unittest.main()
