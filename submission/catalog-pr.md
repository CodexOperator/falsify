<!-- DRAFT, private: never opened as a PR until the owner's yes. the repo is CodexOperator/{REPO_NAME}; render
     catalog/falsify.yaml with catalog/fill.py <repo name> <sha> once the owner names the repo. -->
# Add falsify to the plugin catalog

**What it is.** A Hermes Agent plugin that makes a coding claim falsifiable. The agent pre-registers a claim, its check
command and the base commit before changing anything. After the change, falsify judges the check against the agent's
final tree (it must pass) and against copies with the agent's own change reverted, whole and hunk by hunk (it must
fail). It records proved / disproved / VOID and nudges through `pre_verify`; it never blocks completion.

**Surface.** Tools `falsify_register`, `falsify_judge`, `falsify_done`, `falsify_status`; hooks `pre_verify`,
`pre_tool_call`, `post_approval_response`; one skill. `python_dependencies: []` (stdlib), `requires_hermes >=0.23.0`.

## Disclosures (rule 13)

- **Shell runs.** `falsify_judge` RUNS the check command the agent registered (and its optional `sanity` command) as
  ordinary subprocesses with the user's permissions, on the final tree and on each reverted copy. There is NO sandbox:
  a check that deletes files, reaches the network or reads secrets does so when approved.
- **Approval per run.** Before anything runs, `pre_tool_call` returns `approve` with the exact argv and the record's
  hash, so Hermes's own human gate asks. Only a human `once` / `session` / `always` answer grants ONE run. Under cron,
  gateway auto-approve, yolo or any run no human answered, the judge records VOID "not run" and executes nothing.
- **Reads of the code under test.** The final tree is read through a private git index (`git add -A` into a temp
  index); the repo's own index, history and working tree are never written.
- **The temp copy.** Every run happens in a temporary copy of the working tree (`.git` excluded, ignored files
  included), deleted afterwards.
- **Network.** None by default: core and the plugin make no network call. OPT-IN only: if the user sets
  `FALSIFY_DRAFT_URL` and registers a claim with `blind: K`, falsify sends the claim's words and the base tree's text
  files (minus every file the check names or would discover) to that endpoint to draft K blind mutants.
- **Credentials.** None by default. Under Hermes, the opt-in drafter's URL and key are `config_schema` secrets
  (`FALSIFY_DRAFT_URL`, `FALSIFY_DRAFT_KEY`) read ONLY through `agent.secret_scope.get_secret`; the plugin never reads
  `os.environ`, `.env` or any credential file (rule 11). An unscoped read fails closed (no drafter). The key is sent
  only to that URL and never written. Neither is in `requires_env`, so an unset key never disables the plugin.
  The standalone CLI (`python -m falsify`) reads the same names from its own environment.
- **Storage.** Records, results, seeds and a hash-chained ledger under the plugin's own data dir
  (`ctx.state.data_dir/store`), created on first use; `register()` writes nothing.
- **No background process**, no wrap or rebind of the agent's finish.

## Validation

`python3 -m unittest discover -s tests` (stdlib). The catalog entry, rendered by `catalog/fill.py`, passes `scripts/validate_plugin_catalog.py`
(re-run at the pinned sha); `hermes plugins validate` passes.
