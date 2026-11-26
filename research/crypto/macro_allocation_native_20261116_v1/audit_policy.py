"""Independent raw-scalar source state reconciliation; no candidate calculator import."""
from datetime import UTC,datetime,timedelta
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

HERE=Path(__file__).absolute().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text())
    records=json.loads((HERE.with_name('macro_transmission_source_native_20261116_v1')/'macro_http_receipts.json').read_text())
    sources=[]
    for row in records:
        r=json.loads(Path(row['path']).read_text())['chart']['result'][0]
        zone=ZoneInfo(r['meta']['exchangeTimezoneName']);observations=[]
        for epoch,value in zip(r['timestamp'],r['indicators']['quote'][0]['close'],strict=True):
            day=datetime.fromtimestamp(epoch,UTC).astimezone(zone).date()
            ready=datetime.combine(day+timedelta(days=1),datetime.min.time(),UTC)+timedelta(hours=6)
            if value is not None:observations.append((ready,float(value)))
        sources.append(observations)
    checks=[]
    for label in ['macro','full','hold']:
        directory=Path(plan['output_root'])/('preflight_'+label+'_1000_conservative')
        policy=json.loads((directory/'strategy_policy.json').read_text())
        assert len(policy)==(1 if label=='hold' else 609)
        for row in policy:
            at=datetime.fromisoformat(row['clock']);expected=1.
            if label=='macro':
                endpoints=[]
                for values in sources:
                    for clock in [at,at-timedelta(days=10)]:
                        ready,value=max((d,v) for d,v in values if d<=clock)
                        assert (clock-ready).total_seconds()/3600<=120
                        endpoints.append(value)
                expected=.25 if endpoints[0]>endpoints[1] and math.log(endpoints[2])>math.log(endpoints[3]) else 1.
            assert row['fraction']==expected
        checks.append(dict(method=label,actual_native_policy_rows=len(policy),scalar_raw_quote_exact=True))
    result=dict(passed=True,methods=checks,actual_policy_rows=sum(r['actual_native_policy_rows'] for r in checks),
        historical_firstseen_certified=False,new_financial_starts=0,candidate_calculator_imported=False)
    (HERE/'policy_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
