---
name: falsify
description: How to make a claim falsifiable before calling it done.
---
# falsify

A claim ("the bug is fixed", "the test covers X") is not done until it survives a check that COULD have failed.

1. `falsify_register` BEFORE you change anything: the claim, the git repo, a check (argv that must exit 0, e.g.
   `{"argv": ["python", "-m", "pytest", "-q", "tests/test_cart.py"]}` or just the list; cwd relative to the repo) and the base commit (default HEAD). Keep the record sha. You do NOT write a mutant:
   falsify derives it from your own change, reverted.
   If you will add the check's own test file in the change, pass `keep` (e.g. `["tests/**"]`) so reverting your
   change never deletes the test itself. For pytest, `kill_rc` is `[1]` (the default): rc 2-5 are collection or
   usage errors, never kills. An optional `sanity` argv (e.g. an import) must pass on every reverted tree.
2. Make the change, then `falsify_judge` with the record sha. The user approves the exact argv at Hermes's approval prompt first; without that the result is
   VOID 'not run' and nothing executes.
3. Read the verdict: `proved` (the check passes on your change and fails with it reverted; it says how many of your
   hunks the check covers), `disproved` (the check fails on your change), or `void` (the check passes without your
   change, there is no change, or it was not run). Only `proved` may be marked done with `falsify_done`, and a
   proved claim stays OPEN until you make that call yourself: the judge's reply ends with the exact call.
4. Never edit a record to make it pass: an edit is a new record with a new sha.
5. If you changed files and no open claim covers them, falsify will say so before you finish: register a check that
   fails without your change, or say plainly that the change is unverified.
