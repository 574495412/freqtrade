"""Register unchanged hypothesis with source-exact arithmetic; retain all v1 runs."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path):
    return {'path': str(path), 'sha256': sha(path)}


def main():
    if (HERE/'plan_v2.json').exists():
        raise ValueError('Preserve correction registration')
    original = json.loads((HERE/'implementation_freeze.json').read_text())
    feature = ROOT/'freqtrade/data/ordinal_entropy.py'
    archive = HERE/'implementation_v001/ordinal_entropy.py'
    for path,digest in original.items():
        actual = archive if path == str(feature) else Path(path)
        if sha(actual) != digest:
            raise ValueError('Original frozen implementation differs: '+path)
    if '5 passed' not in (HERE/'tests_v2.log').read_text():
        raise ValueError('Exact source gate and causality tests required')
    plan = json.loads((HERE/'plan.json').read_text())
    plan.update(schema='local-entropy-trendcore-native-source-exact-v2',
                registered_at=datetime.now(UTC).isoformat(),
                output_root=str(Path(plan['output_root'])/'attempt2'))
    for candidate in plan['candidates']:
        candidate['source_files']['OrdinalEntropy.py'] = record(feature)
    extras = [HERE/name for name in [
        'correction_protocol_v2.json','helper_revision_v2.json','test_exact_gate.py',
        'analyze_v2.py','audit_features_v2.py','preflight_v2.py','run_v2.py',
        'prepare_plan_v2.py','analysis_inputs_freeze.json','gate_rounding_diagnostic.json',
    ]]
    plan['additional_source_seals'] += [record(path) for path in extras]
    Path(plan['output_root']).mkdir(exist_ok=False)
    (HERE/'plan_v2.json').write_text(json.dumps(plan,indent=2)+'\n')
    files=[Path(path) for path in original]+extras+[archive,HERE/'implementation_freeze.json',
                                               HERE/'plan_v2.json',HERE/'tests_v2.log']
    (HERE/'implementation_freeze_v2.json').write_text(json.dumps(
        {str(path):sha(path)for path in files},indent=2)+'\n')
    print('Source-exact correction registered:4reruns;4v1attemptspreserved;0rulechanges')


if __name__ == '__main__':
    main()
