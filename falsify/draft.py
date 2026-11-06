"""The BLIND drafter (step 4, plasma's form): K mutants for a claim with no diff, drafted by a model that reads the
claim's words (view "claim") or the claim's words and the base tree's files (view "files"), never the check or a
result. Stdlib only: an OpenAI-compatible /v1/chat/completions endpoint (the local llama.cpp server) or the
Anthropic Messages API (api "anthropic", e.g. claude-haiku-5-5 through a proxy base URL). falsify.core hashes what it returns into the record; it decides nothing."""
import http.client
import json
import os
import re
import urllib.request

PROMPT = """A developer claims the following about this repository, without changing any code:

CLAIM: {claim}

Write {k} MUTANTS. A mutant is ONE small edit to ONE file below that would make the claim FALSE if it were applied:
the kind of bug a test of this claim must catch. Each edit replaces an exact snippet ("old", copied character for
character from the file, occurring exactly once in it) with a changed snippet ("new"). Make the {k} mutants different
from each other. Answer with JSON only: {{"mutants": [{{"path": "...", "old": "...", "new": "..."}}, ...]}}

FILES:
{files}"""

PROMPT_CLAIM = """A developer claims the following about a Python repository, without changing any code:

CLAIM: {claim}

You do NOT see the code. Write {k} MUTANTS: each is ONE small edit to ONE file that would make the claim FALSE, the kind
of bug a test of this claim must catch. Guess the file path and an exact snippet of its code ("old") and the changed
snippet ("new"). Make the {k} mutants different from each other. Answer with JSON only:
{{"mutants": [{{"path": "...", "old": "...", "new": "..."}}, ...]}}"""


def prompt(claim, files, k, view="files"):
    if view == "claim":
        return PROMPT_CLAIM.format(claim=claim, k=k)
    body = "\n".join(f"=== {p} ===\n{t}" for p, t in sorted(files.items()))
    return PROMPT.format(claim=claim, k=k, files=body)


def parse(text):
    """-> the list of mutants in a model's answer (the last JSON object holding "mutants"; a <think> block and code
    fences are skipped), or []."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    dec, best = json.JSONDecoder(), []
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("mutants"), list):
            best = obj["mutants"]
    return best


def make_drafter(url, model, k=5, timeout=600, temperature=0.7, api_key=None, post=None, api="openai", view="files",
                 max_tokens=16000):
    """-> drafter(claim, files) for falsify.core.Store (drafter.view is recorded with the mutants). api "openai" posts
    {url}/chat/completions; api "anthropic" posts {url}/v1/messages with x-api-key (no sampling params: current
    models refuse them). post(url, payload, headers, timeout) -> response dict is injectable; the default is urllib."""
    if view not in ("files", "claim") or api not in ("openai", "anthropic"):
        raise ValueError(f"view {view!r} / api {api!r}")
    def _post(u, payload, headers, t):
        req = urllib.request.Request(u, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=t) as r:
            try:
                return json.loads(r.read())
            except http.client.IncompleteRead as e:   # a proxy that says chunked and sends a plain body: the body is whole
                return json.loads(e.partial)

    def drafter(claim, files):
        msgs = [{"role": "user", "content": prompt(claim, files, k, view)}]
        if api == "anthropic":
            headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": api_key or ""}
            r = (post or _post)(url.rstrip("/") + "/v1/messages", {"model": model, "max_tokens": max_tokens,
                                "output_config": {"effort": "low"}, "messages": msgs}, headers, timeout)
            text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        else:
            headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {api_key}"} if api_key else {})}
            r = (post or _post)(url.rstrip("/") + "/chat/completions", {"model": model, "temperature": temperature,
                                "messages": msgs}, headers, timeout)
            text = r["choices"][0]["message"].get("content") or ""
        drafter.last = r
        return parse(text)
    drafter.last, drafter.view, drafter.model = None, view, model
    return drafter


def from_env(env=None):
    """FALSIFY_DRAFT_URL (an OpenAI-compatible base, e.g. http://127.0.0.1:8091/v1, or with FALSIFY_DRAFT_API=anthropic
    a Messages API base), FALSIFY_DRAFT_MODEL, FALSIFY_DRAFT_K, FALSIFY_DRAFT_VIEW (files | claim);
    the key, if any, from FALSIFY_DRAFT_KEY in the environment only (never a file). -> a drafter, or None."""
    env = os.environ if env is None else env
    url = env.get("FALSIFY_DRAFT_URL")
    if not url:
        return None
    return make_drafter(url, env.get("FALSIFY_DRAFT_MODEL", "local"), int(env.get("FALSIFY_DRAFT_K", 5)),
                        api_key=env.get("FALSIFY_DRAFT_KEY"), api=env.get("FALSIFY_DRAFT_API", "openai"),
                        view=env.get("FALSIFY_DRAFT_VIEW", "files"))
