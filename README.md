# falsify (private, pre-release)

A Hermes Agent plugin: an agent's claim is not proved until it survives a check that could have failed.

- **Pre-register:** the claim's check and its mutant are hashed (sha256) BEFORE any run; an edit is a new record.
- **Judge:** run the check and a mutant that must FAIL. Mutant passes = VOID (the check was vacuous). Otherwise proved / disproved.
- **Evidence:** "done" only with a resolvable result; an audit demotes one whose evidence moved.

Private until the owner's yes. No secrets in this repo (it lives on an unencrypted disk).
