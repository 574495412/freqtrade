# Native blacklist replacement study

This directory owns an isolated **research-only** Freqtrade experiment. It does not change a running paper or live service. Read `PROTOCOL.md` before interpreting accounts.

The financial engine is the pinned Freqtrade native `Backtesting` implementation. `HistoricalVolumePairList.py` is a local historical-data bridge to unchanged upstream `RangeStabilityFilter` and `VolatilityFilter`. The historical clock and initial-entry pool gate are explicit existing local engine changes. Storage adapters preserve native values and types and are validated against a complete known account. This is not an official upstream historical risk-filter feature.

## User-prioritized delivery scope

`user-scope-change.json` records the later request to focus on 2026 and shorten waiting. The original economic window was already 2026; older input is indicator warmup only. Deliver `REPORT-2026-15bps.md` and `comparison-2026-15bps.json` after **both** 15bps native accounts finish. `year2026_report.py` validates these actual accounts and leaves the original two-cost gate explicitly unevaluated.

The unstarted `accounts/structural_8bps/DEFERRED_BY_USER.json` directory is an intentional scheduler stop barrier: the unchanged batch runner stops before launching any 8bps child. Its resulting scheduler `FileExistsError` is not a native strategy failure. Both8bps sensitivity accounts are deferred; do not remove the marker or resume them without restoring the intended scope explicitly. Original plans and the four-account protocol remain unchanged.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi_blacklist_rules_20261109.year2026_report
```

`disk-recovery.json` preserves the earlier disk-guard attempt (exit73), generated-cache inventories and unchanged noncache evidence hashes. The successful retry uses the same source, input and economic plan. Cache recovery changed no risk or resource thresholds.

## Evidence and reproduction

- `plans.json` seals sources, actual plans and the pre-economics protocol. `parity-plan-v002.json` is the effective known-account control; the earlier failed adapter attempt is retained through its registration and failure receipt.
- `parity-result.json` requires identical 89,917 NAV points and all 244 native financial trade records.
- `source-audit.json` tracks the old, current and pre-year author lists, eligibility scope and LIT contract identity evidence. `identity-calendar-audit.json` checks daily and main 5-minute inputs against contract onboard times.
- `schedule-summary.json` and `rule-patterns.json` describe frozen causal daily decisions. Membership changes are not order turnover or factor returns.
- `comparison.json`, `comparison.csv`, `REPORT.md` and `verification.json` are generated **only after all four policy accounts have completed successfully**. A `checkpoint-*.json` is a partial account audit, not the final comparison.

Run from the Freqtrade repository root, using the existing research environment:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi_blacklist_rules_20261109.run
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi_blacklist_rules_20261109.analyze
```

The runner verifies seals and executes one native financial worker. Completed, audited accounts are reused; failed or partial directories are preserved and cause an error. It removes only generated caches after successful accounting, recording their byte inventory. Raw input data, archives, ledgers, failed attempts and hashes are retained under `user_data/crypto_quant/nfi_blacklist_rules_20261109_v001/`.

Validation:

```sh
.venv/bin/python -m pytest -q research/crypto/nfi_blacklist_rules_20261109/test_policy.py
.venv/bin/ruff check research/crypto/nfi_blacklist_rules_20261109 --extend-per-file-ignores register.py:E501 --extend-per-file-ignores source_audit.py:E501
```

The narrow lint exceptions preserve byte-frozen financial source strings and Chinese report punctuation. They do not skip execution, economic or input validation.

## Interpretation limits

The study tests two frozen rule policies and two execution-cost assumptions. No thresholds or names may be changed after observing their economics. A drawdown under 30% is historical research admission, not a live account stop or a loss guarantee. The risk filter restricts **new initial entries**; original NFI DCA/exits remain active for held names after pool removal.

An event ban needs asset/contract identity, reason, effective/publication time, evidence and a review/release condition. Calendar expiry alone does not resolve hacks or supply integrity failures. Historical announcement publication times were not reconstructed here. Price windows can expire and requalify; that must not be described as proof that a project risk has ended.

Both new policies remove retrospective named-blacklist rules but retain current author membership and current market/tier eligibility. Retired contracts and a full point-in-time market universe are not recovered. Previous author-blacklist profits are retrospective auxiliary evidence, not unbiased controls. No account in this study alone qualifies a policy for production.
