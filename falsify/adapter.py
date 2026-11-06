"""The thin seam between falsify's core and a host agent (Hermes, first): R2, rule 9. falsify never wraps or blocks
the agent's finish; it answers the host's public pre_verify hook with a NUDGE naming every claim that is not done on a
proved result and every changed path no open claim covers, and otherwise lets the turn finish. Approval (R1) is the host's to supply: make_approver adapts any
yes/no callable, and nothing here runs a check by itself."""
import os

from . import core as F  # noqa: F401


def uncovered(store, changed_paths, cwd=None):
    """-> the changed paths that NO open claim covers, in the host's spelling. A record covers a path when the path
    lies inside the record's repo and the record is open (registered, not done on a proved result); a relative path
    is read against cwd (default: this process's)."""
    repos = []
    for rid, _, _, _ in store.open_claims():
        rec, _ = store.load("records", rid)
        if rec:
            repos.append(os.path.realpath(rec["repo"]))
    out = []
    for p in changed_paths or ():
        rp = os.path.realpath(os.path.join(cwd or os.getcwd(), str(p)))
        if not any(rp == r or rp.startswith(r + os.sep) for r in repos):
            out.append(str(p))
    return out


def nudge_message(store, limit=5, changed_paths=(), cwd=None):
    """-> the nudge text, or None when every registered claim is done on a proved result and every changed path is
    covered by an open claim."""
    parts = []
    bare = uncovered(store, changed_paths, cwd)
    if bare:
        more = f" and {len(bare) - limit} more" if len(bare) > limit else ""
        parts.append(f"falsify: you changed {', '.join(bare[:limit])}{more} with no falsify claim: register a check "
                     f"that fails without your change.")
    rows = store.open_claims()
    if rows:
        lines = [f"- {(c or '(unreadable record)')[:120]} [{rid[:12]}]: {v} ({why})" for rid, c, v, why in rows[:limit]]
        more = f"\n... and {len(rows) - limit} more" if len(rows) > limit else ""
        parts.append(f"falsify: {len(rows)} claim(s) are not done on a proved result. Each needs its pre-registered check "
                     f"to pass while its mutant fails; VOID means the check could not have failed or was not run.\n"
                     + "\n".join(lines) + more + "\nJudge them (with approval), or say plainly that they are not done.")
    return "\n".join(parts) or None


def make_pre_verify(store, max_attempts=1):
    """A pre_verify callback: kwargs as the host passes them (session_id, platform, model, coding, attempt,
    final_response, changed_paths, ...); returns {"action": "continue", "message"} or None. A turn that changed files
    no open claim covers is nudged too. Self-throttled by attempt (the host also caps it by max_verify_nudges)."""
    def pre_verify(**kw):
        if int(kw.get("attempt", 0) or 0) >= max_attempts:
            return None
        msg = nudge_message(store, changed_paths=kw.get("changed_paths") or ())
        return {"action": "continue", "message": msg} if msg else None
    return pre_verify


def make_approver(ask):
    """ask(prompt) -> bool from the host's approval route; the prompt names the record and its exact argv."""
    def approve(kind, argv, cwd, rid):
        return ask(f"falsify: run the {kind} of record {rid[:12]}: {argv!r} in {cwd or '.'}?") is True
    return approve
