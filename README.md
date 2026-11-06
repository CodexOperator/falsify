# falsify (private, pre-release)

A Hermes Agent plugin: an agent's claim is not proved until it survives a check that could have failed.

- **Pre-register:** BEFORE changing anything, the agent registers the claim, its check (an argv: `{"argv": [...]}` or
  a bare list of strings) and the base commit. A malformed spec is refused with the expected shape and an example.
  The record is hashed (sha256); an edit is a new record.
- **Judge:** after the change, the check must PASS on the agent's final tree and FAIL with the agent's own change
  reverted. The agent never writes the mutant: it is derived from the change. Each hunk is then reverted alone, so a
  proved claim also says how much of the change its check covers ("covers k of h hunks").
- **Evidence:** "done" only on a resolvable, proved result; an audit demotes one whose evidence moved.
- **Ledger:** every registration and every judgement is a hash-chained line, so a retry-until-green run is visible:
  `done` prints "this claim: N registrations, verdicts p / d / v" and flags a re-registration after a loss.

Private until the owner's yes. No secrets in this repo (it lives on an unencrypted disk).

## How it differs from proofops

proofops seals a receipt AFTER the run and runs mutants only as an offline benchmark; falsify commits the check's hash
BEFORE the run and judges every claim against a per-claim mutant derived from the agent's own change, so a check that
passes without the change reads VOID instead of green.

## Verdicts

| verdict | when |
|---|---|
| proved | the check passes on the final tree and fails (with an exit code in the pinned `kill_rc`) with the change reverted; reports k of h hunks covered |
| disproved | the check fails on the final tree (a full result, not an error) |
| VOID "your check passes without your change" | the check passes with the whole change reverted: it could not have failed |
| VOID "unrevertable" | a revert that does not apply, a reverted tree that fails the pre-registered `sanity` argv, or a check that dies with an exit code outside `kill_rc` (pytest 2-5) or times out: never counted as a kill |
| proved (blind) | NO diff, and the claim was registered with `blind: K`: the check passes on the final tree and fails on the ONE blind mutant drawn after the run (below) |
| VOID "your check passes on the drawn blind mutant" | no diff, and the check passes with the drawn blind mutant applied |
| VOID "no mutant" | there is no change since the base (or only in `keep` paths), and no blind mutant was registered (or none survived drafting) |
| VOID "not run" | no approver, a "no", or a non-interactive caller (cron, gateway); nothing executed |

