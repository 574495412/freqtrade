# Local Cover allocation weights

`cover_allocation_weights` is a local signal component, not upstream Freqtrade
functionality or an account engine. It implements the performance-weighted
simplex average in Cover (1991), equations 1.3--1.5, using a finite triangular
lattice for BTC/ETH/unremunerated cash. Resolution100 has5151 fixed portfolios;
these are internal model states, not5151 funded backtests. No source package is
imported. The reviewed Marigold package uses FMIT and is not transplanted.

UTC daily closes must be complete and positive. Decision dayD uses only closes
strictly beforeD; the first decision is uniform and has no pre-window training.
Future rows are ignored. This does not certify feed publication timestamps or
make finite-net-return guarantees from the frictionless asymptotic theorem.

`OnlineAllocation` consumes this component inside native strategy initialization
and executes through the supported native stake/quantity callbacks. The local
account uses a one-hour execution delay,99% of actual marked NAV, actual lots,
minimum deltas and fees. Constant thirds is an explicit cash-inclusive control,
distinct from the source library's cash-excluding default CRP. Unfilled target
differences remain tracking error, not free rebalances.

The frozen study `research/crypto/universal_allocation_native_20261116_v1` has
8 native wallets and6 predeclared actual500+500 combinations; all fail the
frozen financial gates. Keep those negative results and do not retune the
resolution, cash fraction, timing or source direction to rescue them. The
8 mathematical/input tests,1824 independently reconstructed weights, real-input
prefix refits and complete native fill/budget/NAV audits are separate evidence
from independent alpha, out-of-sample, paper or live readiness.
