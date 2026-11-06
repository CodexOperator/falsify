"""The BLIND drafter (step 4, plasma's form): K mutants for a claim with no diff, drafted by a model that reads the
claim's words and the base tree's files, never the check. Stdlib only; any OpenAI-compatible /v1/chat/completions
endpoint (the local llama.cpp server first). falsify.core hashes what it returns into the record; it decides nothing."""
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


def prompt(claim, files, k):
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


def make_drafter(url, model, k=5, timeout=600, temperature=0.7, api_key=None, post=None):
    """-> drafter(claim, files) for falsify.core.Store. post(url, payload, headers, timeout) -> response dict is
    injectable for tests; the default is urllib."""
    def _post(u, payload, headers, t):
        req = urllib.request.Request(u, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=t) as r:
            return json.loads(r.read())

    def drafter(claim, files):
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {api_key}"} if api_key else {})}
        payload = {"model": model, "temperature": temperature,
                   "messages": [{"role": "user", "content": prompt(claim, files, k)}]}
        r = (post or _post)(url.rstrip("/") + "/chat/completions", payload, headers, timeout)
        drafter.last = r
        return parse(r["choices"][0]["message"].get("content") or "")
    drafter.last = None
    return drafter


def from_env(env=None):
    """FALSIFY_DRAFT_URL (an OpenAI-compatible base, e.g. http://127.0.0.1:8091/v1), FALSIFY_DRAFT_MODEL, FALSIFY_DRAFT_K;
    the key, if any, from FALSIFY_DRAFT_KEY in the environment only (never a file). -> a drafter, or None."""
    env = os.environ if env is None else env
    url = env.get("FALSIFY_DRAFT_URL")
    if not url:
        return None
    return make_drafter(url, env.get("FALSIFY_DRAFT_MODEL", "local"), int(env.get("FALSIFY_DRAFT_K", 5)),
                        api_key=env.get("FALSIFY_DRAFT_KEY"))
