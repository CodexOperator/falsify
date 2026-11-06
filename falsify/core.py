#!/usr/bin/env python3
"""falsify: an agent's claim is not DONE until it survives a check that could have failed.

Three acts, pure stdlib, one store directory:
  register  a falsifier RECORD {claim, check, mutant} is canonicalised and hashed (sha256) BEFORE any run; the file is
            content-addressed (records/<sha>.json) and written exclusive-create, so an edit is a NEW record, never a
            rewrite; a record whose bytes no longer hash to its name has MOVED.
  judge     re-hash the record, then run the check and its MUTANT (the same check against a deliberately broken
            subject). The mutant MUST FAIL: if it passes, the check could not have failed, so the verdict is VOID
            (vacuous). Otherwise: check passes -> proved; check fails -> disproved (a full result, never an error).
            The result is written content-addressed too (results/<sha>.json), naming the record's sha.
  done      a claim is DONE only on a RESOLVABLE result: the result file exists and hashes to its name, its record
            exists and hashes to its name, the result names that record and that claim, and the verdict is proved.
            Anything else is refused with the reason (the evidence gate).

R1 (rule 12): the judge runs ONLY the argv already in the hashed record, and only what an APPROVER says yes to: judge()
takes approve(kind, argv, cwd, record_sha) -> bool, asked for the mutant AND the check BEFORE either runs; no approver,
a "no", or interactive=False (cron, gateway, any non-interactive caller) = VOID 'not run', never a silent run.
R2 (rule 9): falsify never blocks completion; it records verdicts and NUDGES (falsify.adapter.make_pre_verify).

The vacuity rule (from goal:g5.50): a mutant that cannot differ from the check is VOID before anything runs (same
command, cwd and no patch), and a mutant that passes is VOID after.

A check is {"argv": [...], "cwd": optional dir, "timeout_s": optional, "expect_rc": optional (0)}; it PASSES iff it
exits with expect_rc inside its timeout. A mutant is a check plus an optional "patch": {"path", "old", "new"}: the
judge copies the check's cwd to a temp dir, replaces `old` (which must occur exactly once) with `new` in `path`, and
runs the mutant there, so the real tree is never touched.
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, time

DEFAULT_TIMEOUT_S = 60
OUT_CAP = 4096


class Refused(Exception):
    """A register / done the store refuses, with the reason."""


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def read_bytes(p):
    with open(p, "rb") as f:
        return f.read()


def write_text(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def _norm_check(c, what):
    if not isinstance(c, dict) or not isinstance(c.get("argv"), list) or not c["argv"] or not all(isinstance(a, str) for a in c["argv"]):
        raise Refused(f"{what}.argv must be a non-empty list of strings")
    out = {"argv": list(c["argv"]), "expect_rc": int(c.get("expect_rc", 0)), "timeout_s": float(c.get("timeout_s", DEFAULT_TIMEOUT_S))}
    if c.get("cwd") is not None:
        out["cwd"] = os.path.abspath(c["cwd"])
    if c.get("patch") is not None:
        p = c["patch"]
        if what != "mutant":
            raise Refused("only the mutant may carry a patch")
        if not all(isinstance(p.get(k), str) for k in ("path", "old", "new")) or not p["old"] or p["old"] == p["new"]:
            raise Refused("mutant.patch needs path, old (non-empty) and new (different from old)")
        out["patch"] = {k: p[k] for k in ("path", "old", "new")}
    return out


def vacuous_by_construction(check, mutant):
    """The g5.50 rule, static half: a mutant that runs the same command in the same place with no patch cannot differ."""
    return mutant.get("patch") is None and mutant["argv"] == check["argv"] and mutant.get("cwd") == check.get("cwd") \
        and mutant["expect_rc"] == check["expect_rc"]


class Store:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        for d in ("records", "results"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)

    def _path(self, kind, h):
        if not (isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h)):
            raise Refused(f"not a sha256: {h!r}")
        return os.path.join(self.root, kind, h + ".json")

    def _write_once(self, kind, obj):
        b = canon(obj)
        h = sha(b)
        p = self._path(kind, h)
        try:
            with open(p, "xb") as f:   # exclusive create: a record is written once, never rewritten
                f.write(b)
        except FileExistsError:
            if read_bytes(p) != b:
                raise Refused(f"{kind}/{h} exists with other bytes: the store was edited")
        return h

    def load(self, kind, h):
        """-> (obj, None) if the file exists and hashes to its name, else (None, reason)."""
        p = self._path(kind, h)
        if not os.path.exists(p):
            return None, f"{kind}/{h[:12]} does not exist"
        b = read_bytes(p)
        if sha(b) != h:
            return None, f"{kind}/{h[:12]} has MOVED: its bytes hash to {sha(b)[:12]}"
        return json.loads(b), None

    # ---- register ----
    def register(self, claim, check, mutant, note=""):
        if not isinstance(claim, str) or not claim.strip():
            raise Refused("claim must be a non-empty string")
        rec = {"claim": claim, "check": _norm_check(check, "check"), "mutant": _norm_check(mutant, "mutant"), "note": note,
               "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        return self._write_once("records", rec)

    # ---- judge ----
    def judge(self, rid, approve=None, interactive=True):
        rec, why = self.load("records", rid)
        if rec is None:
            return self._result(rid, None, "void", why, None, None)
        chk, mut = rec["check"], rec["mutant"]
        if vacuous_by_construction(chk, mut):
            return self._result(rid, rec["claim"], "void", "vacuous by construction: the mutant cannot differ from the check", None, None)
        if not interactive:
            return self._result(rid, rec["claim"], "void", "not run: a non-interactive caller never runs a check", None, None)
        if approve is None:
            return self._result(rid, rec["claim"], "void", "not run: no approver", None, None)
        for kind, c in (("mutant", mut), ("check", chk)):   # both asked BEFORE either runs; the record's own argv only
            if approve(kind, list(c["argv"]), c.get("cwd", chk.get("cwd")), rid) is not True:
                return self._result(rid, rec["claim"], "void", f"not run: the {kind} was not approved", None, None)
        m = run_mutant(mut, chk)
        if m.get("error"):
            return self._result(rid, rec["claim"], "void", f"mutant could not be built: {m['error']}", None, m)
        if m["passed"]:
            return self._result(rid, rec["claim"], "void", "the mutant PASSED: the check could not have failed (vacuous)", None, m)
        c = run_check(chk)
        if c["timed_out"]:
            return self._result(rid, rec["claim"], "void", "the check timed out: no reading", c, m)
        v = "proved" if c["passed"] else "disproved"
        return self._result(rid, rec["claim"], v, "the mutant failed as required; the check " + ("passed" if c["passed"] else "failed"), c, m)

    def _result(self, rid, claim, verdict, why, check_run, mutant_run):
        res = {"record": rid, "claim": claim, "verdict": verdict, "why": why, "check_run": check_run, "mutant_run": mutant_run,
               "judged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        return self._write_once("results", res), res

    # ---- done (the evidence gate) ----
    def done(self, claim, result_id):
        res, why = self.load("results", result_id)
        if res is None:
            raise Refused(f"not done: {why}")
        rec, why = self.load("records", res["record"])
        if rec is None:
            raise Refused(f"not done: the result's record is unresolvable: {why}")
        if res["claim"] != claim or rec["claim"] != claim:
            raise Refused(f"not done: the result is evidence for {res['claim']!r}, not {claim!r}")
        if res["verdict"] != "proved":
            raise Refused(f"not done: the verdict is {res['verdict']} ({res['why']})")
        line = canon({"claim": claim, "result": result_id, "record": res["record"]}).decode()
        with open(os.path.join(self.root, "done.jsonl"), "a") as f:
            f.write(line + "\n")
        return line

    def results_for(self, rid):
        """every resolvable result naming record rid, oldest first."""
        out = []
        for fn in sorted(os.listdir(os.path.join(self.root, "results"))):
            res, _ = self.load("results", fn[:-5]) if fn.endswith(".json") else (None, None)
            if res and res["record"] == rid:
                out.append((res["judged_at"], fn[:-5], res))
        return [(h, r) for _, h, r in sorted(out, key=lambda x: x[0])]

    def open_claims(self):
        """registered records whose claim is not DONE on a proved result: [(record sha, claim, latest verdict, why)]."""
        p = os.path.join(self.root, "done.jsonl")
        done = {json.loads(ln)["record"] for ln in (read_bytes(p).decode().splitlines() if os.path.exists(p) else [])}
        out = []
        for fn in sorted(os.listdir(os.path.join(self.root, "records"))):
            rid = fn[:-5]
            rec, why = self.load("records", rid)
            if rec is None:
                out.append((rid, None, "void", why))
                continue
            if rid in done:
                continue
            rs = self.results_for(rid)
            out.append((rid, rec["claim"], rs[-1][1]["verdict"] if rs else "unjudged", rs[-1][1]["why"] if rs else "never judged"))
        return out

    def audit(self):
        """Re-check every done line against the store now: -> [(line, reason)] for every line that no longer resolves."""
        bad, p = [], os.path.join(self.root, "done.jsonl")
        for ln in (read_bytes(p).decode().splitlines() if os.path.exists(p) else []):
            d = json.loads(ln)
            res, why = self.load("results", d["result"])
            rec, why2 = (self.load("records", d["record"]) if res else (None, why))
            if res is None or rec is None or res["verdict"] != "proved" or res["record"] != d["record"]:
                bad.append((ln, why or why2 or "verdict or record mismatch"))
        return bad


# ---------- running ----------
def _run(c, cwd):
    t0 = time.time()
    try:
        p = subprocess.run(c["argv"], cwd=cwd, capture_output=True, timeout=c["timeout_s"])
        rc, to, out, err = p.returncode, False, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, to, out, err = None, True, e.stdout or b"", e.stderr or b""
    except OSError as e:
        rc, to, out, err = None, False, b"", str(e).encode()
    return {"argv": c["argv"], "rc": rc, "timed_out": to, "passed": (not to) and rc == c["expect_rc"], "wall_s": round(time.time() - t0, 3),
            "stdout_sha256": sha(out), "stderr_sha256": sha(err), "stdout_tail": out[-OUT_CAP:].decode(errors="replace"),
            "stderr_tail": err[-OUT_CAP:].decode(errors="replace")}


def run_check(c):
    return _run(c, c.get("cwd"))


def run_mutant(m, check):
    """The mutant runs in a TEMP COPY of its cwd (the check's when it names none) with its patch applied; never in place."""
    if m.get("patch") is None:
        return _run(m, m.get("cwd", check.get("cwd")))
    src = m.get("cwd", check.get("cwd"))
    if not src or not os.path.isdir(src):
        return {"error": "a patched mutant needs a cwd directory to copy"}
    with tempfile.TemporaryDirectory(prefix="falsify-mut-") as tmp:
        dst = os.path.join(tmp, "tree")
        shutil.copytree(src, dst, symlinks=True)
        f = os.path.join(dst, m["patch"]["path"])
        if not os.path.realpath(f).startswith(os.path.realpath(dst) + os.sep) or not os.path.isfile(f):
            return {"error": f"patch path {m['patch']['path']!r} is not a file inside the tree"}
        t = read_bytes(f).decode("utf-8")
        n = t.count(m["patch"]["old"])
        if n != 1:
            return {"error": f"patch 'old' occurs {n} times in {m['patch']['path']!r}, must be exactly once"}
        write_text(f, t.replace(m["patch"]["old"], m["patch"]["new"]))
        r = _run(m, dst)
        r["patched"] = m["patch"]["path"]
        return r


# ---------- CLI ----------
def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="falsify", description=__doc__.split("\n")[0])
    ap.add_argument("--store", default=os.environ.get("FALSIFY_STORE", ".falsify"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register", help="register a falsifier record from a JSON file {claim, check, mutant, note?}")
    r.add_argument("file")
    j = sub.add_parser("judge", help="judge a registered record by its sha")
    j.add_argument("record")
    j.add_argument("--approve", action="store_true", help="the operator approves running this record's check and mutant")
    d = sub.add_parser("done", help="mark a claim done on a proved result")
    d.add_argument("claim")
    d.add_argument("result")
    sub.add_parser("audit", help="re-check every done line")
    a = ap.parse_args(argv)
    S = Store(a.store)
    try:
        if a.cmd == "register":
            spec = json.loads(read_bytes(a.file))
            print(S.register(spec["claim"], spec["check"], spec["mutant"], spec.get("note", "")))
        elif a.cmd == "judge":
            yes = a.approve or (sys.stdin.isatty() and input(f"run record {a.record[:12]}'s mutant and check? [y/N] ").strip().lower() == "y")
            h, res = S.judge(a.record, approve=(lambda *_: True) if yes else None, interactive=a.approve or sys.stdin.isatty())
            print(json.dumps({"result": h, "verdict": res["verdict"], "why": res["why"]}))
            return {"proved": 0, "disproved": 1}.get(res["verdict"], 2)
        elif a.cmd == "done":
            print(S.done(a.claim, a.result))
        else:
            bad = S.audit()
            for ln, why in bad:
                print(f"DEMOTED {why}: {ln}")
            return 1 if bad else 0
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
