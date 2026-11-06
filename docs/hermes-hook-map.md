# Hermes hook map for the "falsify" plugin

Source: `/fast/director-thought-science/hermes-agent` @ `ca4360692055bdb4df3a695c164646e528891da8` (read-only). All cites are `path:line` at that sha.

## 1. Hook map

Dispatcher: `invoke_hook(name, **kw)` → `hermes_cli/plugins.py:1800`, body `hermes_cli/plugins_dispatch.py:275-326`. Each callback is isolated: an exception is reported and skipped (`plugins_dispatch.py:318-323`); only `pre_tool_call` fails closed (`plugins_dispatch.py:58`, `:314-316`, `:322-323`). Hooks in `_HOOK_TIMEOUT_BOUNDED_HOOKS` (`plugins_dispatch.py:42-50`, incl. `pre_verify`, `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`, `transform_llm_output`) run under `plugins.hook_callback_timeout` (default 30 s, max 600 s: `plugins_dispatch.py:183-184`). On timeout the worker is abandoned and the result skipped (fail-open). Under a safe-worker policy, every hook returns `[]` (`plugins_dispatch.py:287-288`). Approval hooks are deliberately not timeout-bounded (`plugins_dispatch.py:35-37`).

| Hook | Fired at | Kwargs | Return contract | Failure mode |
|---|---|---|---|---|
| `pre_verify` | `agent/turn_stop_gates.py:69` (`_pre_verify_nudge`), via `hermes_cli/plugins.py:2037` | `session_id, platform, model, coding, attempt, final_response, changed_paths` (`plugins.py:2038-2039`) + `telemetry_schema_version` | `{"action":"continue","message":str}` or CC-Stop `{"decision":"block","reason":str}` → keep going; first non-empty message wins; anything else → finish (`plugins.py:2041-2047`) | bounded, fail-open (exception logged at debug, `turn_stop_gates.py:75-76`) |
| `pre_approval_request` | CLI `tools/approval.py:993`; `tools/approval_prompt.py:242,362`; gateway `tools/approval_gateway_wait.py:117,236` | `command, description, pattern_key, pattern_keys, session_key, surface` (+`turn_id, tool_call_id, session_id`: `tools/approval_context.py:66-69`) | ignored, observer-only: "cannot veto or pre-answer" (`plugins.py:144-146`; docs `website/docs/user-guide/features/hooks.md:1476`) | dispatch never raises (`approval_context.py:54-73`) |
| `post_approval_response` | `approval.py:996`; `approval_prompt.py:252,375`; `approval_gateway_wait.py:97,244` | same + `choice` (`once/session/always/deny/timeout/cancelled/notify_failed/smart_approve/smart_deny`), `decided_by` | ignored | same |
| `on_session_start` | `agent/conversation_loop.py:933-936`, inside `_restore_or_build_system_prompt` (`:802`): only on a fresh prompt build (new session), and not for persistence-disabled forks (`:930`) | `session_id, model, platform` | ignored | try/except warning (`:937-938`), bounded |
| `pre_llm_call` (each turn; `is_first_turn` marks the first) | `agent/turn_context.py:862-875` | `session_id, task_id, turn_id, user_message, conversation_history, is_first_turn, model, platform, parent_session_id, sender_id` | `{"context":str}` or a string → injected into the user message; `{"action":"block","message"}` → ends the turn before inference (`turn_context.py:888-903`; `plugins_dispatch.py:282-283`) | bounded, fail-open |
| `post_llm_call` (turn end) | `agent/turn_finalizer.py:494-505`, after `transform_llm_output` (`:480-482`) | `session_id, task_id, turn_id, user_message, assistant_response, conversation_history, model, platform, side_question_token` | ignored (observer) | `_invoke_hook_safely` |
| `on_session_end` (per `run_conversation()` call, not per session) | `agent/turn_finalizer.py:885-897`; CLI quit mid-turn `hermes_cli/cli_shutdown.py:199-200` | `session_id, task_id, turn_id, completed, failed, interrupted, turn_exit_reason, model, platform` | ignored | safe |
| `on_session_finalize` (real session end) | `hermes_cli/cli_session_mixin.py:578`, `hermes_cli/lifecycle.py:83,97` | `session_id` | ignored | — |
| `transform_llm_output` (relevant to item 8) | `agent/turn_finalizer.py:509-547`, called from `agent/turn_final_response.py:390-393` before the final row is persisted | `response_text, session_id, model, platform, turn_id` | first non-empty `str` replaces the final answer (`turn_finalizer.py:541-545`); `hold_stream=True` holds streaming until the rewrite (`plugins_dispatch.py:75,567-578`) | bounded, fail-open |

