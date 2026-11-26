"""Bounded actual-input audit, not an exhaustive inventory of all possible data."""
import csv
import hashlib
import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
PARENT=HERE.parent/'net_nav_native_20261114_v1/customer_input_admission.json'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
parent=json.loads(PARENT.read_text());rows=[]
for r in parent['records']:
 p=Path(r['source']);assert sha(p)==r['sha256']
 with p.open()as f:columns=next(csv.reader(f))
 candidates=[c for c in columns if any(s in c.lower()for s in('div','exdt','cd1','cd2','cd3','permno','payment','withhold'))]
 required={'permno','cd1','cd2','cd3','divamt','exdt'}
 rows.append({'symbol':r['symbol'],'path':str(p),'sha256':r['sha256'],'rows_source_receipt_verified':True,
  'relevant_columns':candidates,'source_distribution_schema_present':required<=set(columns),
  'status':'NO_ADMITTED_FREQUENCY_CODED_DISTRIBUTION_PANEL'})
assert len(rows)==514
out={'scope':'same514 original input CSVs; not every file, all SEC/HKEX notices or a claim that external dividend data does not exist',
 'parent_receipt':str(PARENT),'parent_sha256':sha(PARENT),'window':['2025-01-01','2026-08-31'],
 'us_count':503,'hk_count':11,'source_distribution_schema_count':sum(r['source_distribution_schema_present']for r in rows),
 'known_zero_distribution_history_count':0,'unknown_panel_count':len(rows),'records':rows,
 'source_required':['CRSPdistributions: permno,cd1,cd2,cd3,divamt,exdt','SignalMasterTable: permno,time_avail_m'],
 'forbidden_substitutes':['current dividend yield','inferring frequency code from observed intervals','absence of a cached notice treated as no dividend','current revised dividend schedule as historical first-seen'],
 'registry_calls':0,'factor_analysis_calls':0,'historical_accounts':0,'performance':None}
(HERE/'input_admission.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items()if k not in('records','source_required','forbidden_substitutes')}))
