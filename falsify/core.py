#!/usr/bin/env python3
"""falsify: an agent's claim is not DONE until it survives a check that could have failed.

v2: the mutant is DERIVED from the agent's own change, never written by the agent.
  register  BEFORE the work: the claim, the check (argv + cwd relative to the repo + timeout + expect_rc) and the BASE
            commit B of a git repo are canonicalised and hashed (sha256); records/<sha>.json is written exclusive-create,
            so an edit is a NEW record; a file whose bytes no longer hash to its name has MOVED.
  judge     AFTER the work: the final tree F is the repo's working tree as `git add -A` sees it (a private index; the
            user's index is never touched). The change is diff(B, F). The check runs on a temp copy of the final tree
            and MUST PASS; then on a temp copy with the WHOLE change reverted it MUST FAIL; then each HUNK is reverted
            alone: "the check covers k of h hunks of your change". Same argv, different trees: one approval covers it.
              check fails on the final tree               -> disproved (a full result, never an error)
              a revert that does not apply, a reverted tree that fails the pre-registered SANITY argv, or a
              check that dies on it with an rc outside the pinned KILL_RC (pytest: 2-5 are collection / usage
              errors, not kills) or times out                -> VOID "unrevertable", never a kill
              check passes with the whole change reverted -> VOID "your check passes without your change"
              no change (B == F)                           -> VOID "no mutant"
              otherwise                                    -> proved, with k of h
            The result is written content-addressed too (results/<sha>.json), naming the record's sha.
  done      a claim is DONE only on a RESOLVABLE result: result and record both exist and hash to their names, the
            result names that record and claim, and the verdict is proved; else refused with the reason.

R1 (rule 12): the judge runs ONLY the argv already in the hashed record, and only what an APPROVER says yes to:
judge() takes approve(kind, argv, cwd, record_sha) -> bool, asked BEFORE anything runs; no approver, a "no", or
interactive=False (cron, gateway, any non-interactive caller) = VOID 'not run', never a silent run.
R2 (rule 9): falsify never blocks completion; it records verdicts and NUDGES (falsify.adapter.make_pre_verify).

KEEP: pre-registered globs (e.g. "tests/**") whose part of the change is never reverted, so a mutant never "kills"
the check by deleting the check's own new test (a trivial kill); those hunks are reported, not measured.

Temp copies exclude `.git` (the check runs on files, not history) and include ignored files (a venv, build outputs),
so both trees differ ONLY by the change.
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, time

DEFAULT_TIMEOUT_S = 60
OUT_CAP = 4096
MAX_HUNKS = 50


class Refused(Exception):
    """A register / done the store refuses, with the reason."""


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def read_bytes(p):
    with open(p, "rb") as f:
        return f.read()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------- git ----------
def git(repo, *args, env=None, inp=None):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, input=inp, env={**os.environ, **(env or {})})
    if p.returncode:
        raise Refused(f"git {' '.join(args[:2])} failed: {p.stderr.decode(errors='replace').strip()[:300]}")
    return p.stdout


NEVER_THE_CHANGE = (":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc")   # bytecode a test run leaves behind


def final_tree(repo):
    """The working tree as a git tree sha, through a PRIVATE index (untracked non-ignored files included, bytecode
    excluded: running the check in the repo writes __pycache__, which is never the agent's change)."""
    with tempfile.TemporaryDirectory(prefix="falsify-idx-") as t:
        env = {"GIT_INDEX_FILE": os.path.join(t, "index")}
        git(repo, "read-tree", "--empty", env=env)
        git(repo, "add", "-A", "--", ".", *NEVER_THE_CHANGE, env=env)
        return git(repo, "write-tree", env=env).decode().strip()


def change(repo, base_tree, tree, keep=()):
    """diff(base, final) with ZERO context lines: every changed region is its own hunk (git's default 3 lines of
    context would merge nearby edits into one). It is only ever applied, reversed, onto an exact copy of the final
    tree, so the missing context cannot misplace it."""
    excl = [f":(exclude,glob){k}" for k in keep]
    return git(repo, "diff", "-U0", "--binary", "--no-renames", "--full-index", base_tree, tree, "--", ".", *excl)


def hunks(diff):
    """Split a git diff into units revertable alone: each @@ hunk with its file header; a file section with no @@
    (binary, mode-only, empty new file) is one unit. -> [(path, patch bytes)]"""
    units = []
    for sec in re.split(rb"(?m)^(?=diff --git )", diff):
        if not sec.strip():
            continue
        m = re.match(rb"diff --git a/(.*?) b/", sec)
        path = m.group(1).decode(errors="replace") if m else "?"
        parts = re.split(rb"(?m)^(?=@@ )", sec)
        head, hs = parts[0], parts[1:]
        if not hs or b"GIT binary patch" in head:
            units.append((path, sec))
        else:
            units.extend((path, head + h) for h in hs)
    return units


def copy_tree(repo, dst):
    shutil.copytree(repo, dst, symlinks=True, ignore=shutil.ignore_patterns(".git"))


def revert(dst, patch):
    """git apply -R inside dst (a throwaway repo root, removed after) -> None, or the reason it did not apply."""
    subprocess.run(["git", "init", "-q", dst], check=True, capture_output=True)
    try:
        p = subprocess.run(["git", "-C", dst, "apply", "-R", "--unidiff-zero", "--binary", "-"], input=patch, capture_output=True)
        return None if p.returncode == 0 else p.stderr.decode(errors="replace").strip()[:300]
    finally:
        shutil.rmtree(os.path.join(dst, ".git"), ignore_errors=True)


# ---------- the record ----------
def _norm_check(c):
    if not isinstance(c, dict) or not isinstance(c.get("argv"), list) or not c["argv"] or not all(isinstance(a, str) for a in c["argv"]):
        raise Refused("check.argv must be a non-empty list of strings")
    cwd = os.path.normpath(c.get("cwd", "."))
    if os.path.isabs(cwd) or cwd.split(os.sep)[0] == "..":
        raise Refused("check.cwd must be relative to the repo and inside it")
    kill = sorted({int(x) for x in c.get("kill_rc", [1])})
    exp = int(c.get("expect_rc", 0))
    if not kill or exp in kill:
        raise Refused("check.kill_rc must be a non-empty list of exit codes that excludes expect_rc")
    return {"argv": list(c["argv"]), "cwd": cwd, "expect_rc": exp, "kill_rc": kill,
            "timeout_s": float(c.get("timeout_s", DEFAULT_TIMEOUT_S))}


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
    def register(self, claim, repo, check, base="HEAD", note="", sanity=None, keep=()):
        if not isinstance(claim, str) or not claim.strip():
            raise Refused("claim must be a non-empty string")
        top = git(os.path.abspath(repo), "rev-parse", "--show-toplevel").decode().strip()
        b = git(top, "rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
        rec = {"claim": claim, "repo": top, "base": b, "base_tree": git(top, "rev-parse", f"{b}^{{tree}}").decode().strip(),
               "check": _norm_check(check), "note": note, "registered_at": now()}
        if sanity is not None:
            s = _norm_check(sanity)
            rec["sanity"] = {k: s[k] for k in ("argv", "cwd", "expect_rc", "timeout_s")}
        if keep:
            if not isinstance(keep, (list, tuple)) or not all(isinstance(k, str) and k and not k.startswith(("/", ":")) for k in keep):
                raise Refused("keep must be a list of repo-relative globs")
            rec["keep"] = sorted(set(keep))
        return self._write_once("records", rec)

    # ---- judge ----
    def judge(self, rid, approve=None, interactive=True, max_hunks=MAX_HUNKS):
        rec, why = self.load("records", rid)
        if rec is None:
            return self._result(rid, None, "void", why)
        if not interactive:
            return self._result(rid, rec["claim"], "void", "not run: a non-interactive caller never runs a check")
        if approve is None:
            return self._result(rid, rec["claim"], "void", "not run: no approver")
        chk = rec["check"]
        try:
            F = final_tree(rec["repo"])
            diff = change(rec["repo"], rec["base_tree"], F, rec.get("keep", ()))
            kept = change(rec["repo"], rec["base_tree"], F) != diff if rec.get("keep") else False
        except Refused as e:
            return self._result(rid, rec["claim"], "void", f"not run: {e}")
        ev = {"final_tree": F, "diff_sha256": sha(diff), "kept_paths_changed": kept}
        if not diff:
            return self._result(rid, rec["claim"], "void", "no mutant: the tree has no change since the base"
                                + (" outside the kept paths" if kept else ""), **ev)
        san = rec.get("sanity")
        for kind, c in (("check", chk),) + ((("sanity", san),) if san else ()):
            if approve(kind, list(c["argv"]), os.path.join(rec["repo"], c["cwd"]), rid) is not True:
                return self._result(rid, rec["claim"], "void", f"not run: the {kind} was not approved", **ev)
        units = hunks(diff)
        ev["hunks_total"] = len(units)
        with tempfile.TemporaryDirectory(prefix="falsify-") as tmp:
            fin = os.path.join(tmp, "final")
            copy_tree(rec["repo"], fin)
            if san:
                s = run_check(san, fin)
                ev["final_sanity_run"] = s
                if not s["passed"]:
                    return self._result(rid, rec["claim"], "void", "the sanity check fails on your final tree: no reading", **ev)
            c = run_check(chk, fin)
            ev["final_run"] = c
            if c["timed_out"]:
                return self._result(rid, rec["claim"], "void", "the check timed out on the final tree: no reading", **ev)
            if not c["passed"]:
                return self._result(rid, rec["claim"], "disproved", "the check FAILS on your final tree", **ev)
            r, ev["reverted_run"] = self._mutant(chk, san, rec["repo"], tmp, "reverted", diff)
            if r != "killed":
                return self._result(rid, rec["claim"], "void", r, **ev)
            cover = []
            for i, (path, patch) in enumerate(units[:max_hunks]):
                v, run = self._mutant(chk, san, rec["repo"], tmp, f"hunk{i}", patch)
                cover.append({"path": path, "patch_sha256": sha(patch), "outcome": v, "run": run})
        k = sum(x["outcome"] == "killed" for x in cover)
        un = sum(x["outcome"].startswith("unrevertable") for x in cover)
        ev.update(hunks_measured=len(cover), hunks_killed=k, hunks_unrevertable=un, hunks=cover)
        more = (f" ({len(units) - len(cover)} beyond the cap not measured)" if len(units) > len(cover) else "") + (
            f"; {un} hunk(s) unrevertable alone" if un else "")
        return self._result(rid, rec["claim"], "proved", f"the check passes on your change and fails without it; it covers {k} of {len(units)} hunks{more}", **ev)

    def _mutant(self, chk, san, repo, tmp, name, patch):
        """-> (outcome, run): killed, or the VOID reason. Only an rc in the pinned kill_rc is a kill."""
        d = os.path.join(tmp, name)
        copy_tree(repo, d)
        try:
            err = revert(d, patch)
            if err:
                return f"unrevertable: the revert does not apply ({err})", None
            if san:
                s = run_check(san, d)
                if not s["passed"]:
                    return "unrevertable: the reverted tree fails the sanity check", s
            r = run_check(chk, d)
            if r["timed_out"]:
                return "unrevertable: the check timed out on the reverted tree", r
            if r["passed"]:
                return "your check passes without your change", r
            if r["rc"] not in chk["kill_rc"]:
                return f"unrevertable: the check died with rc {r['rc']}, not a kill (kill_rc {chk['kill_rc']})", r
            return "killed", r
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _result(self, rid, claim, verdict, why, **ev):
        res = {"record": rid, "claim": claim, "verdict": verdict, "why": why, "evidence": ev, "judged_at": now()}
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
def run_check(c, tree):
    t0 = time.time()
    try:
        p = subprocess.run(c["argv"], cwd=os.path.join(tree, c["cwd"]), capture_output=True, timeout=c["timeout_s"])
        rc, to, out, err = p.returncode, False, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, to, out, err = None, True, e.stdout or b"", e.stderr or b""
    except OSError as e:
        rc, to, out, err = None, False, b"", str(e).encode()
    return {"rc": rc, "timed_out": to, "passed": (not to) and rc == c["expect_rc"], "wall_s": round(time.time() - t0, 3),
            "stdout_sha256": sha(out), "stderr_sha256": sha(err), "stdout_tail": out[-OUT_CAP:].decode(errors="replace"),
            "stderr_tail": err[-OUT_CAP:].decode(errors="replace")}


# ---------- CLI ----------
def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="falsify", description=__doc__.split("\n")[0])
    ap.add_argument("--store", default=os.environ.get("FALSIFY_STORE", ".falsify"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register", help="register BEFORE the work: a JSON file {claim, repo, check, base?, note?, sanity?, keep?}")
    r.add_argument("file")
    j = sub.add_parser("judge", help="judge a registered record by its sha, AFTER the work")
    j.add_argument("record")
    j.add_argument("--approve", action="store_true", help="the operator approves running this record's check")
    d = sub.add_parser("done", help="mark a claim done on a proved result")
    d.add_argument("claim")
    d.add_argument("result")
    sub.add_parser("audit", help="re-check every done line")
    a = ap.parse_args(argv)
    S = Store(a.store)
    try:
        if a.cmd == "register":
            spec = json.loads(read_bytes(a.file))
            print(S.register(spec["claim"], spec["repo"], spec["check"], spec.get("base", "HEAD"), spec.get("note", ""),
                             spec.get("sanity"), spec.get("keep", ())))
        elif a.cmd == "judge":
            yes = a.approve or (sys.stdin.isatty() and input(f"run record {a.record[:12]}'s check on your change and its reverts? [y/N] ").strip().lower() == "y")
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