### pre_verify: what triggers it, exactly
- Call chain: `agent/turn_final_response.py:375` `apply_stop_gates(...)` → `agent/turn_stop_gates.py:134`. It runs only when the model stops with a text answer (no tool calls).
- Gate order (`turn_stop_gates.py:139`, `:160-221`): (1) late LSP verdicts, (2) verify-on-stop (`agent.verify_on_stop`, default `False`, `hermes_cli/config_defaults.py:209`), (3) **pre_verify**, (4) the kanban terminal-tool guard. The first gate that nudges wins, and the loop re-enters.
- The pre_verify condition (`turn_stop_gates.py:56,62`): `_edited and has_hook("pre_verify") and attempt < max_verify_nudges()`.
  - `_edited` is the sorted `agent._turn_file_mutation_paths`, filled ONLY by landed `write_file` / `patch` calls (`agent/tool_result_classification.py:10`; recorded at `agent/turn_explainers.py:125,133-137`) and reset each turn (`agent/turn_context.py:630`). **Edits made through `terminal` (sed, git apply, codegen) do NOT arm pre_verify.**
  - `coding` is a kwarg only. It is not a gate: computed once by `is_coding_context(platform)` (`turn_stop_gates.py:64-67`). The hook must scope itself.
- How many times: once per text-stop while `attempt < agent.max_verify_nudges` (default 3: `hermes_cli/config_defaults.py:203`; read at `agent/verify_hooks.py:29-33`, fallback `DEFAULT_MAX_VERIFY_NUDGES=3` at `:16`). The counter `_pre_verify_nudges` is incremented at `turn_stop_gates.py:193` and reset per turn (`turn_context.py:621`). So it can fire up to 3 times per turn by default, and always at least once if code was edited. (The "once per turn" comment at `plugins.py:116` is loose. The docs say to self-throttle on `attempt`: `hooks.md:869`.)
- What happens to the returned message: the attempted answer gets `finish_reason="verify_hook_continue"`, is emitted as an INTERIM assistant message and flushed to the DB (`turn_stop_gates.py:194-197`, `:119-131`). The message is appended as a synthetic user row flagged `_pre_verify_synthetic` (`:198`, `:145`), and the turn continues with `final_response=None`. The candidate is kept as `pending_verification_response`, the budget-exhaustion fallback (`:150-157`). Synthetic nudges are stripped from history later (`agent/turn_finalizer.py:25-28`). Caveat: the docs claim the attempted answer is "not surfaced to the user" (`hooks.md:867`), but the code emits it as an interim message unless it is a hidden held row (`turn_stop_gates.py:121-125`). The docs also say it fires "in agent/conversation_loop.py" (`hooks.md:857`), which is stale.
- Once the cap is hit, pre_verify is no longer consulted and the turn finishes with whatever the model said.
- Shell-hook form: `_parse_pre_verify` (`agent/shell_hooks.py:661-667`).

## 2. Approvals

- **A plugin cannot request approval for an arbitrary argv directly.** The public route is a `pre_tool_call` directive `{"action":"approve","message":..., "rule_key":...}` (`hooks.md:568-575`), resolved in `hermes_cli/plugins.py:1987-2016` → `tools.approval.request_tool_approval` (`tools/approval.py:1208-1235`). That is the same human gate used for dangerous shell patterns. Deny, timeout or a gate error blocks the tool (fail-closed, `plugins.py:2010-2015`). It applies to plugin tools too: `pre_tool_call` fires for every tool in `model_tools.py:778-800` (`_pre_dispatch_guards`).
  - The approval prompt shows `display_target="<tool_name> (plugin approval rule)"` plus the `message` (`approval.py:1226-1228`). **The argv itself is shown only if the plugin puts it in `message`.**
  - The allowlist key is `plugin_rule:{rule_key}` (`approval.py:1226`). A session/always answer short-circuits later calls with the same key (`approval.py:1074-1076`). **For falsify, set `rule_key` to include the record hash**, so an "always" cannot pre-approve future records.
  - This gate does NOT go through the smart (LLM guardian) step: `_run_approval_gate` calls `_human_decision` without `smart` (`approval.py:1112-1115`; default `smart=False` at `:879`). Only the terminal guard passes `smart=approval_mode=="smart"` (`approval.py:1281-1285`).
  - Importing `tools.approval.request_tool_approval` directly is not public API. `hermes_cli/data/plugin_api.json:92-93` lists only `resolve_gateway_approval[_card]`, and private imports get an admission warning (`hermes_cli/plugin_validate_private_imports.py:1-8`).
  - `ctx.register_approval_transport(name, present_fn)` (`plugins.py:438-462`) supplies a UI transport only. Policy stays with the host.
