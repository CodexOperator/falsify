"""Tests for falsify.core and falsify.adapter (v2: the mutant is derived from the agent's own change).
stdlib unittest; every subject is a tiny git repo in a temp dir, every check a python script in it."""
import contextlib, io, json, os, subprocess, sys, tempfile, unittest, unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from falsify import adapter as A  # noqa: E402
from falsify import core as F  # noqa: E402
from falsify import draft as D  # noqa: E402

PY = sys.executable
YES = lambda *a: True
BUGGY = "def add(a, b):\n    return 0\n" + "\n# padding, so the two functions are separate hunks\n" * 6 + "\ndef sub(a, b):\n    return 0\n"
FIXED = BUGGY.replace("def add(a, b):\n    return 0", "def add(a, b):\n    return a + b")
BOTH = FIXED.replace("def sub(a, b):\n    return 0", "def sub(a, b):\n    return a - b")
TEST_ADD = "from subject import add\nassert add(2, 3) == 5\n"
TAUT = "assert True\n"


def sh(cwd, *a):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.S = F.Store(os.path.join(self.tmp.name, "store"))
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        self.write("subject.py", BUGGY)
        self.write("test_add.py", TEST_ADD)
        self.write("test_taut.py", TAUT)
        sh(self.repo, "git", "init", "-q")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text)

    def reg(self, script="test_add.py", claim="add works", blind=0, **chk):
        return self.S.register(claim, self.repo, {"argv": [PY, script], **chk}, blind=blind)

    def judge(self, rid, **kw):
        return self.S.judge(rid, approve=YES, **kw)


class Register(Base):
    def test_the_record_pins_the_base_and_is_content_addressed(self):
        rid = self.reg()
        rec, why = self.S.load("records", rid)
        self.assertIsNone(why)
        head = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual((rec["base"], rec["check"]["cwd"]), (head, "."))
        p = os.path.join(self.S.root, "records", rid + ".json")
        with open(p, "wb") as f:
            f.write(F.canon({**rec, "claim": "edited"}))            # an edit after registration
        self.assertIn("MOVED", self.S.load("records", rid)[1])
        with self.assertRaises(F.Refused):
            self.S._write_once("records", rec)                     # and the original bytes can't be re-written over it

    def test_bad_specs_are_refused(self):
        for chk in ({"argv": []}, {"argv": "x"}, {"argv": ["x"], "cwd": "/etc"}, {"argv": ["x"], "cwd": "../up"}):
            with self.assertRaises(F.Refused):
                self.S.register("c", self.repo, chk)
        with self.assertRaises(F.Refused):
            self.S.register(" ", self.repo, {"argv": ["x"]})
        with self.assertRaises(F.Refused):
            self.S.register("c", self.tmp.name, {"argv": ["x"]})   # not a git repo
        with self.assertRaises(F.Refused):
            self.S.register("c", self.repo, {"argv": ["x"]}, base="no-such-ref")


    def test_a_bare_list_is_the_argv(self):
        self.assertEqual(self.S.register("c", self.repo, [PY, "test_add.py"]), self.S.register("c", self.repo, {"argv": [PY, "test_add.py"]}))
        self.assertEqual(self.S.load("records", self.S.register("c", self.repo, ["x"], sanity=["y"]))[0]["sanity"]["argv"], ["y"])

    def test_every_schema_error_shows_the_expected_shape_and_an_example(self):
        bad = [dict(check=c) for c in ([], [1], {"argv": []}, {"argv": "x"}, {"argv": ["x"], "cwd": "/etc"}, {"argv": ["x"], "cwd": 3},
                                       {"argv": ["x"], "kill_rc": [0]}, {"argv": ["x"], "expect_rc": "zero"}, {"argv": ["x"], "timeout_s": [1]}, "pytest")]
        bad += [dict(claim=" "), dict(claim=None), dict(repo=None), dict(repo=self.tmp.name), dict(base="no-such-ref"),
                dict(sanity={"argv": []}), dict(keep="tests/*"), dict(keep=["/abs"])]
        for b in bad:
            a = {"claim": "c", "repo": self.repo, "check": ["x"], **b}
            with self.subTest(b), self.assertRaises(F.Refused) as e:
                self.S.register(a.pop("claim"), a.pop("repo"), a.pop("check"), **a)
            self.assertRegex(str(e.exception), r"; expected .+; e\.g\. \S", str(e.exception))

