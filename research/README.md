# Freqtrade research and backtests

- [NFI 80-pair backtests](crypto/nfi80_extended_windows_20261126_v1/README.md) covers the archived 12-slot baseline, the 14-slot candidate, and the short-rebuy cap. The continuous window is January 2025 through November 25, 2026 UTC.
- The [cap comparison](crypto/nfi80_extended_windows_20261126_v1/short_rebuy_14_wallet_comparison.json) links to the independently reconciled summaries for [15 bp](crypto/nfi80_extended_windows_20261126_v1/short_rebuy_14_wallet_15bp_verified.json) and [8 bp](crypto/nfi80_extended_windows_20261126_v1/short_rebuy_14_wallet_8bp_verified.json). The cap beat uncapped 14 slots on net return at 15 bp and trailed it at 8 bp. The combined result records `both_costs_passed: false`.
- The crypto and equity study directories hold source code, frozen protocols, and compact evidence for completed, rejected, and pending experiments. Their protocols identify the engine version, stage of validation, and market inputs for each run.

## Replaying an account

Use the study's frozen protocol and verified input hashes to restore the original data and engine before replay. Existing evidence may reference absolute paths from the original research checkout. Raw exchange data, full account ledgers, generated caches, and external dependency checkouts are stored separately. Links to those artifacts identify the original inputs and outputs. Account return figures describe historical simulations with the configuration and costs specified in each protocol.