- **Observe/veto:** `pre_approval_request`/`post_approval_response` are observers only (`plugins.py:144-146`). Veto is via `pre_tool_call` `block` (`hooks.md:568-575`; precedence block > approve).
- **Bypass order in `_run_approval_gate`** (`approval.py:1048-1115`):
  - yolo or `approvals.mode: off` → approved (`:1072-1073`)
  - session cache hit → approved (`:1074-1076`)
  - otherwise presence is checked with `_presence()` (`:1013-1034`). Cron and single-query clear the cli/gateway/ask flags (`:1031-1032`).
- **Unattended contexts** (`approval.py:674-690`): single-query (`-q`), cron (`HERMES_CRON_SESSION`, `tools/approval_context.py:167-169`), and unattended platforms `webhook`, `msgraph_webhook`, `api_server` (`approval_context.py:175-186`).
  - A context in `deny` mode → blocked (`approval.py:1085-1095`).
  - `single_query_mode: approve` → approved (`:1096-1101`).
  - `cron_mode: approve` / `unattended_mode: approve` → **auto-approved even for plugin `approve` rules** (the `break` at `:1102` skips the fail-closed `else`, then `:1110-1112`).
  - No context and no human: plugin rules fail closed (`fail_closed_when_no_human=True`, `approval.py:1231`, `:1103-1109`).
- **Config defaults** (`hermes_cli/config_defaults.py:1650-1676`): `approvals.mode: smart`, `timeout: 300`, `cron_mode: deny`, `single_query_mode: deny`, `unattended_mode: deny`, `deny: []` (never-bypassable globs, `approval.py:1144-1153`).
- **Messaging gateway** (Telegram, Discord, …) counts as a human surface (`is_gateway`). The approval goes out as a card or `/approve` with a timeout; silence = deny (`approval.py:964-966`).
- **Falsify consequence:** the judge must refuse by itself under cron / gateway / non-interactive. Do not rely on `approvals.*_mode`, because the user can set it to approve. Rule 12 forbids routing around approvals and requires a clean fail or timeout under unattended runs (`plugin-catalog/README.md:108-113`).
- `ctx.dispatch_tool("terminal", ...)` (`plugins.py:678-687`) goes to `registry.dispatch` (`tools/registry.py:972`). I did not verify whether that path fires `pre_tool_call`. The terminal handler's own guard (`tools/terminal_tool.py:141`, `check_all_command_guards` at `approval.py:1238`) prompts only for DANGEROUS patterns: a plain `pytest` argv would run unprompted.

## 3. Registration

- **Plugin layout:** `<dir>/plugin.yaml` + `__init__.py` with `register(ctx)` (`hermes_cli/plugins.py:7-8`). Optional `skills/<name>/SKILL.md` (`website/docs/developer-guide/plugins/index.md:1180-1199`). Example: `plugins/disk-cleanup/{plugin.yaml,__init__.py}`, register at `plugins/disk-cleanup/__init__.py:231-236`.
- **Tool:** `ctx.register_tool(name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", override=False)` (`plugins.py:464-499`). Overriding a built-in needs `plugins.entries.<id>.allow_tool_override` / `tools.override` (`:470-478`), and a name collision without override is refused (`:482-485`).
  - Schema shape is OpenAI function style: `{"name","description","parameters":{"type":"object","properties":{...},"required":[...]}}` (`developer-guide/plugins/index.md:748-766`).
  - Handler is `def h(args: dict, **kwargs) -> str` (JSON). It must never raise (`index.md:884-891`).
