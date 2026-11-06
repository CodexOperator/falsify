#!/usr/bin/env python3
"""falsify demo: the test that passes with the fix reverted, caught; the honest test, proved; and a
retry-until-green run, where every retry is a visible registration.

  python demo/run_demo.py [--json OUT]      stdlib + pytest; no network, no key; about 15 s
  (the interpreter must have pytest; FALSIFY_DEMO_PYTHON names a different one for the checks).
  Measured with Python 3.13.5 + pytest 9.2.0 (a private venv; the system python3 here has no pytest): 11-13 s.

Acts (each in a temp copy of demo/fixture, with a fresh falsify store):
  1 WEAK    the agent-style fix plus a test that mocks the fixed function. A post-run receipt (a sha256 over
            the tree, the argv and the output) is GREEN. falsify registers the check with ONE mutant per hunk of
            the fix (that hunk reverted) BEFORE running anything; every mutant PASSES -> VOID, "your check
            passes without your change", covering 0 of 2 hunks.
  2 HONEST  the same fix, a test that calls the real code: every hunk's mutant FAILS, the check passes ->
            PROVED, covering 2 of 2 hunks.
  3 RETRY   an agent that retries until green: (a) half the fix with the honest test -> DISPROVED; (b) the same
            half fix, the test swapped for the mocked one -> VOID; (c) the whole fix with the honest test ->
            PROVED. The ledger shows every registration, not just the last green one.
falsify core is used as it stands (falsify/core.py: register, judge, done); the "covers k of h hunks" and the
plain-words verdicts are this demo's report over its per-hunk records.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from falsify import core as F  # noqa: E402

FIXTURE = os.path.join(HERE, "fixture")
PY = os.environ.get("FALSIFY_DEMO_PYTHON", sys.executable)
CLAMP_FIXED = "    pct = min(max(pct, 0), 100)\n"
FORMULA_FIXED = "    return round(total * (100 - pct) / 100, 2)\n"
FORMULA_BUGGY = "    return total - pct\n"
HUNKS = [  # each hunk's REVERT, as falsify's mutant patch on shop/pricing.py
    {"name": "clamp pct to 0..100", "path": "shop/pricing.py", "old": CLAMP_FIXED, "new": "\n"},
    {"name": "discount as a percentage", "path": "shop/pricing.py", "old": FORMULA_FIXED, "new": FORMULA_BUGGY},
]


def pytest_argv(test):
    return [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", test]


def tree(tmp, name, revert=()):
    """a copy of the fixture with the named hunks reverted (the agent's state at that moment)."""
    dst = os.path.join(tmp, name)
    shutil.copytree(FIXTURE, dst, ignore=shutil.ignore_patterns("__pycache__"))
    p = os.path.join(dst, "shop", "pricing.py")
    t = open(p).read()
    for h in revert:
        t = t.replace(h["old"], h["new"])
    open(p, "w").write(t)
    return dst


def receipt(cwd, argv):
    """the post-run seal stand-in: run once, then sha256 over the tree, the argv, the rc and the output."""
    r = F.run_check({"argv": argv, "cwd": cwd, "expect_rc": 0, "timeout_s": 60})
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(cwd)):
        for f in sorted(files):
            if "__pycache__" not in root:
                fp = os.path.join(root, f)
                h.update(os.path.relpath(fp, cwd).encode() + b"\0" + open(fp, "rb").read())
    h.update(json.dumps([argv[1:], r["rc"], r["stdout_sha256"]]).encode())
    return {"green": r["passed"], "sha256": h.hexdigest()}


def approve(kind, argv, cwd, rid):
    return True  # the operator approved running this record's check and mutant (demo)


def falsify(S, claim, cwd, test, hunks):
    """ONE record per hunk: the check is the test; the mutant is the same test with that hunk reverted.
    -> (verdict, plain words, k covered, h hunks, [result ids])."""
    rows = []
    for h in hunks:
        rid = S.register(claim, {"argv": pytest_argv(test), "cwd": cwd},
                         {"argv": pytest_argv(test), "cwd": cwd, "patch": {k: h[k] for k in ("path", "old", "new")}},
                         note=f"mutant = revert hunk: {h['name']}")
        res_id, res = S.judge(rid, approve=approve)
        rows.append((h, rid, res_id, res))
    covered = sum(1 for _, _, _, r in rows if r["mutant_run"] and not r["mutant_run"].get("passed", True))
    verdicts = {r["verdict"] for _, _, _, r in rows}
    if covered == 0:
        verdict, words = "void", "your check passes without your change"
    elif verdicts == {"proved"}:
        verdict, words = "proved", "your check fails without your change, and passes with it"
    elif "disproved" in verdicts:
        verdict, words = "disproved", "your check fails WITH your change"
    else:
        verdict, words = "void", "some of your change is never checked: " + ", ".join(
            h["name"] for h, _, _, r in rows if r["verdict"] == "void")
    return verdict, words, covered, len(hunks), [x[2] for x in rows]


