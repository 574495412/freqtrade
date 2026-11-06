"""Reproduce the six saved short-window holding and exposure descriptions."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = HERE.parent / 'factor_cost_futures_long_20261106/account_details.py'
spec = importlib.util.spec_from_file_location('frozen_account_details', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    folder = HERE.parent / 'factor_cost_futures_20261106'
    plan = module.read(folder / 'plan.json')
    audits = module.read(folder / 'results.json')['rows']
    for name, digest in plan['input_hashes'].items():
        assert module.sha(ROOT / name) == digest, name
    records = []
    for row in plan['runs']:
        audit = dict(next(a for a in audits if a['run_id'] == row['id']))
        directory = ROOT / 'user_data/crypto_quant/factor_cost_scenario_20261106' / row['id']
        audit['result_sha256'] = module.sha(directory / 'result.json')
        detail = module.describe(row, audit)
        assert detail['holding']['natural_completed_campaigns'] == audit['result']['natural_campaigns']
        assert detail['holding']['terminal_open_campaigns'] == audit['result']['terminal_open_campaigns']
        records.append(detail)
    result = dict(rows=records, source_hashes={str(p.relative_to(ROOT)): module.sha(p)
        for p in [SOURCE, folder / 'results.json', folder / 'plan.json']},
        account_count=6, new_financial_runs=0, production_eligible=False)
    output = HERE / 'futures_short_account_details.json'
    if args.check:
        assert module.read(output) == result
        print('Six saved account descriptions independently regenerated identically')
    else:
        with output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')


if __name__ == '__main__':
    main()
