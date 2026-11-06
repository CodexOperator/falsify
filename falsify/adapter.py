"""The thin seam between falsify's core and a host agent (Hermes, first): R2, rule 9. falsify never wraps or blocks
the agent's finish; it answers the host's public pre_verify hook with a NUDGE naming every claim that is not done on a
proved result, and otherwise lets the turn finish. Approval (R1) is the host's to supply: make_approver adapts any
yes/no callable, and nothing here runs a check by itself."""
from . import core as F  # noqa: F401


def nudge_message(store, limit=5):
    """-> the nudge text, or None when every registered claim is done on a proved result."""
    rows = store.open_claims()
    if not rows:
        return None
    lines = [f"- {(c or '(unreadable record)')[:120]} [{rid[:12]}]: {v} ({why})" for rid, c, v, why in rows[:limit]]
    more = f"\n... and {len(rows) - limit} more" if len(rows) > limit else ""
    return (f"falsify: {len(rows)} claim(s) are not done on a proved result. Each needs its pre-registered check to pass "
            f"while its mutant fails; VOID means the check could not have failed or was not run.\n" + "\n".join(lines) + more +
            "\nJudge them (with approval), or say plainly that they are not done.")


def make_pre_verify(store, max_attempts=1):
    """A pre_verify callback: kwargs as the host passes them (session_id, platform, model, coding, attempt,
    final_response, changed_paths, ...); returns {"action": "continue", "message"} or None. Self-throttled by
    attempt (the host also caps it by max_verify_nudges)."""
    def pre_verify(**kw):
        if int(kw.get("attempt", 0) or 0) >= max_attempts:
            return None
        msg = nudge_message(store)
        return {"action": "continue", "message": msg} if msg else None
    return pre_verify


def make_approver(ask):
    """ask(prompt) -> bool from the host's approval route; the prompt names the record and its exact argv."""
    def approve(kind, argv, cwd, rid):
        return ask(f"falsify: run the {kind} of record {rid[:12]}: {argv!r} in {cwd or '.'}?") is True
    return approve
