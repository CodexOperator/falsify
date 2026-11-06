<!-- DRAFT, private (thought-master 16:00Z 11-06, package item 6). Drafted by director-thought-1; director-thought-review
     audits each line against the bytes. "main" = /fast/falsify main 8872228 (later commits touch submission/ only). A test cited by name lives in
     tests/ there and passed on a fresh clone. A transcript path is a recorded run on /fast, NOT in git. -->
# falsify: what is proved, what is not

## Proved (each line names the run that shows it)

| claim | run |
|---|---|
| A check that passes without the change reads VOID; an honest check on the same fix reads PROVED, covering 2 of 2 hunks | demo/test_demo.py (the pinned demo story; "demo story OK" on a fresh clone of main) |
| A planted tautological check reads VOID | test_PLANTED_tautology_reads_void |
| A proved claim reports how many hunks its check covers | test_a_check_that_needs_the_change_is_proved_with_its_hunk_coverage, test_a_check_that_misses_a_hunk_says_k_of_h |
| A check run with no human approval, a "no", or a non-interactive caller executes nothing and reads VOID "not run" | test_no_approver_runs_nothing, test_non_interactive_runs_nothing_even_if_approved, test_auto_approved_run_without_a_human_answer_is_void_not_run, test_a_human_deny_runs_nothing |
| The approver sees exactly the registered argv; one human "once" grants one run | test_the_approver_sees_exactly_the_records_argv_once, test_human_once_proves_and_the_grant_is_one_shot |
| An edited record, or an edited ledger line, is detected | test_a_moved_record_reads_void, test_an_edited_or_dropped_ledger_line_breaks_the_chain_and_audit_says_so |
| Retry-until-green is counted and flagged | test_retry_until_green_is_counted_and_flagged |
| A collection error or an import break is never counted as a kill | test_an_rc_outside_kill_rc_is_unrevertable_not_a_kill, test_a_hunk_that_breaks_the_import_is_unrevertable_by_sanity_not_a_kill |
| The nudge names files changed with no claim, and proved claims not yet closed (with the exact closing call) | test_a_change_no_open_claim_covers_is_nudged, test_a_proved_not_done_claim_is_nudged_with_its_exact_done_call_and_listed_first |
| No-diff claims: one blind mutant, drafted without the check, drawn after the run by a committed seed | test_a_no_diff_claim_is_proved_against_its_drawn_blind_mutant, test_a_tautological_check_reads_void_on_the_drawn_mutant, test_a_moved_seed_voids_the_draw |
| The blind drafter never gets the check's own files or the tests a runner would discover | test_the_drafter_never_sees_the_files_the_check_sanity_or_keep_name, test_a_pathless_pytest_check_withholds_pytests_discovery_set |
| Committing first does not dodge the derived mutant (within one session) | test_a_commit_first_dodge_is_judged_from_the_earlier_base, test_widening_never_crosses_into_an_older_session |
| Stale bytecode cannot fake a pass on a reverted tree (the bug the suite's positive control found) | test_a_same_size_moved_line_fix_with_an_honest_check_reads_proved; goal:g5.53 M-GOLD a08 (director-thought-science) |
| Under Hermes, the drafter's URL and key come only through Hermes's secret scope | test_the_drafter_key_comes_from_hermes_secret_scope_never_os_environ |
| The plugin passes Hermes's own checks | `hermes plugins validate`: "Validation passed." (15 checks, on a fresh clone of main 8872228); catalog validator OK on a fill.py-rendered copy |
| Hermes + Haiku, UNHINTED task (the prompt names no falsify call): the agent registered a claim on its own; Hermes's approval prompt showed the exact check; the run's one approval was answered "Allow once" by a scripted stand-in for the user (drive_session.py, not a human); the verdict was proved, covering 1 of 1 hunks; the agent left it open, not done | /fast/director-thought-goals/session/transcript10.txt (:75 prompt, :326-333 approval prompt, :412 "The claim is registered and judged proved. It is recorded as open, not marked done.", :431 verdict); drive10.out "approvals [(31.5, 'o')]" |
| Hermes + local Qwen3-4B, SCRIPTED task (the prompt gave the exact falsify_register arguments, the check and each call in order): register, judge, proved | /fast/director-thought-goals/session/transcript8.txt (:75-79 = task-hinted.md) |
| goal:g5.52, 20 tasks (SOLVED = falsify PROVED on the agent's own check AND a hidden test passes): Haiku 5.5 20/20 for $0.0198 ($0.00099 per solve), Sonnet 5.5 batch 20/20 $0.0701, Sonnet 5.5 20/20 $0.2656, Opus 5.5 20/20 $0.6762, local Qwen3-4B 6/20 at $0 | agi graph posts/thought-master c0fd488d54, experiment:solved-claims-per-dollar-local-and-console-g5-52-run-1; hypothesis:solved-claims-per-dollar-local-and-console-g5-52 (proved) |
| On that suite, two planted ways to game it failed on all 20 tasks: an agent that edits nothing solved 0/20; one that only adds a passing test read VOID on 20/20. The reference fix (the positive control) read SOLVED 20/20 | same experiment node (M-SOLVE, M-HIDE, M-GOLD) |
| Prompt caching cut Sonnet's bill to 0.636 of the same usage priced flat | same experiment node (E-CACHE) |

## Not proved, and the limits

- **It nudges; it never blocks.** Hermes consults `pre_verify` only when the agent stops after `write_file` / `patch`
  edits, at most `max_verify_nudges` times per turn (default 3). Edits made through the terminal never trigger it.
- **No sandbox.** An approved check runs with the user's permissions in a temporary copy.
- **Approval is an answer at Hermes's prompt, not proof of a person.** falsify cannot tell a person from a script answering
  it; in session 10 a scripted stand-in answered.
- **A proved claim means this check needs this change**, not that the code is correct or the check tests the right
  property. A blind mutant is only as good as its drafter.
- **Closing the claim:** in session 10 the agent did not call `falsify_done`. The closing-call hint (G3) is on main and
  tested, but no Hermes run with it has been recorded yet.
- **Unprompted use by the local 4B model is not shown.** Session 11 (local, unhinted): 0 falsify tool calls in 14 min 58 s
  (Hermes's own Duration line; 900 s budget), and a dangerous-command approval timed out unanswered (:5696, :5701), per
  /fast/director-thought-goals/session/transcript11.txt. The nudge's text is not printed (Hermes stores it as a synthetic
  user turn), but its trace is: after the landed patch (:1572) the model replies "the user is pointing out that I didn't
  use the falsify tools" (:5737), and Hermes counts 2 user messages (:6490-6491) where the task is the only visible one.
- **Hermes 3 is not agentic.** Hermes Agent's own banner warns "Nous Research Hermes 3 & 4 models are NOT agentic". In our
  three Hermes-3-8B sessions (transcript1-3.raw) it made 1 tool call, and that one failed on our harness's missing
  directory (exit 126), not on the model. So the demo runs Qwen3-4B (local) and claude-haiku-5-5.
- **Discovery:** tests a custom runner finds under other names are not withheld from the blind drafter.
- **g5.52's scope.** Its Console arms ran through a minimal SCRIPTED loop, not an agent harness (the owner has since
  ruled the Console door harness-only), and every Console arm solved 20/20. So its dollar figures are a price list
  for this easy suite, not a quality ranking of the models. The harness lane (pi, droid, unreal-agent, run 3, jailed)
  solved 20/20 each with 0 false assurance, 0 false alarm, 0 tamper, and each cost more than the bare loop on
  claude-haiku-5-5: pi 1.4.0 1.35x, unreal-agent a5f3fd1 1.80x, droid 0.247.0 5.2-5.3x, mostly from extra turns. No task
  discriminated, so this says nothing about solve rate. Source: experiment:harness-overhead-g5-52-suite-run-3, agi
  graph posts/thought-master 23642c1f50.
- **goal:g5.53** (40 tasks, three tiers) is not closed: [PENDING, director-thought-science].
