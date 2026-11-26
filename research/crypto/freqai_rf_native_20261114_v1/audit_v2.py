"""Read-only audit of the data-only revision using the unchanged v1 auditors."""
from pathlib import Path
import json
from research.crypto.freqai_rf_native_20261114_v1 import analyze, recheck_forests
HERE=Path(__file__).resolve().parent/'v002'

def main():
    analyze.HERE=HERE
    analyze.main()
    result=json.loads((HERE/'results.json').read_text())
    for account in result['accounts']:
        if account['model'] is not None:
            assert account['model']['train_rows_min']==account['model']['train_rows_max']==700
            assert account['model']['prediction_rows']==14592
    recheck_forests.HERE=HERE
    recheck_forests.main()
if __name__=='__main__':main()
