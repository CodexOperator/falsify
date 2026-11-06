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
              no change (B == F), no blind mutants         -> VOID "no mutant"
              otherwise                                    -> proved, with k of h
            The result is written content-addressed too (results/<sha>.json), naming the record's sha.
  ledger    ledger.jsonl: an APPEND-ONLY, hash-chained log of every register and every judge (each line names the
            sha256 of the line before it); audit() walks the chain, and done reports the claim's whole history:
            "this claim: N registrations, verdicts p / d / v; session total M", flagging a claim re-registered
            after a disproved or VOID verdict (retry-until-green is visible, not hidden behind the last green).
  done      a claim is DONE only on a RESOLVABLE result: result and record both exist and hash to their names, the
            result names that record and claim, and the verdict is proved; else refused with the reason.

R1 (rule 12): the judge runs ONLY the argv already in the hashed record, and only what an APPROVER says yes to:
judge() takes approve(kind, argv, cwd, record_sha) -> bool, asked BEFORE anything runs; no approver, a "no", or
interactive=False (cron, a webhook, any non-interactive caller) = VOID 'not run', never a silent run.
R2 (rule 9): falsify never blocks completion; it records verdicts and NUDGES (falsify.adapter.make_pre_verify).

BLIND: a claim with NO diff (docs, config, "it already works") has no change to revert. At
register, blind=K asks the store's DRAFTER (a separate model: falsify.draft) for K mutants drafted from the claim's
words and the base tree's files only, never the check; each is {path, old, new}, an edit of one exact occurrence. The
valid ones are hashed into the record with sha256 of a fresh seed (commit); the seed waits in seeds/. Every file the
check / sanity argv or a KEEP glob names is WITHHELD from the drafter (recorded, with the size-cap cut list). At judge, with no
diff, the seed is revealed and must hash to the commit, and mutant j = sha256(seed + record sha) mod K is DRAWN: the
check must pass on the final tree and be killed on the final tree with mutant j applied (same kill quality as a
revert). A drawn mutant that does not apply is VOID "unrevertable"; one the check passes is VOID "your check passes
on the drawn blind mutant". The agent never writes, sees in advance, or picks the mutant it is judged against.