- **Hook:** `ctx.register_hook(name, cb, *, hold_stream=False)` (`plugins.py:935-937`). An unknown name warns but is stored (`plugins_dispatch.py:567-573` → `plugins.py:951-952`).
- **Skill:** `register_skill(name, path, description="", frontmatter=None)` (`hermes_cli/plugins_content.py:31-66`). It is read-only and namespaced `<plugin>:<name>`; it is not copied into `~/.hermes/skills`.
- **Slash command:** `ctx.register_command(name, handler, description="", args_hint="", argument_mode=None)` (`plugins.py:650-676`). A built-in name clash is skipped.
- **CLI subcommand:** `ctx.register_cli_command(name, help, setup_fn, handler_fn=None, description="")` (`plugins.py:636-647`).
- **Manifest fields** (`hermes_cli/plugins_manifest.py:361-419`):
  - identity: `name`, `version`, `description`, `author`, `kind`, `requires_hermes`
  - declarations: `requires_env`, `provides_tools`, `provides_hooks`, `capabilities`, `requires_plugins`, `python_dependencies`
  - versioning: `manifest_version`, `api_version`
  - other: `config_schema`, `license`, `homepage`, `tags`, `emits`, `listens`, `provides_locales`, `config_keys`
  - Validation requires `name`, `version` and `description` (`hermes_cli/plugin_validate.py:101-112`).
- **`capabilities` (manifest) is NOT where hooks or tools are declared.** It lists privileged capability ids from `hermes_cli/plugin_capabilities.py:30-52`: `tools.override`, `llm.provider_override|model_override|agent_id_override|profile_override|task_override`, `llm.side_question`, `gateway.platform_actions`. Unknown ids are dropped (`:63-88`). Declaring one is consent metadata, not a grant (`plugins_manifest.py:388-391`).
  - `plugin_capability_granted(plugin_id, cap)` (`plugin_capabilities.py:137-154`) returns True only if the cap is in `plugins.entries.<id>.granted_capabilities` or the legacy `allow_*` key is set. Unknown ids and unreadable state → False, with an audit log.
  - Hooks, tools and middleware are declared in `provides_hooks` / `provides_tools` / `provides_middleware` (catalog: `capabilities.provides_*`). Validation diffs them against what `register()` actually did (`plugin_validate.py:407-458`).
  - Falsify needs NONE of the privileged capabilities.

## 4. Secrets

- `agent.secret_scope.get_secret(name, default=None)` (`agent/secret_scope.py:230-261`) and `get_secret_str` (`:272-276`). It honours the active profile scope. Under multiplexing with no scope it raises `UnscopedSecretError` (fail-closed, `:248-260`). `agent.secret_scope` is public API (`hermes_cli/data/plugin_api.json:27`).
- Rule 11 (`plugin-catalog/README.md:91-107`):
  - Declared `requires_env` / `config_schema` secrets are read only via `get_secret`. A bare `os.environ` read can pick up another profile's key.
  - Never read `.env`, `auth.json`, vendor tokens or keychains.
  - Never refresh or write OAuth tokens.
- `requires_env` entries must be UPPER_SNAKE (`plugin_validate.py:164-185`).

## 5. Catalog admission rules (`plugin-catalog/README.md`)

| # | Line | Rule (short) |
|---|---|---|
| 1 | 14 | Human-merged gate: added only via a maintainer-merged PR |
| 2 | 17 | Exact 40-char SHA pin mandatory; branches, tags and short SHAs rejected |
| 3 | 20 | No self-updating code |
| 4 | 27 | SHA bumps are new, re-reviewed PRs |
| 5 | 30 | Owner or major contributor submits, or a maintainer-curated sweep |
| 6 | 39 | Declared capabilities (tools, hooks, middleware, env) must match reality |
| 7 | 43 | Install scanner runs at admission (`dangerous` fails; `caution` warns) |
| 8 | 48 | Desktop plugins stay inside the SDK surface |
| 9 | 66 | No runtime overrides of Hermes core (no replace/wrap/rebind; `no core override` check) |
| 10 | 79 | Dependency security policy is the plugin's (bounded floors) |
| 11 | 91 | Credentials come through Hermes (`get_secret`), never from files |
| 12 | 108 | Approvals and unattended runs are respected; never route around; fail or time out cleanly under cron/gateway |
| 13 | 114 | Risky behaviour disclosed (network, shell commands, reads outside own data) |
| 14 | 120 | Truthful compatibility metadata (`requires_hermes` SemVer floor, in plugin.yaml too) |
| 15 | 132 | No skins or forks of bundled plugins |
| 16 | 135 | One listing per plugin lineage |
| 17 | 142 | No unscannable executable payloads (no binaries or .pyc) |
| 18 | 161 | Password managers come from their vendor |

