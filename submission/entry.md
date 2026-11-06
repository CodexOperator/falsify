<!-- DRAFT, private: the form's entry text, 150 words. Numbers only from goal:g5.52 once committed. -->
falsify makes an agent's "it's fixed" falsifiable. Before touching code, the agent pre-registers its claim, the check
and the base commit, hashed. Afterwards falsify runs that check twice in temporary copies: on
the agent's final tree, where it must pass, and with the agent's own change reverted, where it must fail. A check that
passes without the change reads VOID, not green, and a proved claim says how many hunks of the change its check
covers. The agent never writes the mutant: it comes from its own diff. Claims with no diff get blind mutants from
a separate model that never sees the check. Every run asks a human for the exact command first. falsify records
verdicts in a hash-chained ledger and nudges the agent before it finishes. It never blocks completion. It proves
that this check needs this change, not that the code is correct. Stdlib only, no sandbox.
