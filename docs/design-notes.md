# falsify: design notes

Maintainer notes moved out of the README (the README is for people installing falsify). Every rule here is
covered by tests/.

**Blind mutants, for a claim with no diff** (docs, config, "it already works"): `register(..., blind=K)`
asks a separate model, the DRAFTER (`falsify/draft.py`, any OpenAI-compatible endpoint: `FALSIFY_DRAFT_URL`,
`FALSIFY_DRAFT_MODEL`; under Hermes, the URL and key are config_schema secrets read only through Hermes's secret
scope, never `os.environ`), for K edits that would make the claim false. It reads the claim's words and the base tree's
files, never the check. Each edit is `{path, old, new}` (one exact occurrence). The valid ones are hashed into the record
with the sha256 of a fresh seed. At judge, with no diff, the seed must hash to its commit, and mutant
`sha256(seed + record sha) mod K` is drawn. The agent never writes, sees in advance, or picks the mutant it is judged
against. The default VIEW is `files`, a deliberate exception to "the drafter never sees the code", for a reason that holds
only in this case. With NO diff, the base files are not the agent's work, so
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
Measured: with the claim's words alone, a drafter wrote edits that did not apply to the real code (it has to guess
the code); with the base files it wrote applicable mutants that the honest check killed and the weak check did not.
Not proved: that the drafter's mutants are the ones that matter. A weak drafter makes a weak judge, and the record
keeps `asked` / `drafted` / kept so that is visible.

`keep` (pre-registered globs, e.g. `tests/**`) leaves those paths' changes in place on every revert, so reverting the
change never "kills" the check by deleting the check's own new test.
