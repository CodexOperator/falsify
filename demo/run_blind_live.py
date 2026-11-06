"""falsify, live blind mutants: a NO-diff claim on the demo fixture, K blind mutants drafted by a model you point it
at, judged with the honest and the weak check. Also measures every kept mutant against both checks (the draw judges
one; the table says how the draw could have gone).
Usage: FALSIFY_DRAFT_URL=<OpenAI-compatible base, e.g. a local llama.cpp server http://localhost:8080/v1> \
       FALSIFY_DRAFT_MODEL=<model> python demo/run_blind_live.py <out.json> [K]
(FALSIFY_DRAFT_API=anthropic for a Messages API base; pytest in FALSIFY_DEMO_PYTHON or this interpreter)."""
import json, os, shutil, subprocess, sys, tempfile, time
fpkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out, K = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5
sys.path.insert(0, fpkg)
from falsify import core as F, draft as D
PY = os.environ.get("FALSIFY_DEMO_PYTHON", sys.executable)
tmp = tempfile.mkdtemp(prefix="falsify-blind-")
repo = os.path.join(tmp, "shop")
shutil.copytree(os.path.join(fpkg, "demo", "fixture"), repo, ignore=shutil.ignore_patterns("BUG.md", "__pycache__"))
g = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
g("init", "-q"); g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
dr = D.from_env({**os.environ, "FALSIFY_DRAFT_K": str(K)})
if dr is None:
    raise SystemExit("set FALSIFY_DRAFT_URL (and FALSIFY_DRAFT_MODEL) to a drafter endpoint")
S = F.Store(os.path.join(tmp, "store"), drafter=dr)
claim = "checkout applies a pct% discount to the cart total, and the total is never negative"
chk = lambda t: [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", t]
t0 = time.time()
honest = S.register(claim, repo, chk("tests/test_honest.py"), blind=K)
draft_s = round(time.time() - t0, 1)
usage = (dr.last or {}).get("usage")
weak = S.register(claim + " (weak check)", repo, chk("tests/test_weak.py"), blind=K)   # a second, independent draft
res = {"claim": claim, "K": K, "model": dr.model, "view": dr.view, "draft_wall_s": draft_s, "usage_first_draft": usage}
for name, rid in (("honest", honest), ("weak", weak)):
    rec = S.load("records", rid)[0]
    _, r = S.judge(rid, approve=lambda *a: True)
    table = []
    for i, m in enumerate(rec["blind"]["mutants"]):
        d = os.path.join(tmp, f"{name}-m{i}")
        F.copy_tree(repo, d)
        err = F.apply_blind(d, m)
        run = None if err else F.run_check(rec["check"], d)
        table.append({"i": i, "mutant": m, "applies": err is None, "killed": bool(run and not run["passed"] and run["rc"] in rec["check"]["kill_rc"]),
                      "rc": run and run["rc"]})
        shutil.rmtree(d)
    res[name] = {"record": rid, "asked": rec["blind"]["asked"], "drafted": rec["blind"]["drafted"], "kept": len(rec["blind"]["mutants"]),
                 "blind_error": rec.get("blind_error"), "verdict": r["verdict"], "why": r["why"], "drawn": r["evidence"].get("blind_drawn"),
                 "kills": sum(x["killed"] for x in table), "table": table}
shutil.rmtree(tmp)
json.dump(res, open(out, "w"), indent=1)
print(json.dumps({k: (v if not isinstance(v, dict) or "table" not in v else {x: v[x] for x in v if x != "table"}) for k, v in res.items()}, indent=1))
