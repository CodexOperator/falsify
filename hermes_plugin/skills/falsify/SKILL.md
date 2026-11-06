---
name: falsify
description: How to make a claim falsifiable before calling it done.
---
# falsify

A claim ("the bug is fixed", "the test covers X") is not done until it survives a check that COULD have failed.

1. `falsify_register` BEFORE you run anything: the claim, a check (argv that must exit 0), and a mutant: the same
   check after a one-string `patch` that breaks the thing you claim (it must FAIL). Keep the record sha.
2. `falsify_judge` with the record sha. A human approves the exact argv first; without that the result is
   VOID 'not run' and nothing executes.
3. Read the verdict: `proved` (mutant failed, check passed), `disproved` (check failed), or `void` (the mutant
   passed, so the check was vacuous, or it was not run). Only `proved` may be marked done with `falsify_done`.
4. Never edit a record to make it pass: an edit is a new record with a new sha.
