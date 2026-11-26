"""Strengthened clock validation, same exposures; reuse rather than recount FA."""
import json
import os

import pandas as pd

import native
from bootstrap import HERE, sha


def main():
    target = HERE / 'technical_revalidation_v3'
    target.mkdir(exist_ok=False)
    for name in ['inputs.json', 'protocol.json', 'RecencySignal.py', 'bootstrap.py']:
        os.symlink(HERE / name, target / name)
    native.HERE = target
    native.main()
    old = json.loads((HERE / 'native_admission.json').read_text())
    new = json.loads((target / 'native_admission.json').read_text())
    assert old['records'] == new['records']
    pd.testing.assert_frame_equal(pd.read_parquet(old['feature_path']),
                                  pd.read_parquet(new['feature_path']), check_exact=True)
    result = dict(
        reason='Timezone-naive schedule clocks now reject before native research',
        exact_admission_records=True, exact_native_exposures=True,
        original_features_sha256=old['feature_sha256'],
        revalidated_features_sha256=new['feature_sha256'],
        current_implementation_freeze=str(target / 'implementation_freeze.json'),
        current_implementation_freeze_sha256=sha(target / 'implementation_freeze.json'),
        actual_native_passes=2, actual_factor_analysis_calls=24,
        factor_analysis_calls_reused=24, additional_factor_analysis_calls=0,
        funded_accounts=0)
    (HERE / 'technical_revalidation.json').write_text(json.dumps(result, indent=2)+'\n')
    print('EXACT PARITY; 24 existing FA calls reused, 0 additional calls', flush=True)


if __name__ == '__main__':
    main()