class Judge(Base):
    def test_a_check_that_needs_the_change_is_proved_with_its_hunk_coverage(self):
        rid = self.reg()
        self.write("subject.py", FIXED)
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "proved", r["why"])
        self.assertEqual((r["evidence"]["hunks_killed"], r["evidence"]["hunks_total"]), (1, 1))
        self.assertIn("covers 1 of 1", r["why"])
        with open(os.path.join(self.repo, "subject.py")) as f:
            self.assertEqual(f.read(), FIXED)                      # the real tree is untouched

    def test_a_check_that_misses_a_hunk_says_k_of_h(self):
        rid = self.reg()
        self.write("subject.py", BOTH)
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "proved")
        self.assertEqual(r["evidence"]["hunks_total"], 2)
        self.assertEqual(r["evidence"]["hunks_killed"], 1)
        self.assertEqual([h["outcome"] for h in r["evidence"]["hunks"]], ["killed", "your check passes without your change"])

    def test_PLANTED_tautology_reads_void(self):
        """The planted tautological check passes with or without the change -> VOID."""
        rid = self.reg("test_taut.py")
        self.write("subject.py", FIXED)
        _, r = self.judge(rid)
        self.assertEqual((r["verdict"], r["why"]), ("void", "your check passes without your change"))

    def test_a_check_that_fails_on_the_final_tree_is_disproved(self):
        rid = self.reg()
        self.write("subject.py", BUGGY.replace("return 0", "return 1", 1))
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "disproved")

    def test_no_change_is_void_no_mutant(self):
        _, r = self.judge(self.reg())
        self.assertEqual(r["verdict"], "void")
        self.assertIn("no mutant", r["why"])

    def test_untracked_and_committed_changes_both_count_and_the_users_index_is_untouched(self):
        rid = self.S.register("X is 7", self.repo, {"argv": [PY, "test_new.py"]}, keep=["test_new.py"])   # the check's own new test is kept
        self.write("newmod.py", "X = 7\n")
        self.write("test_new.py", "from newmod import X\nassert X == 7\n")
        sh(self.repo, "git", "add", "test_new.py")
        before = subprocess.run(["git", "-C", self.repo, "status", "--porcelain"], capture_output=True, text=True).stdout
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "proved", r["why"])
        self.assertEqual([h["path"] for h in r["evidence"]["hunks"]], ["newmod.py"])   # untracked, still the change
        after = subprocess.run(["git", "-C", self.repo, "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(before, after)

    def test_bytecode_left_by_a_test_run_is_never_part_of_the_change(self):
        rid = self.reg()
        self.write("subject.py", FIXED)
        subprocess.run([PY, "test_add.py"], cwd=self.repo, check=True)   # the agent ran its test in the repo
        os.makedirs(os.path.join(self.repo, "__pycache__"), exist_ok=True)
        with open(os.path.join(self.repo, "__pycache__", "stray.cpython-313.pyc"), "wb") as f:
            f.write(b"\x00bytecode")
        _, r = self.judge(rid)
        self.assertEqual((r["verdict"], r["evidence"]["hunks_total"]), ("proved", 1))
        self.assertTrue([h["path"] for h in r["evidence"]["hunks"]] == ["subject.py"])

    def test_a_timeout_on_the_final_tree_is_void(self):
        self.write("test_slow.py", "import time\ntime.sleep(5)\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "slow")
        rid = self.reg("test_slow.py", timeout_s=0.5)
        self.write("subject.py", FIXED)
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("timed out", r["why"])

    def test_a_moved_record_reads_void(self):
        rid = self.reg()
        p = os.path.join(self.S.root, "records", rid + ".json")
        rec = json.loads(F.read_bytes(p))
        with open(p, "wb") as f:
            f.write(F.canon({**rec, "check": {**rec["check"], "argv": [PY, "test_taut.py"]}}))
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("MOVED", r["why"])

    def test_hunks_split_per_hunk_and_keep_binary_and_new_files_whole(self):
        d = (b"diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n@@ -9 +9 @@\n-c\n+d\n"
             b"diff --git a/y.bin b/y.bin\nindex 1..2\nGIT binary patch\nliteral 1\nIc\n")
        u = F.hunks(d)
        self.assertEqual([p for p, _ in u], ["x", "x", "y.bin"])
        self.assertTrue(all(b.startswith(b"diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n@@") for _, b in u[:2]))


class KillQuality(Base):
    """Step 2: only an rc in the pinned kill_rc is a kill; a sanity failure or a dead run is VOID 'unrevertable'."""
    def commit(self, msg):
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", msg)

    def test_an_rc_outside_kill_rc_is_unrevertable_not_a_kill(self):
        self.write("test_rc3.py", "import sys\nfrom subject import add\nsys.exit(0 if add(2, 3) == 5 else 3)\n")
        self.commit("rc3")
        bad = self.reg("test_rc3.py")                       # default kill_rc [1]: rc 3 is not a kill
        good = self.reg("test_rc3.py", claim="rc3 pinned", kill_rc=[3])
        self.write("subject.py", FIXED)
        _, r = self.judge(bad)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("unrevertable: the check died with rc 3, not a kill", r["why"])
        _, r = self.judge(good)
        self.assertEqual(r["verdict"], "proved", r["why"])

    def test_kill_rc_must_exclude_expect_rc(self):
        for k in ([0], []):
            with self.assertRaises(F.Refused):
                self.reg(kill_rc=k)

    def test_a_reverted_test_file_is_not_a_kill_unless_kept(self):
        """the agent adds the fix AND its test: reverting the change deletes the test itself (python: rc 2, file
        missing). Without keep that is VOID unrevertable; keep=['tests/**'] leaves the test in place -> proved."""
        os.makedirs(os.path.join(self.repo, "tests"))
        plain = self.S.register("add works", self.repo, {"argv": [PY, "tests/t_add.py"]})
        kept = self.S.register("add works (kept)", self.repo, {"argv": [PY, "tests/t_add.py"]}, keep=["tests/**"])
        self.write("subject.py", FIXED)
        self.write("tests/t_add.py", "import sys; sys.path.insert(0, '.')\nfrom subject import add\nassert add(2, 3) == 5\n")
        _, r = self.judge(plain)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("unrevertable", r["why"])
        _, r = self.judge(kept)
        self.assertEqual(r["verdict"], "proved", r["why"])
        self.assertTrue(r["evidence"]["kept_paths_changed"])
        self.assertEqual([h["path"] for h in r["evidence"]["hunks"]], ["subject.py"])

    def test_a_change_only_in_kept_paths_is_no_mutant(self):
        rid = self.S.register("c", self.repo, {"argv": [PY, "test_add.py"]}, keep=["test_*.py"])
        self.write("test_add.py", TEST_ADD + "# more\n")
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("outside the kept paths", r["why"])

    def test_a_hunk_that_breaks_the_import_is_unrevertable_by_sanity_not_a_kill(self):
        """the change adds newmod.py and subject.py imports it: reverting newmod.py ALONE breaks the import, which
        the sanity argv catches -> that hunk is unrevertable, never counted as killed."""
        san = {"argv": [PY, "-c", "import subject"]}
        rid = self.S.register("add works", self.repo, {"argv": [PY, "test_add.py"]}, sanity=san)
        self.write("newmod.py", "def plus(a, b):\n    return a + b\n")
        self.write("subject.py", "from newmod import plus\n" + FIXED.replace("return a + b", "return plus(a, b)"))
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "proved", r["why"])
        out = {h["path"]: h["outcome"] for h in r["evidence"]["hunks"] if h["path"] == "newmod.py"}
        self.assertEqual(out, {"newmod.py": "unrevertable: the reverted tree fails the sanity check"})
        self.assertGreaterEqual(r["evidence"]["hunks_unrevertable"], 1)
        self.assertIn("unrevertable alone", r["why"])

    def test_a_sanity_that_fails_on_the_final_tree_is_void(self):
        rid = self.S.register("c", self.repo, {"argv": [PY, "test_add.py"]}, sanity={"argv": [PY, "-c", "import nosuchmod"]})
        self.write("subject.py", FIXED)
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("sanity check fails on your final tree", r["why"])

    def test_the_sanity_argv_is_approved_too(self):
        seen = []
        rid = self.S.register("c", self.repo, {"argv": [PY, "test_add.py"]}, sanity={"argv": [PY, "-c", "import subject"]})
        self.write("subject.py", FIXED)
        _, r = self.S.judge(rid, approve=lambda k, argv, *a: seen.append(k) or k != "sanity")
        self.assertEqual(seen, ["check", "sanity"])
        self.assertIn("the sanity was not approved", r["why"])


class Ledger(Base):
    """Step 3: every register and judge is a hash-chained line; done reports the denominator."""
    def test_retry_until_green_is_counted_and_flagged(self):
        a = self.reg()
        self.write("subject.py", BUGGY.replace("return 0", "return 1", 1))
        self.judge(a)                                               # disproved
        b = self.reg("test_taut.py")
        self.judge(b)                                               # void: passes without the change
        c = self.reg()
        self.write("subject.py", FIXED)
        h, r = self.judge(c)
        self.assertEqual(r["verdict"], "proved")
        out = self.S.done("add works", h)
        self.assertIn("this claim: 3 registration(s), verdicts 1 proved / 1 disproved / 1 void", out)
        self.assertIn("session total 3 registration(s), 3 judgement(s)", out)
        self.assertIn("RE-REGISTERED after a disproved or VOID verdict", out)

    def test_a_clean_first_try_is_not_flagged(self):
        rid = self.reg()
        self.write("subject.py", FIXED)
        out = self.S.done("add works", self.judge(rid)[0])
        self.assertIn("1 registration(s), verdicts 1 proved / 0 disproved / 0 void", out)
        self.assertNotIn("RE-REGISTERED", out)

    def test_an_edited_or_dropped_ledger_line_breaks_the_chain_and_audit_says_so(self):
        rid = self.reg()
        self.judge(rid)
        self.judge(rid)
        p = os.path.join(self.S.root, "ledger.jsonl")
        lines = F.read_bytes(p).splitlines()
        self.assertEqual(self.S.ledger()[1], [])
        for broken in ([lines[0].replace(b"add works", b"add WORKS")] + lines[1:], [lines[0]] + lines[2:], lines[1:]):
            with open(p, "wb") as f:
                f.write(b"\n".join(broken) + b"\n")
            bad = self.S.audit()
            self.assertTrue(any(k == "ledger.jsonl" and "breaks the chain" in w for k, w in bad), bad)
        with open(p, "wb") as f:
            f.write(b"\n".join(lines) + b"\n")
        self.assertEqual(self.S.audit(), [])

    def test_a_judge_that_did_not_run_is_still_logged(self):
        rid = self.reg()
        self.S.judge(rid)                                           # no approver
        es, _ = self.S.ledger()
        self.assertEqual([(e["event"], e.get("verdict")) for e in es], [("register", None), ("judge", "void")])


class Approval(Base):
    """R1: only approved, only the record's argv, never under a non-interactive caller; nothing runs on a refusal."""
    def marker(self):
        m = os.path.join(self.tmp.name, "ran")
        self.write("test_mark.py", f"open({m!r}, 'a').write('x')\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "m")
        rid = self.reg("test_mark.py")
        self.write("subject.py", FIXED)
        return rid, m

    def test_no_approver_runs_nothing(self):
        rid, m = self.marker()
        _, r = self.S.judge(rid)
        self.assertEqual((r["verdict"], r["why"]), ("void", "not run: no approver"))
        self.assertFalse(os.path.exists(m))

    def test_non_interactive_runs_nothing_even_if_approved(self):
        rid, m = self.marker()
        _, r = self.S.judge(rid, approve=YES, interactive=False)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("not run: a non-interactive caller", r["why"])
        self.assertFalse(os.path.exists(m))

    def test_a_refused_or_non_bool_answer_runs_nothing(self):
        rid, m = self.marker()
        for ans in (False, "yes", None):
            _, r = self.S.judge(rid, approve=lambda *a: ans)
            self.assertIn("not approved", r["why"])
            self.assertFalse(os.path.exists(m))

    def test_the_approver_sees_exactly_the_records_argv_once(self):
        seen = []
        rid = self.reg()
        self.write("subject.py", FIXED)
        _, r = self.S.judge(rid, approve=lambda k, argv, cwd, h: seen.append((k, argv, cwd, h)) or True)
        self.assertEqual(r["verdict"], "proved")
        self.assertEqual(seen, [("check", [PY, "test_add.py"], os.path.join(os.path.realpath(self.repo), "."), rid)])


class Adapter(Base):
    def test_pre_verify_nudges_open_claims_and_goes_quiet_when_done(self):
        hook = A.make_pre_verify(self.S)
        self.assertIsNone(hook(attempt=0))
        rid = self.reg()
        out = hook(session_id="s", coding=True, attempt=0, final_response="done", changed_paths=["subject.py"])
        self.assertEqual(out["action"], "continue")
        self.assertIn("unjudged", out["message"])
        self.assertIsNone(hook(attempt=1))
        self.write("subject.py", FIXED)
        h, _ = self.judge(rid)
        self.assertIn("proved", hook(attempt=0)["message"])
        self.S.done("add works", h)
        self.assertIsNone(hook(attempt=0))

    def test_a_change_no_open_claim_covers_is_nudged(self):
        hook = A.make_pre_verify(self.S)
        out = hook(attempt=0, changed_paths=[os.path.join(self.repo, "subject.py")])
        self.assertIsNotNone(out, "a turn that changed files with no claim must be nudged")
        self.assertEqual(out["action"], "continue")
        self.assertIn("with no falsify claim: register a check that fails without your change", out["message"])
        self.assertIn("subject.py", out["message"])
        self.assertIsNone(hook(attempt=1, changed_paths=[os.path.join(self.repo, "subject.py")]))

    def test_an_open_claim_covers_its_repo_and_a_done_one_does_not(self):
        rid = self.reg()
        inside, outside = os.path.join(self.repo, "subject.py"), os.path.join(self.tmp.name, "elsewhere.py")
        self.assertEqual(A.uncovered(self.S, [inside, outside]), [outside])
        self.assertEqual(A.uncovered(self.S, ["subject.py"], cwd=self.repo), [])
        self.assertNotIn("no falsify claim", A.nudge_message(self.S, changed_paths=[inside]))
        self.write("subject.py", FIXED)
        self.S.done("add works", self.judge(rid)[0])
        self.assertIn("you changed " + inside + " with no falsify claim", A.nudge_message(self.S, changed_paths=[inside]))

    def test_a_proved_not_done_claim_is_nudged_with_its_exact_done_call_and_listed_first(self):
        open_rid = self.reg("test_taut.py", claim="taut")   # unjudged, registered first
        rid = self.reg()
        self.write("subject.py", FIXED)
        h, _ = self.judge(rid)
        rows = A.open_rows(self.S)
        self.assertEqual([(r[0], r[2], r[4]) for r in rows], [(rid, "proved", h), (open_rid, "unjudged", None)])
        msg = A.nudge_message(self.S)
        call = 'now call falsify_done(claim="add works", result="%s") to close this claim' % h
        self.assertEqual(A.next_call("add works", h), call)
        self.assertIn(call, msg)
        self.assertLess(msg.index("add works"), msg.index("taut"))
        self.assertEqual(msg.count("falsify_done("), 1)   # only the proved claim gets the call
        self.assertIn(rid, [r[0] for r in self.S.open_claims()])   # proved is NOT done: nothing auto-closed
        self.S.done("add works", h)
        self.assertNotIn("falsify_done(", A.nudge_message(self.S))

    def test_a_void_claim_is_named_in_the_nudge(self):
        self.S.judge(self.reg())
        self.assertIn("void (not run: no approver)", A.nudge_message(self.S))

    def test_make_approver_passes_the_hosts_answer(self):
        asked = []
        self.assertTrue(A.make_approver(lambda p: asked.append(p) or True)("check", ["x"], "/t", "a" * 64))
        self.assertIn("['x']", asked[0])
        self.assertFalse(A.make_approver(lambda p: None)("check", ["x"], None, "a" * 64))


SUB = {"path": "subject.py", "old": "return a + b", "new": "return a - b"}
MUL = {"path": "subject.py", "old": "return a + b", "new": "return a * b"}


class Blind(Base):
    """step 4: a claim with NO diff is judged against ONE blind mutant, drafted from the claim's words at register and
    drawn after the run by a committed seed."""
    def setUp(self):
        super().setUp()
        self.write("subject.py", FIXED)   # the claim is that add ALREADY works: the agent changes nothing
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qam", "fixed")
        self.seen = []
        self.S.drafter = lambda claim, files: self.seen.append((claim, files)) or [SUB, MUL, {"path": "subject.py", "old": "nope", "new": "x"}, SUB]

    def test_a_no_diff_claim_is_proved_against_its_drawn_blind_mutant(self):
        rid = self.reg(blind=4)
        bl = self.S.load("records", rid)[0]["blind"]
        self.assertEqual((bl["asked"], bl["drafted"], bl["mutants"]), (4, 4, [SUB, MUL]))   # invalid and duplicate dropped
        h, r = self.judge(rid)
        self.assertEqual(r["verdict"], "proved", r["why"])
        ev = r["evidence"]
        self.assertEqual(F.sha(ev["blind_seed"].encode()), bl["seed_commit"])
        self.assertEqual(ev["blind_drawn"], F.draw_index(ev["blind_seed"], rid, 2))
        self.assertIn(f"kills blind mutant {ev['blind_drawn']} of 2", r["why"])
        self.assertIn("DONE", self.S.done("add works", h) + "DONE")

    def test_a_tautological_check_reads_void_on_the_drawn_mutant(self):
        _, r = self.judge(self.reg("test_taut.py", blind=4))
        self.assertEqual(r["verdict"], "void")
        self.assertEqual(r["why"], "your check passes on the drawn blind mutant")

    def test_the_drafter_reads_the_claim_and_base_files_never_the_check(self):
        self.reg("test_add.py", claim="add works", blind=2, expect_rc=0)
        ((claim, files),) = self.seen
        self.assertEqual(claim, "add works")
        self.assertEqual(files["subject.py"], FIXED)
        self.assertNotIn(PY, json.dumps(files))

    def test_the_drafter_never_sees_the_files_the_check_sanity_or_keep_name(self):
        os.makedirs(os.path.join(self.repo, "tests"))
        self.write("tests/test_more.py", TEST_ADD)
        self.write("helper.py", "X = 1\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "more")
        rid = self.S.register("add works", self.repo, [PY, "test_add.py::test_x"], sanity=[PY, "helper.py"], keep=["tests/**"], blind=2)
        ((_, files),) = self.seen
        self.assertEqual(sorted(files), ["subject.py", "test_taut.py"])
        self.assertEqual(self.S.load("records", rid)[0]["blind"]["withheld"], ["helper.py", "test_add.py", "tests/test_more.py"])
        self.S.register("all", self.repo, [PY, "-m", "pytest", "."], blind=2)
        self.assertEqual(self.seen[-1][1], {})   # "." names the whole tree: nothing is shown

    def discovery_repo(self, extra):
        for name, text in {"tests/test_cart.py": TEST_ADD, "conftest.py": "", "shop_test.py": TEST_ADD, **extra}.items():
            os.makedirs(os.path.join(self.repo, os.path.dirname(name)), exist_ok=True)
            self.write(name, text)
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "tests")

    def test_a_pathless_pytest_check_withholds_pytests_discovery_set(self):
        # plasma's red case: 'python -m pytest -q' over a repo with tests/test_cart.py must not show that file
        self.discovery_repo({"checks/probe.py": "", "check_x.py": "", "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["checks"]\n',
                             "pytest.ini": "[pytest]\npython_files = check_*.py test_*.py\n"})
        rid = self.S.register("add works", self.repo, [PY, "-m", "pytest", "-q"], blind=2)
        files = self.seen[-1][1]
        self.assertNotIn("tests/test_cart.py", files)
        withheld = self.S.load("records", rid)[0]["blind"]["withheld"]
        self.assertEqual(withheld, ["check_x.py", "checks/probe.py", "conftest.py", "shop_test.py", "test_add.py", "test_taut.py", "tests/test_cart.py"])
        self.assertIn("subject.py", files)

    def test_a_pathless_unittest_or_test_script_check_withholds_test_star(self):
        self.discovery_repo({"run_tests": "#!/bin/sh\n"})
        self.S.register("add works", self.repo, [PY, "-m", "unittest"], blind=2)
        self.assertEqual(sorted(self.seen[-1][1]), ["conftest.py", "run_tests", "shop_test.py", "subject.py"])
        self.S.register("add works", self.repo, ["./run_tests"], blind=2)   # the script itself + both default sets
        self.assertEqual(sorted(self.seen[-1][1]), ["subject.py"])

    def test_the_size_cap_cut_list_is_recorded(self):
        files, withheld, cut = F.base_files(self.repo, "HEAD", max_total=len(FIXED) + 1)
        self.assertEqual((sorted(files), withheld), (["subject.py"], []))
        self.assertEqual(cut, ["test_add.py", "test_taut.py"])
        self.assertEqual(self.S.load("records", self.reg(blind=2))[0]["blind"]["cut"], [])

    def test_a_moved_seed_voids_the_draw(self):
        rid = self.reg(blind=2)
        bl = self.S.load("records", rid)[0]["blind"]
        with open(os.path.join(self.S.root, "seeds", bl["seed_commit"]), "w") as f:
            f.write("0" * 64)
        _, r = self.judge(rid)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("seed is missing or does not match", r["why"])

    def test_no_drafter_refuses_and_a_failing_drafter_drafts_nothing(self):
        self.S.drafter = None
        with self.assertRaises(F.Refused):
            self.reg(blind=2)
        self.S.drafter = lambda c, f: 1 / 0
        rid = self.reg(blind=2)
        self.assertIn("ZeroDivisionError", self.S.load("records", rid)[0]["blind_error"])
        _, r = self.judge(rid)
        self.assertIn("no blind mutant survived drafting (0 drafted)", r["why"])

    def test_a_diff_still_uses_the_derived_mutant(self):
        rid = self.reg(blind=2)
        self.write("subject.py", BOTH)
        _, r = self.judge(rid)
        self.assertEqual(r["why"], "your check passes without your change")   # the REVERT judged it, not a blind draw
        self.assertIn("reverted_run", r["evidence"])
        self.assertNotIn("blind_drawn", r["evidence"])

    def test_apply_blind_reports_why_it_does_not_apply(self):
        d = os.path.join(self.tmp.name, "copy")
        F.copy_tree(self.repo, d)
        self.assertIsNone(F.apply_blind(d, SUB))
        self.assertIn("occurs 0 times", F.apply_blind(d, SUB))
        self.assertIn("not a file", F.apply_blind(d, {"path": "../x", "old": "a", "new": "b"}))



class CommitFirst(Base):
    """leak 2: an agent that commits its change and then registers at HEAD has no diff; the judge widens to this
    session's earliest registered base that is an ancestor, and derives the mutant from that."""
    def commit(self, msg):
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qam", msg)

    def test_a_commit_first_dodge_is_judged_from_the_earlier_base(self):
        first = self.reg()                      # registered at the BUGGY base
        self.write("subject.py", FIXED)
        self.commit("the fix, committed before re-registering")
        dodge = self.reg(claim="add works")     # base HEAD = the fix: no diff
        h, r = self.judge(dodge)
        self.assertEqual(r["verdict"], "proved", r["why"])
        self.assertIn("base includes this session's commits", r["why"])
        self.assertEqual(r["evidence"]["base_widened_to"]["record"], first)
        _, r = self.judge(self.reg("test_taut.py", claim="taut"))
        self.assertEqual(r["verdict"], "void")
        self.assertTrue(r["why"].startswith("your check passes without your change; base includes"), r["why"])

    def test_widening_never_crosses_into_an_older_session(self):
        self.reg()                                   # session A registers at the BUGGY base
        self.write("subject.py", FIXED)
        self.commit("someone's fix, before this session")
        later = F.Store(self.S.root)                 # the same store, a NEW session
        rid = later.register("add works", self.repo, {"argv": [PY, "test_add.py"]})
        _, r = later.judge(rid, approve=YES)
        self.assertEqual(r["verdict"], "void")
        self.assertIn("no mutant", r["why"])
        self.assertNotIn("base_widened_to", r["evidence"])
        same = F.Store(self.S.root, session=self.S.session)   # the SAME session, a new process: widens
        _, r = same.judge(same.register("add works", self.repo, {"argv": [PY, "test_add.py"]}), approve=YES)
        self.assertEqual(r["verdict"], "proved", r["why"])

    def test_the_session_is_in_the_hash_chained_ledger(self):
        rid = self.S.register("c", self.repo, [PY, "test_add.py"], session="hermes-s1")
        (e,) = [x for x in self.S.ledger()[0] if x.get("record") == rid]
        self.assertEqual(e["session"], "hermes-s1")

    def test_with_no_earlier_ancestor_there_is_still_no_mutant(self):
        self.write("subject.py", FIXED)
        self.commit("fix")
        _, r = self.judge(self.reg())
        self.assertEqual(r["verdict"], "void")
        self.assertIn("no mutant", r["why"])
        self.assertNotIn("base_widened_to", r["evidence"])


SWAP_BUG = "def f():\n    x = 1\n    x = 2\n    return x\n"      # returns 2
SWAP_FIX = "def f():\n    x = 2\n    x = 1\n    return x\n"      # a moved line: same size, returns 1


class Bytecode(Base):
    """M-GOLD's bug (g5.53 a08): bytecode copied into a mutant tree can outlive the revert. Here the FIXED code's .pyc
    is unchecked-hash (PEP 552: python loads it without looking at the source), the deterministic form of a copied
    .pyc whose source mtime + size still match after a same-second, same-size revert."""
    def setUp(self):
        super().setUp()
        self.write("swapped.py", SWAP_BUG)
        self.write("test_f.py", "from swapped import f\nassert f() == 1\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "swap")
        self.rid = self.reg("test_f.py", claim="f returns 1")
        self.write("swapped.py", SWAP_FIX)
        self.assertEqual(len(SWAP_BUG), len(SWAP_FIX))
        sh(self.repo, PY, "-m", "py_compile", "swapped.py")   # writes __pycache__ beside the source
        import py_compile
        py_compile.compile(os.path.join(self.repo, "swapped.py"), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)

    def test_a_same_size_moved_line_fix_with_an_honest_check_reads_proved(self):
        _, r = self.judge(self.rid)
        self.assertEqual(r["verdict"], "proved", r["why"])

    def test_copying_bytecode_into_the_mutant_tree_would_read_void(self):
        old = lambda repo, dst: F.shutil.copytree(repo, dst, symlinks=True, ignore=F.shutil.ignore_patterns(".git"))
        with unittest.mock.patch.object(F, "copy_tree", old):
            _, r = self.judge(self.rid)
        self.assertEqual((r["verdict"], r["why"]), ("void", "your check passes without your change"))

    def test_every_check_runs_without_writing_bytecode(self):
        r = F.run_check(F._norm_check([PY, "-c", "import os, sys; sys.exit(os.environ.get('PYTHONDONTWRITEBYTECODE') != '1')"]), self.repo)
        self.assertTrue(r["passed"], r)

class Draft(unittest.TestCase):
    def test_parse_skips_think_and_fences_and_takes_the_last_mutants_object(self):
        text = '<think>{"mutants": [1]}</think>Sure.\n```json\n{"mutants": [{"path": "a", "old": "x", "new": "y"}]}\n```'
        self.assertEqual(D.parse(text), [{"path": "a", "old": "x", "new": "y"}])
        self.assertEqual(D.parse("no json here"), [])

    def test_the_drafter_posts_the_claim_and_files_only(self):
        sent = []
        post = lambda u, payload, h, t: sent.append((u, payload)) or {"choices": [{"message": {"content": '{"mutants": [{"path": "f.py", "old": "1", "new": "2"}]}'}}]}
        dr = D.make_drafter("http://127.0.0.1:9/v1/", "m", k=3, post=post)
        self.assertEqual(dr("X is 1", {"f.py": "X = 1\n"}), [{"path": "f.py", "old": "1", "new": "2"}])
        ((u, payload),) = sent
        self.assertEqual(u, "http://127.0.0.1:9/v1/chat/completions")
        msg = payload["messages"][0]["content"]
        self.assertIn("CLAIM: X is 1", msg)
        self.assertIn("=== f.py ===\nX = 1", msg)
        self.assertIn("Write 3 MUTANTS", msg)

    def test_the_anthropic_drafter_in_claim_view_sends_the_claim_only(self):
        sent = []
        post = lambda u, payload, h, t: sent.append((u, payload, h)) or {"content": [{"type": "thinking", "thinking": ""},
                                                                                     {"type": "text", "text": '{"mutants": []}'}]}
        dr = D.make_drafter("http://door:1", "claude-haiku-5-5", k=2, post=post, api="anthropic", view="claim", api_key="lbl")
        self.assertEqual(dr("X is 1", {"f.py": "SECRET_CODE = 1\n"}), [])
        ((u, payload, h),) = sent
        self.assertEqual((u, h["x-api-key"], h["anthropic-version"]), ("http://door:1/v1/messages", "lbl", "2023-06-01"))
        self.assertIn("CLAIM: X is 1", payload["messages"][0]["content"])
        self.assertNotIn("SECRET_CODE", json.dumps(payload))
        self.assertNotIn("temperature", payload)

    def test_from_env_needs_a_url(self):
        self.assertIsNone(D.from_env({}))
        self.assertTrue(callable(D.from_env({"FALSIFY_DRAFT_URL": "http://127.0.0.1:9/v1"})))

class Done(Base):
    def proved(self):
        rid = self.reg()
        self.write("subject.py", FIXED)
        return self.judge(rid)[0]

    def test_done_on_a_proved_result_and_refusals(self):
        h = self.proved()
        void = self.judge(self.reg("test_taut.py"))[0]
        for claim, rid, word in (("add works", void, "void"), ("add works", "0" * 64, "does not exist"),
                                 ("something else", h, "evidence for"), ("add works", "nothex", "not a sha256")):
            with self.assertRaises(F.Refused) as e:
                self.S.done(claim, rid)
            self.assertIn(word, str(e.exception))
        self.assertIn('"claim":"add works"', self.S.done("add works", h))
        self.assertEqual(self.S.audit(), [])

    def test_audit_demotes_a_done_whose_evidence_moved(self):
        h = self.proved()
        self.S.done("add works", h)
        p = os.path.join(self.S.root, "results", h + ".json")
        res = json.loads(F.read_bytes(p))
        with open(p, "wb") as f:
            f.write(F.canon({**res, "why": "edited"}))
        bad = self.S.audit()
        self.assertEqual(len(bad), 1)
        self.assertIn("MOVED", bad[0][1])


class Cli(Base):
    def test_register_judge_done_audit(self):
        spec = os.path.join(self.tmp.name, "spec.json")
        with open(spec, "w") as f:
            json.dump({"claim": "add works", "repo": self.repo, "check": {"argv": [PY, "test_add.py"]}}, f)
        st = ["--store", self.S.root]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(F.main(st + ["register", spec]), 0)
        rid = out.getvalue().strip()
        self.write("subject.py", FIXED)
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
