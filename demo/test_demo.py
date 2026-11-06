"""The demo's expected story, pinned: the weak test reads VOID with a GREEN receipt, the honest one PROVED
covering every hunk, the retry ledger keeps every registration. Runs the whole demo (about 15 s)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_demo  # noqa: E402


def test_the_demo_story():
    out = run_demo.main(["run_demo.py"])
    w, h = out["weak"], out["honest"]
    assert w["receipt_green"] and w["verdict"] == "void" and w["covered"] == 0 and w["done"].startswith("refused")
    assert w["words"] == "your check passes without your change"
    assert h["receipt_green"] and h["verdict"] == "proved" and (h["covered"], h["hunks"]) == (2, 2) and h["done"] == "DONE"
    assert [r["verdict"] for r in out["retry"]] == ["disproved", "void", "proved"]
    assert [r["receipt_green"] for r in out["retry"]] == [False, True, True]
    assert out["ledger"]["registrations"] == 4 and out["ledger"]["attempts"] == 3
    assert out["wall_s"] < 90
