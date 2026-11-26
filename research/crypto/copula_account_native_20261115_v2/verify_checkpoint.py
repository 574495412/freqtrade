"""Recheck sealed model preparation; this never certifies funded economics."""
import json
from pathlib import Path
from .nominate import HERE,ROOT,OLD,OUT
from .fit_native import sha

def main():
    manifest=json.loads((HERE/'artifact_manifest.json').read_text())
    for row in manifest['files']:
        p=Path(row['path']);assert p.is_file()and p.stat().st_size==row['bytes']and sha(p)==row['sha256'],str(p)
    parent=json.loads((OLD/'artifact_manifest.json').read_text())
    for row in parent['files']:assert sha(row['path'])==row['sha256'],row['path']
    runtime=json.loads((OLD/'runtime_receipt.json').read_text());m=runtime['upstream_source_manifest'];assert sha(m['path'])==m['sha256'];original=json.loads(Path(m['path']).read_text());engine=ROOT.parent/'CZ-Agent/vendor/freqtrade'
    assert len(original['files'])==595 and all(sha(engine/n)==digest for n,digest in original['files'].items())
    assert {str(p.relative_to(engine))for p in engine.rglob('*.py')}=={n for n in original['files']if n.endswith('.py')}
    capsule=json.loads((OUT/'model_capsule.json').read_text())
    for path,digest in capsule['identity'].items():assert sha(path)==digest
    for row in capsule['all_model_files']:assert sha(row['path'])==row['sha256']
    proof=json.loads((HERE/'model_verification.json').read_text());status=json.loads((HERE/'status.json').read_text());metadata=json.loads((HERE/'metadata_admission.json').read_text())
    assert proof['passed_within_model_scope']and proof['native_weekly_records']==88 and proof['fitted_weeks']==61 and proof['insufficient_stationarity_weeks']==27
    assert proof['copula_likelihoods_rechecked']==2379 and proof['marginal_likelihoods_rechecked']==366 and proof['full_hourly_decisions']==14592
    assert len(proof['causal_refits'])==6 and proof['EOS_nonzero_hourly_decisions']=={'10':103,'15':107,'20':154}
    assert not metadata['financial_admission']and status['returns']=='UNKNOWN'and status['native_funded_accounts']==0 and not status['goal_achieved']and status['goal_status']=='active'
    assert not list(OUT.rglob('native_results/*.zip')),'Input checkpoint must not hide a financial run'
    print(json.dumps({'passed_input_model_scope':True,'sealed_paths':len(manifest['files']),'prior_checkpoint_paths_unchanged':len(parent['files']),'source_members':20,'weekly_records':88,'fitted_weeks':61,'funded_accounts':0,'returns':'UNKNOWN','goal_achieved':False,'goal_status':'active'},indent=2))
if __name__=='__main__':main()
