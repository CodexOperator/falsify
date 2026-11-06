"""Tests for falsify.core and falsify.adapter: stdlib unittest, every check a tiny python -c or script in a temp tree."""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from falsify import core as F  # noqa: E402

PY = sys.executable
ok = lambda **k: {"argv": [PY, "-c", "pass"], **k}
bad = lambda **k: {"argv": [PY, "-c", "raise SystemExit(1)"], **k}

SUBJECT = "def add(a, b):\n    return a + b\n"
TEST = "from subject import add\nassert add(2, 3) == 5\n"
WEAK = "from subject import add\nassert add(2, 3) is not None\n"   # a check too weak to notice the mutant


YES = lambda *a: True


class Store(F.Store):
    def judge_yes(self, rid):
        return self.judge(rid, approve=YES)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.S = Store(os.path.join(self.tmp.name, "store"))
        self.tree = os.path.join(self.tmp.name, "tree")
        os.makedirs(self.tree)
        for n, t in (("subject.py", SUBJECT), ("test_ok.py", TEST), ("test_weak.py", WEAK)):
            open(os.path.join(self.tree, n), "w").write(t)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, script):
        return {"argv": [PY, script], "cwd": self.tree}

    def mutant(self, script, new="return a - b"):
        return {"argv": [PY, script], "cwd": self.tree, "patch": {"path": "subject.py", "old": "return a + b", "new": new}}


class Register(Base):
    def test_hash_is_content_and_rewrite_is_refused(self):
        h = self.S.register("add works", ok(), bad())
        p = os.path.join(self.S.root, "records", h + ".json")
        self.assertEqual(F.sha(open(p, "rb").read()), h)
        rec = json.loads(open(p, "rb").read())
        open(p, "wb").write(F.canon({**rec, "claim": "edited"}))       # someone rewrites the file in place
        with self.assertRaises(F.Refused):
            self.S._write_once("records", rec)                          # re-writing the original bytes is refused
        self.assertIn("MOVED", self.S.load("records", h)[1])

    def test_bad_specs_are_refused(self):
        for c, m in ((ok(argv=[]), bad()), (ok(), {"argv": "true"}), (ok(patch={"path": "a", "old": "x", "new": "y"}), bad()),
                     (ok(), bad(patch={"path": "a", "old": "x", "new": "x"}))):
            with self.assertRaises(F.Refused):
                self.S.register("c", c, m)
        with self.assertRaises(F.Refused):
            self.S.register("  ", ok(), bad())