**Blind mutants, for a claim with no diff** (plasma's form; docs, config, "it already works"): `register(..., blind=K)`
asks a separate model, the DRAFTER (`falsify/draft.py`, any OpenAI-compatible endpoint: `FALSIFY_DRAFT_URL`,
`FALSIFY_DRAFT_MODEL`; under Hermes, the URL and key are config_schema secrets read only through Hermes's secret
scope, never `os.environ`), for K edits that would make the claim false. It reads the claim's words and the base tree's
files, never the check. Each edit is `{path, old, new}` (one exact occurrence). The valid ones are hashed into the record
with the sha256 of a fresh seed. At judge, with no diff, the seed must hash to its commit, and mutant
`sha256(seed + record sha) mod K` is drawn. The agent never writes, sees in advance, or picks the mutant it is judged
against. The default VIEW is `files` (thought-master's ruling 14:49Z 11-06, measured below): a deviation from plasma's
"never the code", for a reason that holds only in this case. With NO diff, the base files are not the agent's work, so
they leak nothing about the claim being judged. The check and every result stay hidden, and that hidden part is what
guards against a mutant tailored to the check. `view=claim` (the claim's words only) stays available.
Every file the check argv, the sanity argv or a `keep` glob names is withheld from the drafter (a directory token
withholds everything under it; `.` withholds the whole tree), and the record lists `withheld` and the size-cap `cut`.
A check that runs a test runner with NO path argument (`python -m pytest -q`, `python -m unittest`, `./run_tests`)
also withholds what that runner would discover: pytest's `test_*.py`, `*_test.py`, `conftest.py` plus `testpaths` /
`python_files` from `pytest.ini`, `tox.ini`, `setup.cfg` or `pyproject.toml`; unittest's `test*.py`; both for any other
runner. Not withheld: tests a custom runner finds by other names, so name them in the argv. A commit-first dodge (commit the change, register at HEAD, no diff) is judged from this session's earliest
registered ancestor base IN THE SAME SESSION (Hermes's session_id, else FALSIFY_SESSION, else one per process; it is
logged in the hash-chained ledger), with the derived mutant, before any blind draw. It never widens into an older session. Without a drafter, `blind` is refused; with a diff, the derived mutant wins and the blind ones are not used.
Measured on the local Qwen3-4B (`demo/run_blind_live.py`, K 5, the fixture with no diff): the honest check PROVED (it
kills 5 of 5 kept mutants), the weak, mocking check VOID (it kills 1 of 5: only the one in `cart.py`, the code it
does not mock); 5 of 5 drafts valid per claim, 213 s and about 12k completion tokens per draft.
On the Console (claude-haiku-5-5, effort low, K 5, same claim), two drafter VIEWS: `files` (claim + base files): honest
PROVED (kills 4 of 5), weak VOID (kills 1 of 5), 747 in / 1884 out tokens per draft. `claim` (the claim's words only):
5 drafted, 0 applicable (it must guess the code), so both read VOID "no mutant". Total $0.004. The record keeps the
drafter's `view` and `model`.
Not proved: that the drafter's mutants are the ones that matter. A weak drafter makes a weak judge, and the record
keeps `asked` / `drafted` / kept so that is visible.

`keep` (pre-registered globs, e.g. `tests/**`) leaves those paths' changes in place on every revert, so reverting the
change never "kills" the check by deleting the check's own new test.

## What it does not prove

It tests the agent's own check against the agent's own change, nothing wider: a proved claim means this check needs
this change, not that the code is correct, that the check tests the right property, or that untouched code works.

## Layout

- `falsify/core.py`: register / judge / done / audit / ledger, and the CLI (`python -m falsify --help`). Pure stdlib + git.
- `falsify/adapter.py`: the seam a host agent uses: `make_pre_verify(store)` (a `pre_verify` callback returning
  `{"action": "continue", "message"}` while any claim is not done on a proved result, or while the turn's
  `changed_paths` include a path no open claim's repo contains, else `None`), `make_approver(ask)` (wraps the host's
  yes/no approval route), `nudge_message(store, changed_paths=())`, `uncovered(store, changed_paths)`.
- `hermes_plugin/`, `plugin.yaml`, `__init__.py`: the Hermes layer (rooted at the repo).
- `demo/`: the hackathon story (`python demo/run_demo.py`; needs pytest).
- `tests/`: `python3 -m unittest discover -s tests` (stdlib unittest; includes a planted tautological check that must
  read VOID). Run from a clone whose directory name is a valid Python identifier (the root `__init__.py` makes the
  repo a package).

## What it will and will not do

- **It runs only what was pre-registered, and only when approved.** The judge executes exactly the argv stored in the
  hashed record (the check, and the optional sanity argv), and asks the approver for each BEFORE anything runs. No
  approver, a "no", or a non-interactive caller (cron, gateway) records VOID "not run" and executes nothing.
- **It never blocks completion.** It records each claim as proved, disproved or VOID and nudges the agent through
  `pre_verify` before it finishes; the host decides what happens next.
- **It never writes your repo.** The final tree is read through a private git index (your index and history are
  untouched; bytecode left by a test run is excluded); every run happens in a temporary copy.

## Disclosed risk: no sandbox

falsify runs the user's own commands (the registered check, and the sanity argv if any) as ordinary subprocesses, with
the user's permissions, in temporary copies of the repo's working tree. There is no sandbox: a check that deletes
files, reaches the network or reads secrets will do so when approved. Register and approve only commands you would run
yourself; do not point it at claims from untrusted sources.
