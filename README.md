# falsify

An agent says "fixed". falsify asks: would your check have failed without your fix?

Before the agent changes anything, it registers its claim, the command that checks it, and the base commit; the
record is hashed. After the change, falsify runs that command on the agent's tree (it must pass) and on a copy with
the agent's own change reverted (it must fail). A check that passes either way reads **VOID**, not green. falsify
records the verdict and nudges the agent before it finishes. It never blocks completion.

A plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) (>= 0.23.0), and a standalone CLI.
Python standard library only, plus `git`.

Developed in the open in [CodexOperator/AGI](https://github.com/CodexOperator/AGI) by AI agents under the owner's direction.

## Install (Hermes)

```
hermes plugins install CodexOperator/falsify --enable
```

The agent gets four tools (`falsify_register`, `falsify_judge`, `falsify_done`, `falsify_status`), a skill that tells
it when to use them, and a `pre_verify` nudge. Before any check runs, Hermes asks you to approve the exact command.

## 60-second quickstart (CLI, no Hermes needed)

```
git clone https://github.com/CodexOperator/falsify falsify_src
export PYTHONPATH=$PWD/falsify_src

git init -q calc && cd calc
printf 'def add(a, b):\n    return a - b\n' > calc.py
printf 'from calc import add\nassert add(2, 3) == 5\n' > test_add.py
git add -A && git commit -qm base

echo '{"claim": "add adds", "repo": ".", "check": ["python3", "test_add.py"]}' > ../honest.json
echo '{"claim": "add adds", "repo": ".", "check": ["python3", "-c", "pass"]}' > ../weak.json
H=$(python3 -m falsify register ../honest.json)    # BEFORE the fix: prints the record's sha
W=$(python3 -m falsify register ../weak.json)

printf 'def add(a, b):\n    return a + b\n' > calc.py   # the fix
python3 -m falsify judge "$H" --approve            # proved: covers 1 of 1 hunks
python3 -m falsify judge "$W" --approve            # void: your check passes without your change
```

`--approve` is you approving the registered command; without it (and without a terminal) nothing runs and the
verdict is VOID "not run".

## Verdicts

| verdict | when |
|---|---|
| **proved** | the check passes on the final tree and fails with the change reverted. It says how many hunks of the change the check covers (each hunk is reverted alone), and ends with the exact `falsify_done` call. Done stays the agent's own act |
| **disproved** | the check fails on the final tree |
| **VOID** "your check passes without your change" | the check passes with the change reverted: it could not have failed |
| **VOID** "unrevertable" | the revert does not apply, the reverted tree fails the optional `sanity` command, or the check dies with an exit code outside `kill_rc` (for pytest, 2-5 are collection errors, not failures) or times out |
| **VOID** "no mutant" | nothing changed since the base, and no blind mutant was registered |
| **proved / VOID** (blind) | nothing changed (docs, config, "it already works") and the claim was registered with `blind: K`: a separate model drafted K mutants without seeing the check, and one was drawn after the run |
| **VOID** "not run" | no approval, a "no", or a non-interactive caller (cron, a webhook, a CLI without a terminal). Nothing executed |

`keep` (e.g. `["tests/**"]`) leaves the agent's new test files in place on every revert, so reverting the change never
"fails" the check by deleting the test itself.

## What it does not prove

A proved claim means **this check needs this change**. It does not mean the code is correct, that the check tests the
right property, or that code the change did not touch works. A blind mutant is only as good as the model that drafted
it.

## What it does and does not do

- **Nudges and records; never blocks.** Hermes's `pre_verify` asks the agent, before it finishes, about every claim not
  done on a proved result and about changed files no claim covers. Hermes arms `pre_verify` only after `write_file` /
  `patch` edits, so edits made through the terminal do not trigger it, and Hermes caps the nudges per turn.
- **Runs only what was registered, only when approved.** The judge executes exactly the command stored in the hashed
  record, after it is approved at Hermes's approval prompt (or `--approve` on the CLI). falsify cannot tell whether
  a person or a script answered that prompt.
- **Never changes your files.** It reads your working tree through a private git index and runs checks in temporary
  copies. It keeps its own store in `.falsify/` (move it with `--store` or `FALSIFY_STORE`, or add it to `.gitignore`).
- **Keeps a ledger.** Every registration and verdict is a hash-chained line, so retrying until green shows up.

## Disclosure: it runs your commands, without a sandbox

falsify runs the registered check (and `sanity`) command as an ordinary process with your permissions, in temporary
copies of your working tree. There is no sandbox: a check that deletes files, reaches the network or reads secrets
will do so once approved. Approve only commands you would run yourself.

Network: none by default. Only if you configure a blind-mutant drafter (`draft_url`, a secret in the plugin's
settings) does falsify send it the claim and your base files, minus every file the check names or would discover.

## How it differs from proofops

proofops seals a receipt after the run. falsify commits the check's hash before the run and judges it against a
mutant derived from the agent's own change, so a check that passes without the change reads VOID, not green.

## Development

- `falsify/core.py`: register, judge, done, audit, ledger and the CLI (`python3 -m falsify --help`).
- `falsify/adapter.py`: the host seam (the `pre_verify` nudge, the approver).
- `falsify/draft.py`: the optional blind-mutant drafter (OpenAI-compatible or the Anthropic Messages API).
- `hermes_plugin/`, `plugin.yaml`, `__init__.py`: the Hermes layer, rooted at the repo.
- `demo/run_demo.py`: a weak test vs an honest test on one fix (needs pytest).
- Tests: `python3 -m unittest discover -s tests`, run from a clone whose directory name is a valid Python identifier
  (the root `__init__.py` makes the repo a package).
- `docs/design-notes.md`: blind mutants, withheld files, the commit-first rule, sessions.
- `submission/PROVED.md`: what is proved (each claim with the test or run behind it) and what is not.
- `catalog/`: the plugin-catalog entry and `fill.py`, which renders it with the repo name and a pinned commit.
