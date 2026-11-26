"""Versioned paper-contract audit, not an independent return engine."""
import hashlib
import json
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    receipt = json.loads((HERE / 'paper_receipt.json').read_text())
    assert hashlib.sha256(Path(receipt['path']).read_bytes()).hexdigest() == receipt['sha256']
    text = (HERE / 'sources/paper.txt').read_text()
    assert 'Values are illustrative' in text
    assert all(t in text for t in ['603.2%', '608.2%', '41.1%', '46.1%', '1,411,000', '1,461,000'])
    # These are checks on published numeric assertions, not reconstructed
    # trades or a claim that any published curve is the actual account.
    start, end = D(1000000), D(1411000)
    table_return = end / start - 1
    body_return = D('6.032')
    assert table_return == D('.411') and table_return != body_return
    implied_body_terminal = start * (1 + body_return)
    assert implied_body_terminal == D(7032000)
    # Explicit deterministic counterexample to equating a level-regression
    # quantity hedge ratio with the same notional ratio. No backtest or costs.
    p1, p2, beta, long_notional = D(200), D(100), D(2), D(1000)
    q1 = long_notional / p1
    source_q2 = beta * long_notional / p2
    spread_q2 = beta * q1
    dp2, dp1 = D(1), D(2)
    assert dp1 == beta * dp2
    source_pnl = q1 * dp1 - source_q2 * dp2
    spread_pnl = q1 * dp1 - spread_q2 * dp2
    assert source_pnl == -10 and spread_pnl == 0
    hours = int((datetime(2024, 1, 1) - datetime(2021, 1, 1)).total_seconds() / 3600)
    assert hours == 26280
    data = dict(passed=True, scope='Source contract/readiness audit only',
        paper_sha256=receipt['sha256'], review='27pages extracted; relevant methods/results/appendix text read, pages8/9/10/22/24 rendered and visually checked',
        published='2026-06-11', license='CC BY4.0 per publisher page',
        source_universe=['BTC','ETH','BNB','ADA','SOL','XRP','DOT','LTC','LINK','DOGE'],
        source_window=['2021-01-01','2023-12-31'], local_target_window=['2025-01-01T00:00Z','2026-08-31T23:00Z'],
        issues=[
            dict(kind='inconsistent_performance', pages=[10,22], table3_overlay_return='.411', section5_2_1_overlay_return='6.032', appendix_terminal=str(end), body_implied_terminal=str(implied_body_terminal), disposition='Neither version admitted as author account; do not choose larger number'),
            dict(kind='illustrative_equity', pages=[10,22], fact='Main text calls appendix a cross-check from final net series; appendix Table11 explicitly labels values illustrative and asks replacement with actual backtest output', disposition='No actual equity series/trade/fee/funding artifact supplied by this PDF'),
            dict(kind='gof_conflict', pages=[8,24], main='1000parametric bootstrap samples with parameter refitting, KS and CvM p>.05', appendix='Algorithm3 p-values from asymptotic distributions', disposition='Do not silently use the cheaper test and claim source parity'),
            dict(kind='cost_scope_conflict', pages=[1,9,10,16], abstract_fee_roundtrip='.0008', section4_3_3_fee_plus_spread_roundtrip='.0012', table10_matching_moderate_return_cost='.0008', disposition='Matched net-account cost definition unresolved; no favorable cost choice'),
            dict(kind='quantity_notional_hedge_mismatch', pages=[6,9], equation='P1=alpha+beta*P2', prose='Long notional N, short notional beta*N', counterexample=dict(p1=str(p1),p2=str(p2),beta=str(beta),long_notional=str(long_notional),source_q1=str(q1),source_q2=str(source_q2),spread_q2=str(spread_q2),source_common_move_pnl=str(source_pnl),spread_common_move_pnl=str(spread_pnl)), disposition='This example disproves automatic spread/dollar neutrality; no actual strategy return computed'),
            dict(kind='account_definition_missing', pages=[9,23,26], fact='100percent market overlay plus30percent pairs allocation; max portfolio risk is input rmax without numeric value, code available upon request', disposition='Complete shared margin/cash/risk cap and release rules not pinned; no author contacted'),
            dict(kind='date_boundary_requires_clarification', pages=[7], quoted_rows=26257, full_three_year_hours=hours, difference_hours=hours-26257, disposition='26257could describe an inclusive Dec31midnight endpoint; do not assert data loss or silently change boundary'),
        ],
        admitted_as_high_return_reference=False, implementation_admitted=False,
        new_registry_factors=0, native_accounts=0, combinations=0,
        financial_performance=None, previous_copula_source_unchanged=True,
        source_not_repaired_after_reading_returns=True)
    (HERE / 'source_admission.json').write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    print('SOURCE_CONTRACT_NOT_ADMITTED: inconsistent/illustrative account evidence and execution definitions;0newfinancial accounts')


if __name__ == '__main__':
    main()