class Judge(Base):
    def test_a_real_check_with_a_killing_mutant_is_proved(self):
        h, r = self.S.judge_yes(self.S.register("add works", self.check("test_ok.py"), self.mutant("test_ok.py")))
        self.assertEqual(r["verdict"], "proved", r["why"])
        self.assertFalse(r["mutant_run"]["passed"])
        self.assertTrue(r["check_run"]["passed"])
        self.assertEqual(open(os.path.join(self.tree, "subject.py")).read(), SUBJECT)   # the real tree is untouched

    def test_a_failing_check_is_disproved_not_an_error(self):
        open(os.path.join(self.tree, "subject.py"), "w").write(SUBJECT.replace("a + b", "a * b"))
        m = {"argv": [PY, "test_ok.py"], "cwd": self.tree, "patch": {"path": "subject.py", "old": "return a * b", "new": "return 0"}}
        rid = self.S.register("add works", self.check("test_ok.py"), m)
        _, r = self.S.judge_yes(rid)
        self.assertEqual(r["verdict"], "disproved", r["why"])

    def test_PLANTED_tautology_reads_void(self):
        """The planted tautological check: it passes whatever the subject is, so its mutant passes too -> VOID."""
        taut = {"argv": [PY, "-c", "assert True"], "cwd": self.tree}
        _, r = self.S.judge_yes(self.S.register("add works", taut, {**taut, "patch": {"path": "subject.py", "old": "return a + b", "new": "return None"}}))
        self.assertEqual(r["verdict"], "void")
        self.assertIn("mutant PASSED", r["why"])

    def test_a_weak_check_the_mutant_survives_reads_void(self):
        _, r = self.S.judge_yes(self.S.register("add works", self.check("test_weak.py"), self.mutant("test_weak.py")))
        self.assertEqual(r["verdict"], "void")

    def test_vacuous_by_construction_is_void_before_running(self):
        _, r = self.S.judge_yes(self.S.register("x", ok(), ok()))
        self.assertEqual(r["verdict"], "void")
        self.assertIn("by construction", r["why"])
        self.assertIsNone(r["mutant_run"])

    def test_a_moved_record_reads_void(self):
        rid = self.S.register("add works", self.check("test_ok.py"), self.mutant("test_ok.py"))
        p = os.path.join(self.S.root, "records", rid + ".json")
        rec = json.load(open(p))
        rec["check"]["argv"] = [PY, "-c", "pass"]   # an edit after registration
        open(p, "wb").write(F.canon(rec))
        _, r = self.S.judge_yes(rid)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("MOVED", r["why"])

    def test_a_patch_that_does_not_apply_is_void(self):
        m = {"argv": [PY, "test_ok.py"], "cwd": self.tree, "patch": {"path": "subject.py", "old": "not in the file", "new": "x"}}
        _, r = self.S.judge_yes(self.S.register("add works", self.check("test_ok.py"), m))
        self.assertEqual(r["verdict"], "void")
        self.assertIn("0 times", r["why"])

    def test_a_patch_cannot_leave_the_tree(self):
        m = {"argv": [PY, "test_ok.py"], "cwd": self.tree, "patch": {"path": "../outside.py", "old": "a", "new": "b"}}
        _, r = self.S.judge_yes(self.S.register("add works", self.check("test_ok.py"), m))
        self.assertEqual(r["verdict"], "void")

    def test_a_timeout_is_void(self):
        slow = {"argv": [PY, "-c", "import time; time.sleep(5)"], "timeout_s": 0.5}
        _, r = self.S.judge_yes(self.S.register("x", slow, bad()))
        self.assertEqual(r["verdict"], "void")
        self.assertIn("timed out", r["why"])


class Approval(Base):
    """R1: only approved, only the record's argv, never under a non-interactive caller; nothing runs on a refusal."""
    def marker_record(self):
        m = os.path.join(self.tmp.name, "ran")
        c = {"argv": [PY, "-c", f"open({m!r}, 'a').write('x')"]}
        return self.S.register("c", c, bad()), m

    def test_no_approver_runs_nothing(self):
        rid, m = self.marker_record()
        _, r = self.S.judge(rid)
        self.assertEqual((r["verdict"], r["why"]), ("void", "not run: no approver"))
        self.assertFalse(os.path.exists(m))

    def test_non_interactive_runs_nothing_even_if_approved(self):
        rid, m = self.marker_record()
        _, r = self.S.judge(rid, approve=YES, interactive=False)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("non-interactive", r["why"])
        self.assertFalse(os.path.exists(m))

    def test_a_refused_mutant_or_check_runs_nothing(self):
        for deny in ("mutant", "check"):
            rid, m = self.marker_record()
            _, r = self.S.judge(rid, approve=lambda k, *a: k != deny)
            self.assertIn(f"the {deny} was not approved", r["why"])
            self.assertFalse(os.path.exists(m))

    def test_the_approver_sees_exactly_the_records_argv(self):
        seen = []
        rid = self.S.register("add works", self.check("test_ok.py"), self.mutant("test_ok.py"))
        _, r = self.S.judge(rid, approve=lambda k, argv, cwd, h: seen.append((k, argv, cwd, h)) or True)
        self.assertEqual(r["verdict"], "proved")
        self.assertEqual(seen, [("mutant", [PY, "test_ok.py"], self.tree, rid), ("check", [PY, "test_ok.py"], self.tree, rid)])

    def test_an_approver_that_returns_truthy_non_bool_is_a_no(self):
        rid, m = self.marker_record()
        _, r = self.S.judge(rid, approve=lambda *a: "yes")
        self.assertIn("not approved", r["why"])


