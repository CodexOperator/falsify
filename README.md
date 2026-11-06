# falsify (private, pre-release)

A Hermes Agent plugin: an agent's claim is not proved until it survives a check that could have failed.

- **Pre-register:** BEFORE changing anything, the agent registers the claim, its check (an argv) and the base commit.
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
| VOID "no mutant" | there is no change since the base (or only in `keep` paths). A claim with no diff (docs, config) has no derived mutant, and v2 does not draft one: it reads VOID. |
| VOID "not run" | no approver, a "no", or a non-interactive caller (cron, gateway); nothing executed |

`keep` (pre-registered globs, e.g. `tests/**`) leaves those paths' changes in place on every revert, so reverting the
change never "kills" the check by deleting the check's own new test.

## What it does not prove

It tests the agent's own check against the agent's own change, nothing wider: a proved claim means this check needs
this change, not that the code is correct, that the check tests the right property, or that untouched code works.

## Layout

- `falsify/core.py`: register / judge / done / audit / ledger, and the CLI (`python -m falsify --help`). Pure stdlib + git.
- `falsify/adapter.py`: the seam a host agent uses: `make_pre_verify(store)` (a `pre_verify` callback returning
  `{"action": "continue", "message"}` while any claim is not done on a proved result, else `None`), `make_approver(ask)`
  (wraps the host's yes/no approval route), `nudge_message(store)`.
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