def ledger(S):
    recs = os.listdir(os.path.join(S.root, "records"))
    v = {"proved": 0, "disproved": 0, "void": 0}
    for fn in os.listdir(os.path.join(S.root, "results")):
        res, _ = S.load("results", fn[:-5])
        v[res["verdict"]] += 1
    return {"registrations": len(recs), **v}


def main(argv):
    t0 = time.time()
    out = {}
    with tempfile.TemporaryDirectory(prefix="falsify-demo-") as tmp:
        claim = "checkout applies the discount as a percentage, clamped to 0..100"
        print("== 1 WEAK: the fix + a test that mocks the function it fixed")
        w = tree(tmp, "weak")
        rc = receipt(w, pytest_argv("tests/test_weak.py"))
        print(f"   post-run receipt: {'GREEN' if rc['green'] else 'RED'}  sha256 {rc['sha256'][:16]}")
        S1 = F.Store(os.path.join(tmp, "store-weak"))
        v, words, k, h, res = falsify(S1, claim, w, "tests/test_weak.py", HUNKS)
        print(f"   falsify: {v.upper()}  '{words}'  (covers {k} of {h} hunks)")
        try:
            S1.done(claim, res[0])
            done = "DONE"
        except F.Refused as e:
            done = f"refused: {e}"
        print(f"   done? {done}")
        out["weak"] = {"receipt_green": rc["green"], "verdict": v, "words": words, "covered": k, "hunks": h, "done": done}

        print("== 2 HONEST: the same fix + a test that calls the real code")
        o = tree(tmp, "honest")
        rc = receipt(o, pytest_argv("tests/test_honest.py"))
        S2 = F.Store(os.path.join(tmp, "store-honest"))
        v, words, k, h, res = falsify(S2, claim, o, "tests/test_honest.py", HUNKS)
        print(f"   post-run receipt: {'GREEN' if rc['green'] else 'RED'}  sha256 {rc['sha256'][:16]}")
        print(f"   falsify: {v.upper()}  '{words}'  (covers {k} of {h} hunks)")
        done = "DONE" if v == "proved" and all(S2.done(claim, r) for r in res) else "not done"
        print(f"   done? {done}")
        out["honest"] = {"receipt_green": rc["green"], "verdict": v, "words": words, "covered": k, "hunks": h, "done": done}

        print("== 3 RETRY UNTIL GREEN: every retry is a registration")
        S3 = F.Store(os.path.join(tmp, "store-retry"))
        half = [HUNKS[1]]  # the agent fixed the formula, not the clamp
        steps = [("a", "half the fix, honest test", tree(tmp, "retry-a", revert=[HUNKS[0]]), "tests/test_honest.py", half),
                 ("b", "half the fix, test swapped for the mocked one", tree(tmp, "retry-b", revert=[HUNKS[0]]),
                  "tests/test_weak.py", half),
                 ("c", "the whole fix, honest test", tree(tmp, "retry-c"), "tests/test_honest.py", HUNKS)]
        out["retry"] = []
        for tag, what, cwd, test, hunks in steps:
            rc = receipt(cwd, pytest_argv(test))
            v, words, k, h, _ = falsify(S3, claim, cwd, test, hunks)
            print(f"   ({tag}) {what}: receipt {'GREEN' if rc['green'] else 'RED'}, falsify {v.upper()}  '{words}'")
            out["retry"].append({"step": tag, "receipt_green": rc["green"], "verdict": v})
        L = ledger(S3)
        L["attempts"] = len(steps)
        L["attempt_verdicts"] = {v: sum(1 for r in out["retry"] if r["verdict"] == v) for v in ("disproved", "void", "proved")}
        a = L["attempt_verdicts"]
        print(f"   ledger: {L['registrations']} registrations over {L['attempts']} attempts: {a['disproved']} disproved, "
              f"{a['void']} void, {a['proved']} proved (one record per hunk: {L['disproved']} / {L['void']} / {L['proved']})")
        out["ledger"] = L
    out["wall_s"] = round(time.time() - t0, 2)
    print(f"== {out['wall_s']} s")
    if len(argv) > 2 and argv[1] == "--json":
        with open(argv[2], "w") as f:
            json.dump(out, f, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    main(sys.argv)