Entry schema (`README.md:180-209`):
```yaml
name: example-plugin        # [a-z0-9_-]{1,64}
repo: https://github.com/owner/repo   # https only
sha: <40-hex commit sha>    # mandatory exact pin
subdir: ""                  # optional relative path
description: ...
maintainer: OwnerName
tier: community             # official|verified|community
category: tools             # desktop|memory|platform|web|tools|voice|automation|models|general
requires_hermes: ">=0.19"
docs_url: ""
version: "1.4.0"
image: ""; screenshots: []; readme: true; platforms: []; title: ""; onboarding: false; known_issues: []
capabilities:
  provides_tools: []
  provides_hooks: []
  provides_middleware: []
  requires_env: []
```

## 6. `hermes plugins validate [--install-deps] [--json] <path>`

- argparse: `hermes_cli/subcommands/plugins.py:59-66`. Dispatch: `hermes_cli/plugins_cmd.py:1141`. Implementation: `hermes_cli/plugins_cmd_catalog.py:880-918`.
- `--install-deps` runs `pm.sync_venv(plugins=...)` and then probes imports with that venv's interpreter (`:886-899`). A failure is printed and validation continues.
- `validate_plugin_dir` (`hermes_cli/plugin_validate.py:502-564`) runs these checks in order:
  - manifest parses (`plugin.yaml`/`.yml`, or portable `plugin.json`)
  - manifest fields
  - `requires_hermes` parses
  - config_schema
  - `requires_env` UPPER_SNAKE
  - oauth
  - loadable (`__init__.py` / `desktop/plugin.js` / `plugin.json`, `:581-593`)
  - python dependencies PEP 508 (`:596-618`)
  - **capability probe**: runs `register()` in isolation and diffs declared vs actual tools, hooks and middleware; undeclared fails (`:407-458`)
  - built-in tool collisions (`:477-499`)
  - trusted_inbound
  - security scan (`tools.plugin_guard.scan_plugin`: dangerous fails, caution warns, `:621-635`)
  - `check_core_override` (`hermes_cli/plugin_validate_core_override.py:323`)
  - `check_private_imports` (warn only, `plugin_validate_private_imports.py:201`)
  - `check_desktop_surface` (`plugin_validate_desktop.py:175`)
  - language packs
  - isolation audit
- Exit code is 0 or 1.

## 7. Custom base URL

- **Docs, OpenAI-compatible** (`website/docs/integrations/providers.md:672-681`):
  ```yaml
  model:
    default: your-model-name
    provider: custom
    base_url: http://localhost:8000/v1
    api_key: your-key      # or key_env: MY_PROVIDER_API_KEY
  ```
- **Named providers, incl. Anthropic-compatible** (`providers.md:1310-1324`):
  ```yaml
  providers:
    anthropic-proxy:
      api: https://proxy.example.com/anthropic   # base_url/url aliases
      key_env: ANTHROPIC_PROXY_KEY
      transport: anthropic_messages               # chat_completions | anthropic_messages | codex_responses
  ```
  - Entry fields: `providers.md:1326`, plus `key_cmd` for minted tokens (`:1342-1353`).
  - Auto-detection fallback: a `/anthropic` path → `anthropic_messages` (`:1404`).
  - Legacy `custom_providers:` list is still read (`:1367`).
- **Env vars:** `OPENAI_BASE_URL` is honoured only for the `openai-api` provider; `LLM_MODEL` is removed (`providers.md:684-686`). Config is the source of truth.
- **Code:**
  - runtime result dict: `hermes_cli/runtime_provider.py:365` (`provider, api_mode, base_url, api_key`)
  - `model.base_url` read at `:378`; custom-endpoint detection at `:645-652` (`OPENAI_BASE_URL`/`OPENROUTER_BASE_URL` via `get_secret_str`)
  - custom-entry helpers: `hermes_cli/runtime_provider_custom.py`
- **MiniMax Anthropic-style example:** `website/docs/user-guide/configuration.md:1908-1913`.

## 8. Not possible without a core override (judged from the contracts)

