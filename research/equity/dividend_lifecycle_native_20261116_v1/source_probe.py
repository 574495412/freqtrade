"""Offline witnesses for two reviewed source modules; no source account engine."""
import importlib.util
import json
import socket
from pathlib import Path

import pandas as pd

HERE = Path(__file__).absolute().parent


def load(name):
    path = HERE / 'sources/studies/143-dividend-capture/dividend_capture' / (name + '.py')
    spec = importlib.util.spec_from_file_location('reviewed_dividend_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def offline(*args, **kwargs):
    raise RuntimeError('Source witnesses cannot access network')


def main():
    socket.socket.connect = offline
    source = load('strategy')
    data = load('data')
    events = pd.DataFrame(dict(price_before=[100.] * 20, price_after=[90.] * 20,
                               div=[10.] * 20, ret_gross=[0.] * 20))
    capture = source.capture_trade_stats(events, cost_bps=0.)
    price_only = source.buy_and_hold_stats(events)
    assert capture['mean_gross_bps'] == 0
    assert abs(price_only['mean_bah_bps'] + 1000) < 1e-10
    # Repeated positive event returns remain positive under the source's fallback.
    planted = events.copy()
    planted['ret_gross'] = .1
    shuffled = source.shuffled_date_control(planted, price_series=None, n_draws=100)
    assert abs(shuffled['mean_ctrl_bps'] - 1000) < 1e-10
    outputs = []
    for name, opening in [('original', 100.), ('altered_open', 999.)]:
        cache = HERE / 'source_witnesses' / name
        cache.mkdir(parents=True, exist_ok=False)
        frame = pd.DataFrame(dict(Open=[100., opening, 90.], Close=[100., 90., 90.],
                                  Dividends=[0., 10., 0.]),
                             index=pd.to_datetime(['2025-01-02', '2025-01-03', '2025-01-06']))
        frame.to_parquet(cache / 'div_cap2_SYN_daily.parquet')
        result = data.fetch_events(tickers=['SYN'], fetch=False, cache_dir=str(cache))
        result.to_parquet(cache / 'source_events.parquet')
        outputs.append(result)
    pd.testing.assert_frame_equal(*outputs)
    assert outputs[0].iloc[0].ret_gross == 0
    receipt = dict(passed=True, synthetic_only=True, source_account_engine_executed=False,
                   reviewed_modules=['data.py', 'strategy.py'],
                   baseline_witness=dict(capture_gross_bps=capture['mean_gross_bps'],
                                         source_price_only_bah_bps=price_only['mean_bah_bps'],
                                         same_entitled_holding_total_return_bps=0,
                                         conclusion='source baseline omits entitled cash; not fair total-return control'),
                   fallback_control_witness=dict(planted_event_bps=1000,
                                                  source_bootstrap_bps=shuffled['mean_ctrl_bps'],
                                                  conclusion='resamples event returns; does not remove planted ex-event mean'),
                   event_price_witness=dict(ex_open_values=[100., 999.], exact_event_output=True,
                                             conclusion='source extracts closes,not an actual ex-open entry or eligibility proof'),
                   missing_contracts=['announcement clock', 'tax/withholding', 'payment availability',
                                      'whole-account capital allocation', 'settlement', 'historical company actions'],
                   author_negative_verdict_preserved=True, historical_replication=False,
                   new_accepted_alpha=0, account_returns_admitted=False)
    (HERE / 'source_witnesses.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('PASS 3 source semantic witnesses;0 historical source/account returns')


if __name__ == '__main__':
    main()
