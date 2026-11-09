# NFI replay truth audit — diagnostic findings

This is a native Freqtrade audit, with explicit local diagnostic extensions. It is
not independent out-of-sample validation and does not certify live profitability.
The user released the manual startup research gate; findings remain separate.

## Fixed diagnostic state contamination

Pinned engine c051e9065c5b677f03817dd1f83cb980af1ed684; NFI X8
1af55971d3a64703ec449078e09fdec257d0d3c6, strategy SHA256
fc5cb221f29ebca5f336429d97920eb93917cdd87f9769381b23b467ca6d5bf3.

The unchanged official lookahead command reproduced three exit warnings in spot
and three in futures on its frozen 2025Q1 ADA/ETH sample. For each warning, four
native prefix controls tested singleton/two-pair scope and clean/recorded cache.
All12 clean-cache controls reproduced the exact natural exit and reason; all12
recorded-cache controls reproduced the anomaly. Every compared indicator cell
was unchanged. Some persisted target timestamps lay after the prefix cutoff.

`native-cache-isolation.patch` gives each diagnostic full/prefix replay its own
runtime directory. The fixed native command reported0 entry/exit/indicator
warnings in both markets (11spot/12futures signals). The original and fixed full
baseline native trade files are byte-identical, including every order. See
`cache-proof.json` and `test_diagnostic_evidence.py` for executable assertions.
This establishes the cause of those six warnings in this sample. It does not
prove that all2026 trades or all267 assets are free of every lookahead issue.

The patch is a LOCAL modification to the owning native diagnostic module, not an
upstream Freqtrade release. Do not clear running bots' legitimate restart caches.
The return accounts use their own new process and runtime directory already;
there is no evidence here that the201.24% account had this same cache leak.

## High win rate is an incomplete risk description

Native archives and89,917 five-minute NAV observations per original account were
reconciled. The8bps scenario's201.2354% net return and15.6573% marked drawdown are
reproduced from the stored curve, including remaining exit fees and modeled
funding.305/306 naturally completed campaigns won (99.6732%). Four other campaigns
were still open at cutoff, with combined marked PnL of-679.3549USDT. Including
terminal forced closes,305/310 campaigns won (98.3871%). Those open losses ARE
included in NAV; they are absent from the natural-close win-rate denominator.

At15bps,302/304 natural campaigns won; the mean natural win was8.0330USDT versus
mean natural loss204.5988USDT. A single campaign had47 entries. Such observations
are consistent with a high-win-rate strategy retaining infrequent large losses.
They are not evidence of a low probability of ruin. Cost changes also alter
profit-sensitive exits and subsequent allocations: additions change63→142
between8/15bps, rather than representing only a subtraction from fixed fills.

## Remaining interpretation limits

- The current named author blacklist was applied back to January: hindsight bias.
- Current membership, active-market filters and exchange metadata remain
  survivor/source selected. Removing named exclusions does not reconstruct
  delisted assets or complete historical exchange rules.
- Multiplier/cost/asset choices were inspected on related data. No independent
  unobserved holdout is claimed. Repeated2026 parameter selection can overfit.
- Backtest candles model matching and mark snapshots; neither orderbook depth,
  intrabar worst risk nor actual live commissions/fills are established.
- Native funding is included in trade/NAV accounting. Native simulated Wallets
  reconstruct free cash from closed/partially realized campaigns; this is not
  authenticated intratrade exchange funding-cash settlement or liquidation
  collateral parity. No exact live-versus-backtest claim is made.
- A100USDT fixed stake ceiling changes allocations/minimum-order admission and
  reinvestment. It needs separate native accounts;1000USDT results cannot be
  scaled into that deployment contract.

The economic registration freezes100USDT author/structural futures cases and a
36asset spot case before their outcomes. Futures are3x/12slots/8bps each side;
spot is1x/6slots/13bps.99USDT is usable stake; no profit-based expansion above99.
30% maximum five-minute marked drawdown is the research gate, not a loss guarantee.
The8/13bps cases do not establish higher-cost stress outcomes.

## Failures retained

The first diagnostic driver treated successful `SystemExit(None)` as failure;
v002 corrected only exit-code handling. The first fixed spot attempt hit Binance
418 before replay; a subsequent frozen-metadata run completed with outbound
sockets blocked. The first spot-tail preparation failed timezone concatenation;
v002 normalized UTC and completed36assets/180files, preserving all prior input
hashes and raw unsigned public responses. No failed run is counted as a success.

Native raw files: `user_data/crypto_quant/nfi_truth_audit_20261109_v001/`.
`ledgers/` contains raw campaigns, fills, monthly summaries and complete NAV CSVs.
Diagnostic cases contain actual native trades, callback states, cache snapshots,
CLI status and comparisons. `accounts/` contains new100USDT account runs.

Documentation correction: the copied frozen account runner retains a legacy
"hourly mark" description string. Its observer actually loads `5m-mark.feather`
for futures and real spot `5m.feather` for spot at each five-minute observation.
The result curve column `marked_equity` and its file contents determine coverage;
the legacy prose is not evidence of hourly valuation. No financial source is
edited after its registration to fix this label.

The100USDT plans inherit their parent's historical provenance fields and some
legacy narrative labels (including static/dynamic comparison purpose and old
cost grids). The new economic-registration contracts, explicit runtime configs,
`dynamic_universe`, actual pairlist receipts and selected `cost_bps` define these
three runs; old prose is not an additional account or an extra stress test.

Rebuild the diagnostic engine into a NEW directory with
`.venv-paper/bin/python -B -m research.crypto.nfi_truth_audit_20261109.build_fixed_engine --output user_data/crypto_quant/nfi_truth_audit_20261109_v001/fixed-native`.
The offline diagnostic driver then uses that fixed engine. Existing directories
are never overwritten. `build-validation.json` records a fresh rebuild whose595
source files exactly match the engine used by the successful diagnostics.
The first builder attempt stopped before patching because the upstream manifest
lists the license as both a file and a symlink; its technical failure is retained.

The frozen runner also retains its parent's introductory "no wallet replaced"
text. That wording is superseded by the explicit `capital_source` registration:
this revision installs `CappedWallets`, the same local budget-limit subclass used
by the user's manual release. Native fill matching and trade profit accounting
remain the owning engine's implementation; the99USDT budget extension is local.
