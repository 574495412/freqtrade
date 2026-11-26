"""Pin exact primary pair rules and keep social leads below evidence admission."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMIT = '78de2ccd1e8d1544bd69f1b0494493f56559ec12'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def defaults(path, class_name):
    tree = ast.parse(path.read_text())
    owner = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==class_name)
    init = next(n for n in owner.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
    keys = [n.arg for n in init.args.args][-len(init.args.defaults):]
    return dict(zip(keys, [ast.unparse(n) for n in init.args.defaults],strict=True))


def main():
    base = HERE/'sources/BasePairsTradingAlphaModel.py'
    pearson = HERE/'sources/PearsonCorrelationPairsTradingAlphaModel.py'
    source = base.read_text()
    checks = dict(base_defaults=defaults(base,'BasePairsTradingAlphaModel'),
        pearson_defaults=defaults(pearson,'PearsonCorrelationPairsTradingAlphaModel'),
        ema_500_in_actual_code='ExponentialMovingAverage(500)' in source,
        percent_threshold_actual='1 + threshold / 100' in source and '1 - threshold / 100' in source,
        misleading_two_std_comment_present='2 std' in source,
        insight_group_actual='Insight.group(short_asset_1, long_asset_2)' in source,
        source_executed_in_lean=False,source_executed_in_freqtrade=False)
    if not checks['ema_500_in_actual_code'] or not checks['percent_threshold_actual']:
        raise ValueError('Pinned source semantics changed')
    records=[]
    for path in [base,pearson,HERE/'sources/LEAN_LICENSE']:
        name = 'LICENSE' if path.name=='LEAN_LICENSE' else 'Algorithm.Framework/Alphas/'+path.name
        records.append(dict(path=str(path),sha256=sha(path),
            url='https://github.com/QuantConnect/Lean/blob/'+COMMIT+'/'+name))
    result = dict(scope='Latest user request is HK/US equities through Freqtrade; crypto campaigns are separate',
        github=dict(repository='https://github.com/QuantConnect/Lean',commit=COMMIT,
            license='Apache-2.0',files=records,rule_audit=checks,
            decision='Input/execution admission pending; not an accepted factor or reproduced portfolio',
            requirements=['source minute histories','500 observations of ratio indicator',
                'documented historical universe and pair-selection clocks',
                'borrow availability/cost/recall and short dividend liability',
                'two-leg group sizing and partial-fill handling in native account']),
        social_leads=[
            dict(platform='YouTube',url='https://www.youtube.com/watch?v=uhuwErm1no8',
                owner='QuantConnect',evidence='Search-exposed title/description only; video fetch failed',
                mechanism='pairs relative-price convergence',
                decision='Discovery lead only; current pinned GitHub model is not proved to be the video code'),
            dict(platform='X/Twitter',url='https://x.com/MatiasScalbi/status/2063042609816252666',
                evidence='Readable social summary; original historical paper/account not verified here',
                mechanism='normalized-price distance pairs',decision='Do not adopt quoted returns or conflate with QC percentage bands'),
            dict(platform='Douyin',url='https://www.douyin.com/video/7673858811899284751',
                evidence='Search-exposed text only; direct page has no readable lines; video not watched',
                mechanism='index-weight-concentrated SPMO momentum',
                decision='No admission: exact historical holdings files, publication clocks and reconstruction unavailable; retrospective weights are not point-in-time inputs')],
        newly_accepted_alphas=0,factor_analysis_calls=0,native_funded_accounts=0,
        observed_higher_comparable_equity_return=None)
    (HERE/'source_intake.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print('PRIMARY SOURCE PINNED; 3 social leads qualified; 0 accepted alphas/accounts')


if __name__=='__main__':
    main()