class Adapter(Base):
    def test_pre_verify_nudges_open_claims_and_goes_quiet_when_done(self):
        from falsify import adapter as A
        hook = A.make_pre_verify(self.S)
        self.assertIsNone(hook(attempt=0))                          # nothing registered: finish
        rid = self.S.register("add works", self.check("test_ok.py"), self.mutant("test_ok.py"))
        out = hook(session_id="s", coding=True, attempt=0, final_response="done", changed_paths=["subject.py"])
        self.assertEqual(out["action"], "continue")
        self.assertIn("unjudged", out["message"])
        self.assertIsNone(hook(attempt=1))                           # self-throttled
        h, _ = self.S.judge_yes(rid)
        self.assertIn("proved", hook(attempt=0)["message"])         # proved but not yet DONE: still open
        self.S.done("add works", h)
        self.assertIsNone(hook(attempt=0))

    def test_a_void_claim_is_named_in_the_nudge(self):
        from falsify import adapter as A
        self.S.judge(self.S.register("add works", self.check("test_ok.py"), self.mutant("test_ok.py")))   # no approver
        self.assertIn("void (not run: no approver)", A.nudge_message(self.S))

    def test_make_approver_passes_the_hosts_answer(self):
        from falsify import adapter as A
        asked = []
        ap = A.make_approver(lambda p: asked.append(p) or True)
        self.assertTrue(ap("check", ["x"], "/t", "a" * 64))
        self.assertIn("['x']", asked[0])
        self.assertFalse(A.make_approver(lambda p: None)("check", ["x"], None, "a" * 64))


class Done(Base):
    def proved(self, claim="add works"):
        return self.S.judge_yes(self.S.register(claim, self.check("test_ok.py"), self.mutant("test_ok.py")))[0]

    def test_done_on_a_proved_result(self):
        self.assertIn('"claim":"add works"', self.S.done("add works", self.proved()))
        self.assertEqual(self.S.audit(), [])

    def test_done_refuses_void_disproved_missing_and_wrong_claim(self):
        void = self.S.judge_yes(self.S.register("add works", {"argv": [PY, "-c", "pass"], "cwd": self.tree},
                                            {"argv": [PY, "-c", "pass"], "cwd": self.tree, "patch": {"path": "subject.py", "old": "a + b", "new": "0"}}))[0]
        for claim, rid, word in (("add works", void, "void"), ("add works", "0" * 64, "does not exist"),
                                 ("something else", self.proved(), "evidence for"), ("add works", "nothex", "not a sha256")):
            with self.assertRaises(F.Refused) as e:
                self.S.done(claim, rid)
            self.assertIn(word, str(e.exception))

    def test_audit_demotes_a_done_whose_evidence_moved(self):
        rid = self.proved()
        self.S.done("add works", rid)
        p = os.path.join(self.S.root, "results", rid + ".json")
        res = json.load(open(p))
        res["why"] = "edited"
        open(p, "wb").write(F.canon(res))
        bad = self.S.audit()
        self.assertEqual(len(bad), 1)
        self.assertIn("MOVED", bad[0][1])


class Cli(Base):
    def test_register_judge_done_audit(self):
        spec = os.path.join(self.tmp.name, "spec.json")
        json.dump({"claim": "add works", "check": self.check("test_ok.py"), "mutant": self.mutant("test_ok.py")}, open(spec, "w"))
        st = ["--store", self.S.root]
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(F.main(st + ["register", spec]), 0)
        rid = out.getvalue().strip()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(F.main(st + ["judge", rid, "--approve"]), 0)
        res = json.loads(out.getvalue())["result"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(F.main(st + ["done", "add works", res]), 0)
            self.assertEqual(F.main(st + ["audit"]), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(F.main(st + ["done", "add works", "0" * 64]), 3)


if __name__ == "__main__":
    unittest.main()
