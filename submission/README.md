<!-- DRAFT, private (thought-master order 14:58Z 11-06). Nothing here is posted until the owner's yes.
     Every number is from goal:g5.52 ONLY and carries its source path; a [PENDING] slot stays empty until
     director-thought-science's g5.52 result nodes are committed. -->
# falsify

An agent's claim ("the bug is fixed") is not done until it survives a check that could have failed.
Before changing anything, the agent pre-registers the claim, its check command and the base commit (hashed).
After the change, the check must pass on the agent's tree and fail with the agent's own change reverted;
falsify records the verdict and nudges the agent before it finishes. It never blocks completion.

## Install

Hermes Agent >= 0.23.0, Python stdlib only (git on PATH):

```
hermes plugins install CodexOperator/{REPO_NAME} --enable
```

The agent gets four tools (`falsify_register`, `falsify_judge`, `falsify_done`, `falsify_status`), a `pre_verify`
nudge and a skill. Standalone: `python -m falsify --help`.

## Verdicts

| verdict | when |
|---|---|
| proved | the check passes on the final tree and fails (exit code in the pinned `kill_rc`) with the change reverted; reports k of h hunks covered. The reply ends with the exact `falsify_done` call; done stays the agent's act |
| disproved | the check fails on the final tree |
| VOID "your check passes without your change" | the check passes with the whole change reverted: it could not have failed |
| VOID "unrevertable" | a revert that does not apply, a reverted tree that fails the `sanity` argv, a check that dies outside `kill_rc` or times out: never counted as a kill |
| VOID "no mutant" | no change since the base, no earlier base registered in this session, and no blind mutant |
| proved / VOID (blind) | no diff and `blind: K`: K mutants drafted by a separate model that never sees the check, one drawn after the run by a committed seed |
| VOID "not run" | no approval, a "no", or a non-interactive caller; nothing executed |

## What it does not prove

A proved claim means this check needs this change. It does not mean the code is correct, that the check tests the
right property, or that untouched code works. A weak blind drafter makes a weak blind judge; the record names it.

## Disclosure: it runs your commands, without a sandbox

falsify runs the registered check (and sanity) command as an ordinary subprocess with your permissions, in temporary
copies of the repo's working tree. There is no sandbox: a check that deletes files, reaches the network or reads
secrets will do so when approved. Every run first shows the exact argv at Hermes's approval prompt and waits for an answer.

## How it differs from proofops

proofops seals a receipt after the run; falsify commits the check's hash before the run and judges it against a
mutant derived from the agent's own change, so a check that passes without the change reads VOID, not green.

## Why a positive control

The suite's positive control (M-GOLD: the reference fix, with an honest check, must read PROVED) caught a bug in falsify
itself. Bytecode copied into the mutant tree outlived a same-second, same-size revert, so an honest moved-line fix read
VOID. Fixed: mutant copies carry no bytecode. Source: goal:g5.53 M-GOLD, task a08 (director-thought-science).

## Measured (goal:g5.52 only)

A price list for one easy suite, not a quality ranking: every Console arm solved all 20 tasks through a minimal
scripted loop (not an agent harness). Source for every row: agi graph posts/thought-master c0fd488d54, experiment:solved-claims-per-dollar-local-and-console-g5-52-run-1.

| arm | solved / 20 | USD | USD per solved |
|---|---|---|---|
| claude-haiku-5-5 | 20 | 0.0198 | 0.00099 |
| claude-sonnet-5-5, batch one-shot | 20 | 0.0701 | 0.0035 |
| claude-sonnet-5-5 | 20 | 0.2656 | 0.0133 |
| claude-opus-5-5 | 20 | 0.6762 | 0.0338 |
| Qwen3-4B, local | 6 | 0 | 0 |

SOLVED = falsify PROVED on the agent's own check AND a hidden test passes. Controls on the same suite: an agent that
edits nothing solves 0/20; one that only adds a passing test reads VOID on 20/20; the reference fix reads SOLVED 20/20.
