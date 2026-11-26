"""Preserve missing-fixture-field failure; add explicit zero first-tier offset."""
import copy
import json
from pathlib import Path

from prepare_fixtures import HERE, sha, save


def main():
    original=HERE/'fixture_inputs.json';target=HERE/'fixture_inputs_maintenance_fixed.json'
    assert not target.exists()
    before=json.loads(original.read_text());after=copy.deepcopy(before);changes=[]
    for case in after['cases']:
        old=Path(case['markets']['path']);assert sha(old)==case['markets']['sha256']
        metadata=json.loads(old.read_text())
        for tiers in metadata['leverage_tiers'].values():
            assert len(tiers)==1 and tiers[0]['minNotional']==0 and tiers[0]['info']['cum']==0
            assert 'maintAmt'not in tiers[0]
            tiers[0]['maintAmt']=0.0
        new=old.with_name('synthetic_markets_maintenance_fixed.json');assert not new.exists();save(new,metadata)
        changes.append({'case':case['name'],'original':case['markets'],'current':{'path':str(new),'sha256':sha(new)},'only_added_field':'leverage_tiers[each_pair][0].maintAmt=0.0'})
        case['markets']={'path':str(new),'sha256':sha(new)}
    save(target,after);save(HERE/'fixture_metadata_amendment.json',dict(original_inputs_sha256=sha(original),current_inputs_sha256=sha(target),changes=changes,
        cause='Native liquidation initialization requires normalized maintAmt field; synthetic first tier offset is explicitly zero. No price, state, costs, sizing or source research contract changed.'))
    print('Preserved original fixtures; six synthetic market records receive normalized maintAmt=0')


if __name__=='__main__':main()
