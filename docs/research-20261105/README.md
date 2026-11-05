# 2026-11-05 frozen crypto research archive

This archive preserves the spot/futures comparison, independent audits and negative
findings produced before the standalone deployment cutover. Source receipts bind
copied reports to their original files. Historical absolute paths and dependencies
are intentionally retained as evidence; referenced large local datasets are not
included in Git and must be restored and hash-checked for reproduction.

- Spot: long/short-window X7/X8 research, 5-minute marked-account audits, and
  descriptive audit revisions are under `research/crypto/spot_baseline_20261105*`.
- Futures: same-contract six-pair and forty-pair comparisons, funding and cost
  checks, and lookahead diagnostics are under `research/crypto/strategy_comparison*`.
- Official lookahead diagnostics did not qualify X8 for live promotion. Retain
  the detailed interpretation and failed/unknown findings; migration is paper-only.
- Historic reproduction runners use the earlier pinned CZ-vendored environment.
  New running bots use only this Freqtrade repository and its `deploy/` release.

Validation on 2026-11-05: six matched-account tests and twelve negative tamper
checks passed. All 99 initially untracked research files passed Python/JSON syntax
and known-secret scanning. These checks do not establish economic performance.
