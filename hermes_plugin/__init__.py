"""falsify for Hermes Agent: the THIN layer over falsify.adapter (module boundary agreed with director-thought-1:
this package imports falsify.adapter + falsify.Store only and never calls subprocess).

Seams (hermes-agent ca43606920, all public; rule 9: nothing wraps or rebinds AIAgent's finish):
- pre_verify (agent/turn_stop_gates.py:69): falsify.adapter.make_pre_verify -> a NUDGE naming open claims; it never blocks.
- pre_tool_call (hermes_cli/plugins.py:1987-2016): for falsify_judge only, returns {"action": "approve"} naming the
  record's exact check and mutant argv, so Hermes's own human gate asks (tools/approval.py:1208); it fails closed when
  no human is present (approval.py:1231).
- post_approval_response (observer, plugins.py:144-147; fired at approval.py:996 and approval_prompt.py:252/375):
  a human 'once'/'session'/'always' for rule plugin_rule:falsify:<record sha> becomes a ONE-SHOT grant.
- falsify_judge's handler passes falsify.adapter.make_approver a yes only when such a grant exists. A run Hermes
  approved WITHOUT asking (approvals.cron_mode / unattended_mode: approve, a cached session/always answer, yolo)
  fires no response hook, so it judges VOID 'not run' and executes nothing (R1, rule 12)."""
import json
import os
import threading

from ..falsify import Refused, Store, adapter

RULE_PREFIX = "falsify:"
HUMAN_YES = frozenset({"once", "session", "always"})


class Grants:
    """One-shot human approvals, keyed by record sha; filled ONLY by the post_approval_response observer."""

    def __init__(self):
        self._lock, self._g = threading.Lock(), {}

    def observe(self, pattern_key="", choice="", **_):
        pk = str(pattern_key or "")
        if not pk.startswith("plugin_rule:" + RULE_PREFIX):
            return None
        rid = pk[len("plugin_rule:" + RULE_PREFIX):]
        with self._lock:
            if choice in HUMAN_YES:
                self._g[rid] = choice
            else:
                self._g.pop(rid, None)
        return None

    def take(self, rid):
        with self._lock:
            return self._g.pop(rid, None)


def _json(obj):
    return json.dumps(obj, sort_keys=True)


def _record_or_none(store, rid):
    rec, _ = store.load("records", rid)
    return rec


def approval_message(rec, rid):
    chk, mut = rec["check"], rec["mutant"]
    patch = f" after patching {mut['patch']['path']}" if mut.get("patch") else ""
    return (f"falsify will RUN two commands for claim {rec['claim'][:120]!r} (record {rid[:12]}): "
            f"the MUTANT {mut['argv']!r}{patch} (must FAIL) and the CHECK {chk['argv']!r} in "
            f"{chk.get('cwd') or '.'}, with your permissions and no sandbox.")


def make_pre_tool_call(store):
    def pre_tool_call(tool_name="", args=None, **_):
        if tool_name != "falsify_judge" or not isinstance(args, dict):
            return None
        rid = str(args.get("record", ""))
        rec = _record_or_none(store, rid)
        if rec is None:
            return None  # the handler will judge it VOID (unresolvable); nothing runs
        return {"action": "approve", "message": approval_message(rec, rid), "rule_key": RULE_PREFIX + rid}
    return pre_tool_call


def make_tools(store, grants):
    def register(args, **_):
        try:
            rid = store.register(args.get("claim"), args.get("check"), args.get("mutant"), args.get("note", ""))
        except Refused as e:
            return _json({"error": str(e)})
        return _json({"record": rid})

    def judge(args, **_):
        rid = str(args.get("record", ""))
        grant = grants.take(rid)
        approve = adapter.make_approver(lambda prompt: True) if grant else None
        result_id, res = store.judge(rid, approve=approve, interactive=grant is not None)
        return _json(dict(res, result=result_id))

    def done(args, **_):
        try:
            return _json({"done": store.done(args.get("claim"), args.get("result"))})
        except Refused as e:
            return _json({"error": str(e)})

    def status(args, **_):
        return _json({"open": [{"record": r, "claim": c, "verdict": v, "why": w} for r, c, v, w in store.open_claims()],
                      "audit": [{"line": ln, "reason": why} for ln, why in store.audit()]})

    argv = {"type": "object", "properties": {
        "argv": {"type": "array", "items": {"type": "string"}}, "cwd": {"type": "string"},
        "expect_rc": {"type": "integer"}, "timeout_s": {"type": "number"},
        "patch": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                                   "new": {"type": "string"}}}}, "required": ["argv"]}

    def schema(name, desc, props, req):
        return {"name": name, "description": desc,
                "parameters": {"type": "object", "properties": props, "required": req}}

    return [
        ("falsify_register", schema("falsify_register",
            "Pre-register a claim: its check (argv that must pass) and a mutant (argv, optionally after a one-string "
            "patch, that must FAIL). Hashed before any run; returns the record sha. Runs nothing.",
            {"claim": {"type": "string"}, "check": argv, "mutant": argv, "note": {"type": "string"}},
            ["claim", "check", "mutant"]), register),
        ("falsify_judge", schema("falsify_judge",
            "Judge a registered claim: after a HUMAN approves the exact argv, run the mutant (must fail) then the "
            "check. proved / disproved / VOID (vacuous, or not run). Unapproved = VOID 'not run', nothing runs.",
            {"record": {"type": "string"}}, ["record"]), judge),
        ("falsify_done", schema("falsify_done",
            "Mark a claim DONE on a proved result (refused otherwise).",
            {"claim": {"type": "string"}, "result": {"type": "string"}}, ["claim", "result"]), done),
        ("falsify_status", schema("falsify_status",
            "List open claims (not done on a proved result) and any done line whose evidence no longer resolves.",
            {}, []), status),
    ]


class LazyStore:
    """The Store, created on FIRST USE under this plugin's own data dir (ctx.state.data_dir, profile-scoped):
    register() writes nothing (Hermes's capability probe runs it in isolation), and there is no fallback
    path outside the plugin's data."""

    def __init__(self, ctx):
        self._ctx, self._store, self._lock = ctx, None, threading.Lock()

    def __getattr__(self, name):
        with self._lock:
            if self._store is None:
                self._store = Store(str(self._ctx.state.data_dir / "store"))
        return getattr(self._store, name)


def register(ctx):
    store, grants = LazyStore(ctx), Grants()
    for name, schema, handler in make_tools(store, grants):
        ctx.register_tool(name=name, toolset="falsify", schema=schema, handler=handler,
                          description=schema["description"])
    ctx.register_hook("pre_verify", adapter.make_pre_verify(store))
    ctx.register_hook("pre_tool_call", make_pre_tool_call(store))
    ctx.register_hook("post_approval_response", grants.observe)
    skill = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills", "falsify", "SKILL.md")
    if os.path.exists(skill):
        ctx.register_skill("falsify", skill, description="How to make a claim falsifiable before calling it done.")
