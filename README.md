# falsify (private, pre-release)

A Hermes Agent plugin: an agent's claim is not proved until it survives a check that could have failed.

- **Pre-register:** the claim's check and its mutant are hashed (sha256) BEFORE any run; an edit is a new record.
- **Judge:** run the check and a mutant that must FAIL. Mutant passes = VOID (the check was vacuous). Otherwise proved / disproved.
- **Evidence:** "done" only with a resolvable result; an audit demotes one whose evidence moved.

Private until the owner's yes. No secrets in this repo (it lives on an unencrypted disk).

## Layout

- `falsify/core.py`: register / judge / done / audit, and the CLI (`python -m falsify --help`). Pure stdlib.
- `falsify/adapter.py`: the one seam a host agent uses: `make_pre_verify(store)` (a `pre_verify` callback returning
  `{"action": "continue", "message"}` while any claim is not done on a proved result, else `None`), `make_approver(ask)`
  (wraps the host's yes/no approval route), `nudge_message(store)`.
- `tests/`: `python3 -m unittest discover -s tests` (stdlib unittest; includes a planted tautological check that must read VOID).

## What it will and will not do

- **It runs only what was pre-registered, and only when approved.** The judge executes exactly the argv stored in the
  hashed record, for the check and for its mutant, and asks the approver for both BEFORE either runs. No approver, a
  "no", or a non-interactive caller (cron, gateway) records VOID "not run" and executes nothing.
- **It never blocks completion.** It records each claim as proved, disproved or VOID and nudges the agent through
  `pre_verify` before it finishes; the host decides what happens next.

## Disclosed risk: no sandbox

falsify runs the user's own commands (the check and its mutant) as ordinary subprocesses, with the user's permissions,
in the check's working directory (the mutant in a temporary copy of it). There is no sandbox: a check that deletes
files, reaches the network or reads secrets will do so when approved. Register and approve only commands you would run
yourself; do not point it at claims from untrusted sources.
