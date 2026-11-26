"""Account-free comparison of source unknowns with native default gap filling."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
plan = json.loads((HERE/'plan.json').read_text())
sys.path.insert(0, plan['native_source'])
import numpy as np
import pandas as pd
from freqtrade.data.history.history_utils import load_pair_history


def main():
    records = []
    for record in plan['records']:
        raw = pd.read_parquet(record['staged']).set_index('date')
        default = load_pair_history(record['pair'],'1d',HERE/'native_history').set_index('date')
        aligned = default.reindex(raw.index)
        columns = ['open','high','low','close','volume']
        missing = ~np.isfinite(raw[columns].to_numpy(dtype=float))
        filled = missing & np.isfinite(aligned[columns].to_numpy(dtype=float))
        invalid = missing.any(axis=1)
        records.append(dict(symbol=record['symbol'],calendar=record['calendar'],
            source_unknown_cells=int(missing.sum()),default_filled_unknown_cells=int(filled.sum()),
            source_unknown_rows=int(invalid.sum()),
            first_unknown_session=raw.index[invalid][0].isoformat() if invalid.any() else None,
            last_unknown_session=raw.index[invalid][-1].isoformat() if invalid.any() else None))
    result = dict(purpose='Input diagnostic only; not indicators, alpha or returns',records=records,
        unknown_cells=sum(r['source_unknown_cells'] for r in records),
        default_filled_unknown_cells=sum(r['default_filled_unknown_cells'] for r in records))
    (HERE/'unknown_diagnostic.json').write_text(json.dumps(result,indent=2)+'\n')
    print({k:v for k,v in result.items() if k!='records'})


if __name__ == '__main__':
    main()
