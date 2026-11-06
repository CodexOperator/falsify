# falsify demo

`python demo/run_demo.py` reproduces the failure every reviewer has seen: an agent's fix "proved" by a test that
passes with the fix reverted (it mocks the function it fixed). A post-run receipt seals it GREEN; falsify reads it
VOID, "your check passes without your change". The honest test on the same fix reads PROVED, covering both hunks of the fix.
A retry-until-green run keeps every registration in the ledger.

- Needs: stdlib + pytest. No network, no key. About 20-30 s (v2 runs the check on the final tree, the whole revert, and each hunk).
- Tested with **Python 3.13.5 + pytest 9.2.0**. If the `python3` you run it with has no pytest, `FALSIFY_DEMO_PYTHON` names
  another interpreter for the checks.
- Pinned: `python -m pytest demo/test_demo.py` (1 test, the whole story).
- `fixture/` is the demo's SUBJECT, run only inside temp copies; `conftest.py` keeps it out of collection.
  `fixture/BUG.md` describes the bug and the 2-hunk fix.
