"""Freeze source-based MAX direction and native evaluation contract before results."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def main():
    if (HERE / 'protocol.json').exists():
        raise FileExistsError('A frozen candidate cannot be redefined')
    parent_path = HERE.parent / 'skew_vol_native_20261113_v1/v002/plan.json'
    parent = json.loads(parent_path.read_text())
    protocol = dict(
        frozen_utc=datetime.now(UTC).isoformat(), task='Crypto MAX input/predictive admission, followed by complete native accounts only if gates pass',
        native_commit=parent['engine_commit'], parent_plan=dict(path=str(parent_path), sha256=sha(parent_path)),
        source=dict(doi='10.1186/s40854-021-00291-9', year=2021, authors=['Ozdamar','Akdeniz','Sensoy'],
            formula='MAX_i,t = max of28completed simple daily returns; footnote9defines month28days/week7days',
            direction='positive: higher MAX predicts higher next-week return',
            source_market='CoinMarketCap spot, market cap over5mUSD and six-month seasoning, January2014-September2020',
            source_replication=False, source_original_data_and_code_available=False),
        local_adaptation='Use unchanged existing40 Binance USDT perpetual members and native cached daily close; no source market-cap/seasoning certification. Not author-sample replication or independent OOS',
        pairs=parent['pairs'], datadir=parent['datadir'],
        account_start='2025-01-01T00:00:00Z', account_end='2026-08-31T23:00:00Z',
        decisions='2025-01-01T00:00Z plus multiples of168hours; no calendar-year phase resets',
        information='At D00 use all28daily returns ending D-1 daily bar, completed at D00; no current-day daily extrema or close',
        native_execution='Input-only IStrategy timeframe1h exposes scores on D00 hourly row; native one-bar lag would execute at D01; predictive labels D01OPEN to D+168h01OPEN',
        label='Gross quote simple return with complete endpoints only; not net portfolio or funding return',
        controls={'mom21_skip7':'C[D-8]/C[D-29]-1, source MOM weeks t-4 through t-2',
                  'lowvol28':'negative sample standard deviation of identical28completed daily simple returns'},
        predictive=dict(registry='isolated native Registry views of actual Freqtrade IStrategy/DataProvider scores',
            n_groups=10, splits=['all','calendar2025','calendar2026_through_aug'],
            gate={'ic_mean_gt':.03,'ir_gt':.5,'positive_ratio_gt':.55},
            required='MAX must pass all three predefined splits, finite support and scoped dedup; preserve any failure without sign/window changes'),
        dedup='Same40 MAX versus MOM21skip7, lowvol28, total skew28, existing annual skew360/lowvol63 where available; shared8 residual momentum and shared6 liquidity panels separately, no global independence claim',
        funded_if_admitted=dict(engine='native Configuration/IStrategy/Backtesting.start/Trade/Order/Wallet/actual ZIP and fee/funding/NAV audit',
            capital_usdt=[1000,500], per_side_cost_bps=[15,8], leverage=1,
            allocation='Each week top4MAX long/bottom4short, actual available stake split8; no minimum upsizing or intracycle resize',
            rotation='Exit prior sleeves D01, enter D02 after actual flat check; forecast label is D01 reference only and not identical holding/execution rule',
            portfolio='Two actual500wallets MAX plus lowvol28 only if both predictive gates pass; no scaling1000curve',
            admission='Both cost cases profitable, combo beats component1000wallets, hourly drawdown<=30percent and both continuous calendar slices positive; complete funding gaps/exposure and actual market rules required'),
        unknowns=['Existing AXS/TRB2026-06-24 04UTC funding gap is not zero',
                  'Existing fixed40pool and current market snapshot do not certify historical universe or rules',
                  'Source3.03percent weekly spread is not local self-financing account CAGR'],
        new_accepted_alphas=0, funded_accounts_started=0, orders_enabled=False,
        deployment=False, services_changed=False, no_post_result_retuning=True)
    save(HERE / 'protocol.json', protocol)
    out = HERE / 'sources'
    out.mkdir(exist_ok=False)
    urls = [
        ('article.html','https://link.springer.com/article/10.1186/s40854-021-00291-9'),
        ('MaxRet.py','https://raw.githubusercontent.com/OpenSourceAP/CrossSection/9f053398c4995651d822e90d81526d0644c6f3e5/Signals/pyCode/Predictors/MaxRet.py'),
        ('LICENSE','https://raw.githubusercontent.com/OpenSourceAP/CrossSection/9f053398c4995651d822e90d81526d0644c6f3e5/LICENSE'),
    ]
    receipts = []
    for name, url in urls:
        try:
            response = requests.get(url, timeout=25)
            if len(response.content) > 3*1024**2:
                raise ValueError('Source exceeds bounded capture')
            p = out / name
            p.write_bytes(response.content)
            receipts.append(dict(path=str(p), url=url, status=response.status_code,
                bytes=len(response.content), sha256=sha(p)))
        except (requests.RequestException, ValueError) as exc:
            receipts.append(dict(url=url, error=str(exc)))
        save(HERE / 'source_receipts.json', receipts)
        print(name, receipts[-1].get('status','failed'), flush=True)


if __name__ == '__main__':
    main()