COMMIT-FIRST: an agent that commits its change and re-registers at HEAD leaves no diff. With no diff, the judge first
widens to the EARLIEST base registered in the SAME SESSION (the ledger's session field; same repo) that is a strict
ancestor of the record's base, and
derives the mutant from that change as usual, saying "base includes this session's commits". Only a base with no
such ancestor (or no change since it) goes blind.

KEEP: pre-registered globs (e.g. "tests/**") whose part of the change is never reverted, so a mutant never "kills"
the check by deleting the check's own new test (a trivial kill); those hunks are reported, not measured.

Temp copies exclude `.git` (the check runs on files, not history) and include ignored files (a venv, build outputs),
so both trees differ ONLY by the change.
"""
import fcntl, fnmatch, hashlib, json, os, re, secrets, shutil, subprocess, sys, tempfile, time

DEFAULT_TIMEOUT_S = 60
OUT_CAP = 4096
MAX_HUNKS = 50
GENESIS = "0" * 64


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


def final_tree(repo, exclude=()):
    """The working tree as a git tree sha, through a PRIVATE index (untracked non-ignored files included, bytecode
    excluded: running the check in the repo writes __pycache__, which is never the agent's change; so are the extra
    `exclude` pathspecs, e.g. falsify's own store when it lives inside the repo)."""
    with tempfile.TemporaryDirectory(prefix="falsify-idx-") as t:
        env = {"GIT_INDEX_FILE": os.path.join(t, "index")}
        git(repo, "read-tree", "--empty", env=env)
        git(repo, "add", "-A", "--", ".", *NEVER_THE_CHANGE, *exclude, env=env)
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


BYTECODE = ("__pycache__", "*.pyc", "*.pyo")


def copy_tree(repo, dst):
    """the working tree into a throwaway dst, minus .git and every bytecode file: a copied .pyc keeps its source's
    mtime, so a revert landing in the same second with a same-size change (a moved line) would load the FIXED bytecode
    and an honest check would read 'passes without your change' (found by a positive-control run)."""
    shutil.copytree(repo, dst, symlinks=True, ignore=shutil.ignore_patterns(".git", *BYTECODE))


def revert(dst, patch):
    """git apply -R inside dst (a throwaway repo root, removed after) -> None, or the reason it did not apply."""
    subprocess.run(["git", "init", "-q", dst], check=True, capture_output=True)
    try:
        p = subprocess.run(["git", "-C", dst, "apply", "-R", "--unidiff-zero", "--binary", "-"], input=patch, capture_output=True)
        return None if p.returncode == 0 else p.stderr.decode(errors="replace").strip()[:300]
    finally:
        shutil.rmtree(os.path.join(dst, ".git"), ignore_errors=True)


PYTEST_FILES = ("test_*.py", "*_test.py", "conftest.py")
UNITTEST_FILES = ("test*.py",)


def _runner(argv):
    """-> "pytest", "unittest", "test" (any other test runner: a script or a subcommand named test), or None."""
    base = [os.path.basename(a) for a in argv]
    if any(b in ("pytest", "py.test") for b in base):
        return "pytest"
    if "unittest" in argv:
        return "unittest"
    return "test" if any("test" in b for b in base) else None


def discovery_globs(runner, config):
    """the files a runner with NO path argument discovers: pytest's defaults plus testpaths / python_files from the
    base's pytest.ini, tox.ini, setup.cfg or pyproject.toml (config: {name: text}); unittest's test*.py; both for any
    other test runner. -> (basename globs, directories)."""
    import configparser
    globs, dirs = set(), set()
    if runner in ("pytest", "test"):
        globs.update(PYTEST_FILES)
        for name, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
            if name in config:
                cp = configparser.ConfigParser(interpolation=None)
                try:
                    cp.read_string(config[name])
                except configparser.Error:
                    continue
                if cp.has_section(section):
                    globs.update(cp.get(section, "python_files", fallback="").split())
                    dirs.update(cp.get(section, "testpaths", fallback="").split())
        if "pyproject.toml" in config:
            try:
                import tomllib
                opts = tomllib.loads(config["pyproject.toml"]).get("tool", {}).get("pytest", {}).get("ini_options", {})
            except Exception:
                opts = {}
            for key, into in (("python_files", globs), ("testpaths", dirs)):
                v = opts.get(key, [])
                into.update(v.split() if isinstance(v, str) else [str(x) for x in v])
    if runner in ("unittest", "test"):
        globs.update(UNITTEST_FILES)
    return globs, {os.path.normpath(d) for d in dirs}


def named_paths(checks, keep=(), config=None, tracked=None):
    """-> withhold(path) -> bool: True for a repo path the check or sanity argv names (each token read as a path
    relative to that check's cwd, a pytest node id's file part included, a relative argv[0] script too; a token naming
    a directory names everything under it, "." the whole tree), a KEEP glob matches, or, when a check runs a test
    runner with NO path argument, the runner would DISCOVER (discovery_globs). What the check runs is never shown to a
    blind drafter. tracked (the base's paths, if given): a token counts as a PATH argument only if it names a tracked
    file or directory, or ".", so `-m pytest -q` names no path and its discovery set is withheld."""
    names, globs, dirs = set(), set(), set()
    real = (lambda n: n == "." or n in tracked or any(t.startswith(n + "/") for t in tracked)) if tracked is not None else (lambda n: True)
    for c in checks:
        if not c:
            continue
        mine = set()
        for i, tok in enumerate(c["argv"]):
            if tok.startswith("-") or os.path.isabs(tok) or (i == 0 and "/" not in tok):
                continue
            n = os.path.normpath(os.path.join(c["cwd"], tok.split("::")[0]))
            if n.split(os.sep)[0] != ".." and real(n):
                (names if i == 0 else mine).add(n)
        names |= mine
        r = _runner(c["argv"])
        if r and not mine:
            g, d = discovery_globs(r, config or {})
            globs |= g
            dirs |= {os.path.normpath(os.path.join(c["cwd"], x)) for x in d}

    def withhold(path):
        return (any(n == "." or path == n or path.startswith(n + "/") for n in names | dirs)
                or any(fnmatch.fnmatch(path, g) for g in keep)
                or any(fnmatch.fnmatch(os.path.basename(path), g) for g in globs))
    return withhold


def base_files(top, base, withhold=lambda p: False, max_file=8000, max_total=60000):
    """-> ({path: text}, withheld, cut): the base commit's tracked text files a blind drafter reads (the claim's words
    plus this), minus every path withhold() names (the check's own files), each <= max_file chars, <= max_total in all;
    `cut` lists the text files dropped by those two caps."""
    out, withheld, cut, total = {}, [], [], 0
    for path in git(top, "ls-tree", "-r", "--name-only", "-z", base).decode().split("\0"):
        if not path:
            continue
        if withhold(path):
            withheld.append(path)
            continue
        try:
            text = git(top, "show", f"{base}:{path}").decode()
        except (Refused, UnicodeDecodeError):
            continue
        if "\0" in text:
            continue
        if len(text) > max_file or total + len(text) > max_total:
            cut.append(path)
            continue
        out[path], total = text, total + len(text)
    return out, withheld, cut


def _blind_ok(m, files):
    """a drafted mutant is kept only as {path, old, new}: a base file, old occurring in it exactly once, old != new."""
    if not isinstance(m, dict) or not all(isinstance(m.get(k), str) for k in ("path", "old", "new")):
        return None
    m = {"path": os.path.normpath(m["path"]), "old": m["old"], "new": m["new"]}
    if not m["old"] or m["old"] == m["new"] or m["path"] not in files or files[m["path"]].count(m["old"]) != 1:
        return None
    return m


def apply_blind(dst, m):
    """edit one exact occurrence in dst (a throwaway copy) -> None, or the reason it did not apply."""
    p = os.path.join(dst, m["path"])
    if os.path.isabs(m["path"]) or m["path"].split(os.sep)[0] == ".." or not os.path.isfile(p):
        return f"{m['path']} is not a file in the tree"
    with open(p, encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    if text.count(m["old"]) != 1:
        return f"its old text occurs {text.count(m['old'])} times in {m['path']}, not once"
    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text.replace(m["old"], m["new"]))
    return None


def draw_index(seed, rid, k):
    return int(sha((seed + rid).encode()), 16) % k


# ---------- the record ----------
CHECK_SHAPE = ('expected {"argv": [str, ...], "cwd"?: str (relative to the repo), "expect_rc"?: int, "kill_rc"?: [int], '
               '"timeout_s"?: number}, or a bare list of strings (= argv); e.g. {"argv": ["python", "-m", "pytest", "-q", '
               '"tests/test_cart.py"]}')
SHAPES = {
    "claim": 'expected a non-empty string; e.g. "cart.total applies the 10% discount once"',
    "repo": 'expected a path inside a git work tree; e.g. "." or "/home/me/shop"',
    "base": 'expected a commit-ish in repo (default "HEAD"); e.g. "HEAD" or "abc1234"',
    "keep": 'expected a list of repo-relative globs; e.g. ["tests/test_cart.py", "tests/**"]',
    "blind": 'expected K, the number of blind mutants to draft for a claim with no diff; e.g. 5',
}


def refused(field, msg):
    """a schema refusal: the reason, then the expected shape and a one-line example."""
    return Refused(f"{msg}; {CHECK_SHAPE if field in ('check', 'sanity') else SHAPES[field]}")


def _norm_check(c, field="check"):
    if isinstance(c, list):
        c = {"argv": c}   # a bare list of strings is the argv
    if not isinstance(c, dict) or not isinstance(c.get("argv"), list) or not c["argv"] or not all(isinstance(a, str) for a in c["argv"]):
        raise refused(field, f"{field}.argv must be a non-empty list of strings")
    if not isinstance(c.get("cwd", "."), str):
        raise refused(field, f"{field}.cwd must be a string")
    cwd = os.path.normpath(c.get("cwd", "."))
    if os.path.isabs(cwd) or cwd.split(os.sep)[0] == "..":
        raise refused(field, f"{field}.cwd must be relative to the repo and inside it")
    try:
        kill = sorted({int(x) for x in c.get("kill_rc", [1])})
        exp = int(c.get("expect_rc", 0))
        timeout = float(c.get("timeout_s", DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        raise refused(field, f"{field}.expect_rc / kill_rc / timeout_s must be an int / a list of ints / a number") from None
    if not kill or exp in kill:
        raise refused(field, f"{field}.kill_rc must be a non-empty list of exit codes that excludes expect_rc")
    return {"argv": list(c["argv"]), "cwd": cwd, "expect_rc": exp, "kill_rc": kill, "timeout_s": timeout}


class Store:
    def __init__(self, root, drafter=None, session=None):
        """drafter(claim, files) -> [{path, old, new}, ...]: the blind mutant author (falsify.draft.make_drafter), or None.
        session: the id every register is logged under (default FALSIFY_SESSION, else one id per Store): the commit-first
        widening never looks outside the record's own session."""
        self.root, self.drafter = os.path.abspath(root), drafter
        self.session = session or os.environ.get("FALSIFY_SESSION") or secrets.token_hex(8)
        for d in ("records", "results", "seeds"):
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
    def register(self, claim, repo, check, base="HEAD", note="", sanity=None, keep=(), blind=0, session=None):
        if not isinstance(claim, str) or not claim.strip():
            raise refused("claim", "claim must be a non-empty string")
        if not isinstance(repo, str) or not repo.strip():
            raise refused("repo", "repo must be a non-empty string")
        try:
            top = git(os.path.abspath(repo), "rev-parse", "--show-toplevel").decode().strip()
        except Refused as e:
            raise refused("repo", f"repo {repo!r} is not inside a git work tree ({e})") from None
        try:
            b = git(top, "rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
        except Refused as e:
            raise refused("base", f"base {base!r} is not a commit in {top} ({e})") from None
        rec = {"claim": claim, "repo": top, "base": b, "base_tree": git(top, "rev-parse", f"{b}^{{tree}}").decode().strip(),
               "check": _norm_check(check), "note": note, "registered_at": now()}
        if sanity is not None:
            s = _norm_check(sanity, "sanity")
            rec["sanity"] = {k: s[k] for k in ("argv", "cwd", "expect_rc", "timeout_s")}
        if keep:
            if not isinstance(keep, (list, tuple)) or not all(isinstance(k, str) and k and not k.startswith(("/", ":")) for k in keep):
                raise refused("keep", "keep must be a list of repo-relative globs")
            rec["keep"] = sorted(set(keep))
        if blind:
            if not isinstance(blind, int) or isinstance(blind, bool) or not 1 <= blind <= 20:
                raise refused("blind", "blind must be an int from 1 to 20 (K mutants to draft)")
            if self.drafter is None:
                raise Refused("blind: this store has no drafter (falsify.draft.make_drafter; the plugin reads FALSIFY_DRAFT_URL)")
            config = {}
            for name in ("pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"):
                try:
                    config[name] = git(top, "show", f"{b}:{name}").decode()
                except (Refused, UnicodeDecodeError):
                    pass
            tracked = set(git(top, "ls-tree", "-r", "--name-only", "-z", b).decode().split("\0")) - {""}
            files, withheld, cut = base_files(top, b, named_paths((rec["check"], rec.get("sanity")), rec.get("keep", ()), config, tracked))
            try:
                drafted = list(self.drafter(claim, files) or [])[:blind]
            except Exception as e:   # a drafter that fails drafts nothing; the claim stays registrable
                drafted, rec["blind_error"] = [], f"{type(e).__name__}: {e}"[:300]
            kept = []
            for m in map(lambda m: _blind_ok(m, files), drafted):
                if m and m not in kept:
                    kept.append(m)
            seed = secrets.token_hex(32)
            rec["blind"] = {"asked": blind, "drafted": len(drafted), "mutants": kept, "seed_commit": sha(seed.encode()),
                            "view": getattr(self.drafter, "view", None), "model": getattr(self.drafter, "model", None),
                            "withheld": withheld, "cut": cut}
            p = os.path.join(self.root, "seeds", rec["blind"]["seed_commit"])
            if not os.path.exists(p):
                with open(p, "x") as f:
                    f.write(seed)
        h = self._write_once("records", rec)
        self._log({"event": "register", "record": h, "claim": claim, "session": str(session or self.session)})
        return h

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
            F = final_tree(rec["repo"], self._store_pathspec(rec["repo"]))
            diff = change(rec["repo"], rec["base_tree"], F, rec.get("keep", ()))
            kept = change(rec["repo"], rec["base_tree"], F) != diff if rec.get("keep") else False
        except Refused as e:
            return self._result(rid, rec["claim"], "void", f"not run: {e}")
        note = ""
        if not diff:   # commit-first dodge: widen to this session's earliest registered base that is an ancestor of ours
            wide = self._earliest_ancestor(rid, rec)
            if wide:
                try:
                    d2 = change(rec["repo"], wide[1]["base_tree"], F, rec.get("keep", ()))
                except Refused:
                    d2 = b""
                if d2:
                    diff, note = d2, (f"; base includes this session's commits: judged from {wide[1]['base'][:12]} "
                                      f"(record {wide[0][:12]}), the earliest registered ancestor of {rec['base'][:12]}")
        ev = {"final_tree": F, "diff_sha256": sha(diff), "kept_paths_changed": kept}
        if note:
            ev["base_widened_to"] = {"base": wide[1]["base"], "record": wide[0]}
        bl = rec.get("blind")
        if not diff and not (bl and bl["mutants"]):
            return self._result(rid, rec["claim"], "void", "no mutant: the tree has no change since the base"
                                + (" outside the kept paths" if kept else "")
                                + (f", and no blind mutant survived drafting ({bl['drafted']} drafted)" if bl else ""), **ev)
        san = rec.get("sanity")
        for kind, c in (("check", chk),) + ((("sanity", san),) if san else ()):
            if approve(kind, list(c["argv"]), os.path.join(rec["repo"], c["cwd"]), rid) is not True:
                return self._result(rid, rec["claim"], "void", f"not run: the {kind} was not approved", **ev)
        if not diff:
            return self._judge_blind(rid, rec, chk, san, ev)
        R = lambda verdict, why, **e: self._result(rid, rec["claim"], verdict, why + note, **e)
        units = hunks(diff)
        ev["hunks_total"] = len(units)
        with tempfile.TemporaryDirectory(prefix="falsify-") as tmp:
            fin = os.path.join(tmp, "final")
            copy_tree(rec["repo"], fin)
            if san:
                s = run_check(san, fin)
                ev["final_sanity_run"] = s
                if not s["passed"]:
                    return R("void", "the sanity check fails on your final tree: no reading", **ev)
            c = run_check(chk, fin)
            ev["final_run"] = c
            if c["timed_out"]:
                return R("void", "the check timed out on the final tree: no reading", **ev)
            if not c["passed"]:
                return R("disproved", "the check FAILS on your final tree", **ev)
            r, ev["reverted_run"] = self._mutant(chk, san, rec["repo"], tmp, "reverted", lambda d: revert(d, diff))
            if r != "killed":
                return R("void", r, **ev)
            cover = []
            for i, (path, patch) in enumerate(units[:max_hunks]):
                v, run = self._mutant(chk, san, rec["repo"], tmp, f"hunk{i}", lambda d, p=patch: revert(d, p))
                cover.append({"path": path, "patch_sha256": sha(patch), "outcome": v, "run": run})
        k = sum(x["outcome"] == "killed" for x in cover)
        un = sum(x["outcome"].startswith("unrevertable") for x in cover)
        ev.update(hunks_measured=len(cover), hunks_killed=k, hunks_unrevertable=un, hunks=cover)
        more = (f" ({len(units) - len(cover)} beyond the cap not measured)" if len(units) > len(cover) else "") + (
            f"; {un} hunk(s) unrevertable alone" if un else "")
        return R("proved", f"the check passes on your change and fails without it; it covers {k} of {len(units)} hunks{more}", **ev)

    def _store_pathspec(self, repo):
        """the store itself is never the agent's change: when it lives inside repo (the CLI default, .falsify in the
        cwd), exclude its directory from the final tree."""
        rel = os.path.relpath(os.path.realpath(self.root), os.path.realpath(repo))
        return () if rel == ".." or rel.startswith(".." + os.sep) else (f":(exclude,literal){rel}",)

    def _earliest_ancestor(self, rid, rec):
        """-> (record sha, record) of the EARLIEST registration in this store's ledger from the SAME SESSION as rid's
        latest registration (a ledger line, hash-chained), same repo, whose base is a strict ancestor of rec's base, or
        None. A store that outlives a session never widens to an older session's base."""
        regs = [e for e in self.ledger()[0] if e.get("event") == "register"]
        mine = [e.get("session") for e in regs if e.get("record") == rid]
        if not mine or mine[-1] is None:
            return None
        for e in regs:
            if e.get("session") != mine[-1]:
                continue
            r, _ = self.load("records", e["record"])
            if not r or r["repo"] != rec["repo"] or r["base"] == rec["base"]:
                continue
            try:
                git(rec["repo"], "merge-base", "--is-ancestor", r["base"], rec["base"])
            except Refused:
                continue
            return e["record"], r
        return None

    def _judge_blind(self, rid, rec, chk, san, ev):
        """no diff: reveal the seed, draw ONE pre-registered blind mutant, and judge the check against it."""
        bl, K = rec["blind"], len(rec["blind"]["mutants"])
        p = os.path.join(self.root, "seeds", bl["seed_commit"])
        seed = read_bytes(p).decode() if os.path.exists(p) else None
        if seed is None or sha(seed.encode()) != bl["seed_commit"]:
            return self._result(rid, rec["claim"], "void", "the blind draw's seed is missing or does not match its commit", **ev)
        j = draw_index(seed, rid, K)
        ev.update(blind_seed=seed, blind_drawn=j, blind_k=K, blind_mutant_sha256=sha(canon(bl["mutants"][j])))
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
            r, ev["blind_run"] = self._mutant(chk, san, rec["repo"], tmp, "blind", lambda d: apply_blind(d, bl["mutants"][j]))
        if r != "killed":
            return self._result(rid, rec["claim"], "void", r.replace("the revert does not apply", "the drawn blind mutant does not apply")
                                .replace("your check passes without your change", "your check passes on the drawn blind mutant")
                                .replace("reverted tree", "mutated tree"), **ev)
        return self._result(rid, rec["claim"], "proved", f"no diff: the check passes on your tree and kills blind mutant {j} "
                            f"of {K} (drafted from the claim's words, drawn after the run by the committed seed)", **ev)

    def _mutant(self, chk, san, repo, tmp, name, mutate):
        """-> (outcome, run): killed, or the VOID reason. mutate(dir) -> None or why it did not apply. Only an rc in
        the pinned kill_rc is a kill."""
        d = os.path.join(tmp, name)
        copy_tree(repo, d)
        try:
            err = mutate(d)
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
        h = self._write_once("results", res)
        self._log({"event": "judge", "record": rid, "claim": claim, "result": h, "verdict": verdict})
        return h, res

    # ---- the ledger ----
    def _ledger_path(self):
        return os.path.join(self.root, "ledger.jsonl")

    def _log(self, entry):
        """append one hash-chained line under an exclusive lock: {seq, prev, at, ...entry}."""
        p = self._ledger_path()
        with open(p, "a+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            lines = f.read().splitlines()
            prev = sha(lines[-1]) if lines else GENESIS
            f.write(canon({**entry, "seq": len(lines), "prev": prev, "at": now()}) + b"\n")
            f.flush()
            os.fsync(f.fileno())

    def ledger(self):
        """-> (entries, problems): every line parsed; problems names each break in the chain (an edit, a removal, a
        reorder or an insertion before the tip all break it)."""
        p = self._ledger_path()
        lines = read_bytes(p).splitlines() if os.path.exists(p) else []
        out, bad, prev = [], [], GENESIS
        for i, ln in enumerate(lines):
            try:
                e = json.loads(ln)
            except ValueError:
                bad.append(f"ledger line {i} does not parse")
                prev = sha(ln)
                continue
            if e.get("prev") != prev or e.get("seq") != i:
                bad.append(f"ledger line {i} breaks the chain (seq {e.get('seq')}, prev {str(e.get('prev'))[:12]} != {prev[:12]})")
            out.append(e)
            prev = sha(ln)
        return out, bad

    def tally(self, claim):
        """the denominator: this claim's registrations and verdicts, the store's totals, and any retry after a loss."""
        es, bad = self.ledger()
        regs = [e for e in es if e.get("event") == "register" and e.get("claim") == claim]
        js = [e for e in es if e.get("event") == "judge" and e.get("claim") == claim]
        v = {k: sum(e.get("verdict") == k for e in js) for k in ("proved", "disproved", "void")}
        lost, retried = set(), False
        for e in es:
            if e.get("claim") != claim:
                continue
            if e.get("event") == "judge" and e.get("verdict") in ("disproved", "void"):
                lost.add(e["record"])
            if e.get("event") == "register" and lost:
                retried = True
        total_r = sum(e.get("event") == "register" for e in es)
        total_j = sum(e.get("event") == "judge" for e in es)
        s = (f"this claim: {len(regs)} registration(s), verdicts {v['proved']} proved / {v['disproved']} disproved / "
             f"{v['void']} void; session total {total_r} registration(s), {total_j} judgement(s)")
        if retried:
            s += "; RE-REGISTERED after a disproved or VOID verdict"
        if bad:
            s += f"; LEDGER BROKEN ({len(bad)} break(s))"
        return s

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
        return line + "\n" + self.tally(claim)

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
        bad += [("ledger.jsonl", b) for b in self.ledger()[1]]
        return bad


# ---------- running ----------
def run_check(c, tree):
    t0 = time.time()
    try:
        p = subprocess.run(c["argv"], cwd=os.path.join(tree, c["cwd"]), capture_output=True, timeout=c["timeout_s"],
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})   # -B for any python the check starts
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
    r = sub.add_parser("register", help="register BEFORE the work: a JSON file {claim, repo, check, base?, note?, sanity?, keep?, blind?}")
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
            if spec.get("blind"):
                try:
                    from . import draft
                except ImportError:   # run as a script, not as the package
                    import draft
                S.drafter = draft.from_env()
            print(S.register(spec["claim"], spec["repo"], spec["check"], spec.get("base", "HEAD"), spec.get("note", ""),
                             spec.get("sanity"), spec.get("keep", ()), spec.get("blind", 0)))
        elif a.cmd == "judge":
            yes = a.approve or (sys.stdin.isatty() and input(f"run record {a.record[:12]}'s check on your change and its reverts? [y/N] ").strip().lower() == "y")
            h, res = S.judge(a.record, approve=(lambda *_: True) if yes else None, interactive=a.approve or sys.stdin.isatty())
            why = res["why"] + (f"; now run: python -m falsify done {json.dumps(res['claim'])} {h}  to close this claim" if res["verdict"] == "proved" else "")
            print(json.dumps({"result": h, "verdict": res["verdict"], "why": why}))
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
