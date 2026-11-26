# Native DCA creation-clock constraint

The actual completed `nfi40_x8_500_cost15` account has 5-minute main candles, no `timeframe_detail` setting, LIMIT/GTC entries, and entry/exit timeouts of 3/2 minutes. The official owner maps the absent detail setting to a zero detail timedelta. This study changes no account configuration, source, economic threshold, or earlier audit.

The narrow result is **PASSED_NARROW_NATIVE_DCA_CREATION_CLOCK_CONSTRAINT**. For this pinned native execution path, a DCA filled at clock F cannot be an older pending entry: all earlier creation clocks are at least 5 minutes earlier, and the built-in 3-minute timeout cancels those orders before fill processing. NFI's custom timeout callbacks cannot undo an already satisfied built-in timeout. A replacement creates a new Order with the current source-row timestamp; it does not reset the old Order's timestamp. The source neither constructs Orders nor writes the audited order clock/status fields.

The minified ZIP still does **not** export creation timestamps. The following values are conditional source-constrained inference, not recovered metadata or venue observations:

| Pair | Trade index | Order index | Fill and uniquely inferred creation, UTC |
| --- | ---: | ---: | --- |
| CRV/USDT:USDT | 127 | 11 | 2025-08-10 16:00 |
| CRV/USDT:USDT | 127 | 13 | 2025-08-13 00:00 |
| XRP/USDT:USDT | 279 | 3 | 2026-08-23 08:00 |

All 683 exported fills were checked for 5-minute clock alignment. This is not a return/NAV/funding numerical recomputation. The separate complete-account audit and the 8bp account remain outside this proof; the latter needs its own successful terminal configuration and source evidence before using the constraint.

Before execution, `freeze.json` bound the proof and protocol, actual effective configuration, actual copied NFI strategy, source strategy, native ZIP and terminal result/process records, all 40 funding calendars, the original runner/storage adapter, and native owners: 56 files. All 56 hashes remained equal afterward. The runtime also checked all 595 vendor source files. Source is `fc5cb221f29ebca5f336429d97920eb93917cdd87f9769381b23b467ca6d5bf3`; effective configuration is `543ddd3d0d999f0de80df33aaab27e8f6a29d5322baa17a30d63c4fa1728f719`.

The actual command was `.venv-paper/bin/python -B -m research.crypto.nfi40_dca_creation_clock_proof_20261118_v1.prove_clock`, observed exec session 44703, exit 0. All 22 cases passed: eight long/short old-initial/old-DCA/same-clock-new-DCA/expiry-before-replacement cases; two replacement controls; native no-detail clock generation; two explicit delayed-fill counterexamples with 1-minute detail or 10-minute timeout; three exact timeout boundary cases; and six admission rejection cases. Missing effective-config evidence, unknown detail, changed timeouts and changed units cannot inherit this certificate. The measured macOS peak RSS was 436,879,360 bytes. Selected fatal-error static checks also exited 0.

The fixtures execute real native `manage_open_orders`, `check_order_cancel`, `check_order_replace`, `_enter_trade`, `_try_close_open_order`, `Order.close_bt_order` and LocalTrade recalculation on detached objects. Only the source timeout functions are compiled from their exact AST; the full strategy is not instantiated. No Backtesting, Wallet or Exchange instance is constructed, no account starts, and native trade registries remain unchanged. Stake/precision metadata use fixture constants; funding and after-fill stop hooks only record calls, liquidation updates are omitted, and NFI order-filled economics are not executed. These explicit omissions keep this an order-clock mechanical proof, not full matching, liquidation, callback, wallet or financial validation. Complete native-loop order is separately bound and checked in the owner source.

The decisive fixed owners are `backtesting.py` lines 1334–1469 (manage/cancel/replace), 1543/1569/1574 (cancel before fill before adjustment), 1143 and 1262–1279 (new Order creation and immediate attempt), 1612–1632 (5-minute/no-detail clocks), and `strategy/interface.py` lines 1734–1759 (built-in timeout precedence). Negative counterexamples are fixture-only and do not alter the account contract. No failed object run occurred; no earlier failed evidence was removed. No deployment or orders are authorized by this result.