| Want | Possible? | Why |
|---|---|---|
| Block completion indefinitely until the check passes | **No** | pre_verify is capped at `max_verify_nudges` (`turn_stop_gates.py:62`; `config_defaults.py:202-203`), and a user can set it to 0. After the cap the turn finishes. A timed-out or raising hook is skipped (fail-open). |
| Arm on any claim | **No** | pre_verify needs landed `write_file`/`patch` edits this turn (`turn_stop_gates.py:56,62`; `tool_result_classification.py:10`). Terminal-made edits and non-code claims never arm it. |
| Run the check inside pre_verify | Technically yes, but **no approval**: hooks have no approval route, and the call is bounded at 30 s (max 600 s) and abandoned (`plugins_dispatch.py:183-184`). Running argv there would bypass approvals (rule 12). | The correct path: the pre_verify message tells the agent to call the plugin's `falsify_run` tool, and the plugin's `pre_tool_call` returns `approve` → human gate. |
| Force a check to run | **No** | A plugin can only nudge (pre_verify message) or block a tool (`pre_tool_call`). It cannot invoke tools on the agent's behalf in-turn; `dispatch_tool` is for slash/CLI handlers (`developer-guide/plugins/index.md:930`). |
| Intercept or wrap finish | **No** (rule 9: `README.md:66-78`; `check_core_override`) | The only seams are the stop-gate hooks and `transform_llm_output`. |
| Rewrite the final answer | **Yes, publicly**: `transform_llm_output` (`turn_finalizer.py:509-547`), first non-empty string wins, before persistence; `hold_stream=True` withholds streaming (`plugins_dispatch.py:567-578`) | Not an override. ProofGate uses it. Can only stamp "VOID/UNVERIFIED", not stop the turn. A competing plugin's string can win. |
| Veto or answer approvals | **No** | `pre_approval_request` / `post_approval_response` are observers (`plugins.py:144-146`). |
| Prevent auto-approve under `cron_mode: approve` | **No** (host setting) | `approval.py:1102-1112`. The plugin must self-refuse via its own context checks. |
| End a turn before inference | Yes | `pre_llm_call` `{"action":"block"}` (`turn_context.py:888-892`) |

## 9. Catalog examples (yaml only; plugin code is external at the pinned SHA, not in this clone)

- **hermes-verifier**: `plugin-catalog/hermes-verifier.yaml:1-20`. Pin `514ff4a7…` (`:3`).
  - Hooks: `post_llm_call`, `pre_tool_call`, `pre_llm_call`, **`pre_verify`**, `on_session_reset` (`:13-18`).
  - Description (`:4`): "a blocker finding can keep a coding turn going"; the phase gate "can block that call (it fails open)"; "no runtime shell execution".
- **proofgate** ("No proof, no done"): `plugin-catalog/proofgate.yaml:1-21`. Pin `b07f2fe5…` (`:3`).
  - Tool `proof_status`; hooks `post_tool_call`, `pre_llm_call`, `transform_llm_output` (`:14-19`).
  - Verifies claims against recorded tool evidence, and in enforce mode holds streaming and may append warnings to or withhold a reply (`:5`). It is the closest prior art to falsify, but it works on heuristic evidence, with no pre-registration and no mutant.
- **approval-ledger**: `plugin-catalog/approval-ledger.yaml:1-20`, uses `subdir` (`:4`), hooks `pre_approval_request`, `post_approval_response` (`:16-18`). "observer only, cannot answer or change an approval" (`:5`).
- Other pre_verify users (grep): `afterforge`, `edit-ledger`, `omh`, `nerve`, `lightkeeper` (`plugin-catalog/lightkeeper.yaml:4`, "a reminder through the verify gate").
- **proofops: NOT FOUND** in `plugin-catalog/` (only `proofable-mcp.yaml`, `proofgate.yaml`).
- Bundled layout example: `plugins/disk-cleanup/plugin.yaml:1-8` (`name`, `version`, `description`, `author`, `provides_hooks`) + `__init__.py:231-236`.

## Implied falsify shape (from the above)

- Register a tool `falsify_run(record_id)`. Its handler re-reads the hashed record and runs only its argv: check, then mutant (VOID if the mutant passes).
- The plugin's own `pre_tool_call` returns `{"action":"approve","message":<argv + hash>,"rule_key":"falsify:<record-hash>"}`, which goes to the human gate (fail-closed, no smart LLM).
- `pre_tool_call` also returns `block` when cron, single-query or an unattended platform is detected, regardless of `approvals.*_mode`.
- `pre_verify` nudges "call falsify_run for claim X" while no verdict exists for `changed_paths` (self-throttle on `attempt`).
- `transform_llm_output` (optionally `hold_stream`) stamps unverified or VOID claims.
- Limits: the pre_verify cap and the write_file/patch-only arming mean completion cannot be hard-blocked.
