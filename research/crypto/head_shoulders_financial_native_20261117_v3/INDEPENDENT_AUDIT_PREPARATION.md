# Independent account audit preparation

The independent reconciler is prepared and frozen. **No H&S financial account has run, no new market return was read, and no execution release was created in this stage.** The executable audit is `independent_account_audit_v2.py`; it reads completed native ZIPs and frozen data and never imports or starts Freqtrade's account engine.

The prior 49-file preparation seal and all v1/v2 source/policy files remain unchanged. The 595-file official vendor tree remains unchanged. `LegacyFiveMinutePrecisionMixin` is a **local research extension** overriding the supported precision lookup to preserve the original NFI5m monthly-table/asof contract. It is not upstream functionality, historical-rule certification, or a lookahead correction. Native matching, wallets and inclusive funding continue through the pinned official implementation.

## Frozen audit scope

- Four actual H&S wallets: 1000/500 USDT × 15/8bp per side, original ordered40 perpetual pool, 1x, six slots, .99 tradable ratio. Decimal fill cash includes entry and exit fees; every hourly NAV uses mark open before that clock's fills/funding refresh and reserves the remaining exit fee.
- The entire common window has 14,592 hours, from 2025-01-01 00:00 to 2026-08-31 23:00 UTC. The 2025 and 2026-through-August slices contain 8,761 and 5,832 points, carry positions across the boundary, and never reset capital. Native terminal forced-close balance is reported separately from the ranking NAV.
- Original inclusive funding is replayed from actual quantities, frozen rates and marks. The original independent NFI500 `funding_history` and `metrics` function ASTs are preserved and provenance-pinned. H&S also has a separate two-fill formula: final `sign*q*sum(E<=F<=C)` and before-clock snapshot `sign*q*sum(E<=F<T)`.
- The H&S formula is admitted only after checking a closed native trade, exactly one entry and one equal full exit, exact entry confirmation clock, fixed no-DCA policy, complete source/detail clocks, and pinned native/export owners. The minified ZIP omits creation and some trade diagnostic fields: missing fields are not fabricated. A closed trade with exactly two equal opposite-side orders proves the narrow filled-order path through the frozen native lifecycle. This proof does not generalize to NFI DCA. Outside it, missing/deferred boundary creation metadata remains **UNKNOWN**, even if aggregate NAV and fees match.
- AXS/TRB's unknown 2026-06-24 04:00 UTC funding rate is never filled with zero. Any gross campaign quantity before/after same-clock fills causes UNKNOWN; opposite positions or sleeves are not netted.
- Every actual H&S trade binds to its original event ID. All 1,891 immutable event records must equal the sealed pre-financial catalog. Source exit timing is then recomputed independently from completed source closes available by the actual exit clock: strict target/stop crossing, then `break_i+width-1` expiry. Touches do not cross; self-terminal events cannot execute; late source exits fail.
- Native custom source exit precedes stop-loss/liquidation in the pinned engine. A risk label at or after an already visible source terminal is rejected under this fixed source policy. Stop-loss, liquidation and final forced-close exits are kept as separate categories. **Native risk execution prices are not independently certified by this audit.** A future `VERIFIED` status means only the declared fill cash/NAV/funding/source-clock scope; it must not be described as independent risk-price validation.
- Fixed combinations use actual H&S500 + actual NFI500 NAV levels, total1000, with both sleeves independently reaudited. No scaling of 1000 wallets, weight search, rebalancing or funding netting. The frozen economic gates compare each cost to the same-cost original NFI1000 curve. Unknown sleeves block combination admission.

## Observed validation

The original synthetic run failed with exit1: scalar `Timestamp.as_unit('ms').value` still returns nanoseconds, causing an overflow when treated as epoch milliseconds. The original code, freeze and failure log remain intact. The separate v2 changes only that scalar conversion to `int(Timestamp.timestamp()*1000)` and redirects its imports/receipts. No source or economic rule changed.

Commands actually executed from `/Users/chrise/GitHub/freqtrade`:

```text
.venv-paper/bin/python -B research/crypto/head_shoulders_financial_native_20261117_v3/test_independent_audit_v2.py
.venv-paper/bin/python -B research/crypto/head_shoulders_financial_native_20261117_v3/test_timestamp_units_v3.py
```

Both exited0. The first passed **36 synthetic cases** covering funding directions and boundary fills, DCA/partial-exit bookkeeping, fees, before-clock NAV, unknown missing-rate exposure, source strict equality/crossing/expiry, future shocks, wrong tags, zero duration, pending/partial paths, late exits, risk precedence, full calendar slices, and actual500-only combination gates. The additive storage-unit regression passed **four input variants / 12 assertions** for pandas ns/us/ms and standard UTC datetime at identical whole-hour and five-minute clocks. Ruff passed for the three active code/test files (S101 intentionally allowed for audit assertions; optimized Python is rejected).

An independent read-only review by `candidate_gap` checked the 22 v2 freeze hashes and the narrow funding/source-clock integration, finding no new blocker within that scope. It did not execute an account. The earlier native preparation probe had zero entry masks; its success and the synthetic callback cases do not establish an actual market matching trajectory.

Freeze hashes:

- `independent_audit_freeze_v2.json`: `961111c411fefdf4aaa73a4cc61b42d286403f24ecdc001323b183a3ae589a6b`
- `independent_audit_unit_freeze_v3.json`: `4dcb41e3b2356e816e3f1c4a35a7309a52d02dfef6996e42610593c67c3daf68`
- Active reconciler: `f841934ed845ea9d7ec0a3c6da05f595e2e93c008ff2cc113e93294828575654`

Financial outputs, independent market-account audits and promotion remain pending. The future audit command requires completed four-wallet and NFI500 receipts; it is not being invoked now. Its successful process completion alone is not economic admission: inspect per-account `status`/`passed` and final admission gates. Historical execution-rule precision gaps and repeated-window/OOS limits remain unchanged.
