# NFI high-win-rate mechanism review

The owning performance evidence is the completed pinned native Freqtrade accounts
in `nfi_truth_audit_20261109`. This review is an auxiliary diagnosis of their
actual exported campaigns, orders, full NAV and source, not a new portfolio or
execution engine, factor test, strategy selection account or causal ablation.
The existing financial outcomes were already known before this review.

Scope:2026-01-01 to2026-11-09 05:00UTC, initial100USDT, stake budget99USDT;
author-named and no-named-blacklist futures3x/12slots/8bps, spot36assets/6slots/13bps.
Natural completion excludes terminal forced closes and closes at/after the common
pre-order cutoff. Keep all open-cycle cash/marked PnL in NAV. Do not call a
multi-order campaign one independent directional prediction.

Read native archives and registered input receipts; fail if hashes or cash
identities differ. Describe hold-time asymmetry, win/loss sizes, additions,
remaining campaigns, exit reasons and correlated entry times. The fixed1h/4h/24h
labels use actual first fills and frozen real5m OPEN quotes; they ignore fees,
position changes and strategy exits. They are descriptive price labels, not a
replacement return backtest, all-signal IC or independent OOS test. Native
min/max price versus first fill is not actual inventory MAE or account drawdown.

The last-day risk snapshot uses the pinned source EMA50/EMA200 and mean14daily
(high-low)/low range formulas with native800daily warmup. It evaluates the
structure/stale gates only, not complete custom_exit/callback control flow.
No parameter tuning, new financial runs, broker requests, orders, live changes or
restart are performed. Future risk-rule ablations must be separately frozen and
run through the owning native backtester; failed outcomes must remain failures.
