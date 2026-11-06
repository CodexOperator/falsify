"""falsify step 4, live: a NO-diff claim on the demo fixture, K blind mutants drafted by the local model, judged with the
honest and the weak check. Also measures every kept mutant against both checks (the draw judges one; the table says
how the draw could have gone). Usage: python demo/run_blind_live.py <out.json> [K]  (the drafter comes from FALSIFY_DRAFT_* (falsify.draft.from_env),
default the local llama.cpp server http://127.0.0.1:8091/v1 in view "files"; and pytest in FALSIFY_DEMO_PYTHON or this interpreter).
Measured 14:34Z 11-06, Qwen3-4B Q4_K_M, K 5: honest PROVED (drawn 0, kills 5 of 5), weak VOID (drawn 2, kills 1 of 5);
5 of 5 drafts valid per claim, 213 s and 12018 completion tokens per draft, 0 USD."""
import json, os, shutil, subprocess, sys, tempfile, time
fpkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out, K = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5
sys.path.insert(0, fpkg)
from falsify import core as F, draft as D
PY = os.environ.get("FALSIFY_DEMO_PYTHON", sys.executable)
tmp = tempfile.mkdtemp(prefix="s4-")
repo = os.path.join(tmp, "shop")
shutil.copytree(os.path.join(fpkg, "demo", "fixture"), repo, ignore=shutil.ignore_patterns("BUG.md", "__pycache__"))
g = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
g("init", "-q"); g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
env = {"FALSIFY_DRAFT_URL": "http://127.0.0.1:8091/v1", "FALSIFY_DRAFT_MODEL": "qwen3-4b", **os.environ, "FALSIFY_DRAFT_K": str(K)}
dr = D.from_env(env)
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
