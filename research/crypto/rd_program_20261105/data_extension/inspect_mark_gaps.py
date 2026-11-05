"""Preserve and inspect two preregistered failed archive payloads, without fills."""
import io
import json
from pathlib import Path
import zipfile

import pandas as pd

import download_mark5m as source


def main():
    path=source.HERE/'gap_probe_registration.json'
    plan=json.loads(path.read_text())
    if source.sha(Path(__file__).read_bytes())!=plan['script_sha256']:
        raise ValueError('Diagnostic code changed')
    output=source.ROOT/plan['output_directory'];output.mkdir(parents=True,exist_ok=False)
    reports=[]
    for job in plan['jobs']:
        payload,receipt=source.download(job['url'],source.MAX_ARCHIVE_BYTES)
        checksum,check_receipt=source.download(job['url']+'.CHECKSUM',4096)
        if checksum.decode().split()[0]!=source.sha(payload):raise ValueError('Checksum differs')
        # Preserve original bytes even when the later date-grid check fails.
        archive_path=output/job['filename'];archive_path.write_bytes(payload)
        (output/(job['filename']+'.CHECKSUM')).write_bytes(checksum)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            info=archive.infolist()
            if len(info)!=1 or info[0].file_size>source.MAX_CSV_BYTES:raise ValueError('ZIP bounds')
            with archive.open(info[0]) as stream:frame=pd.read_csv(stream)
        dates=pd.DatetimeIndex(pd.to_datetime(frame.open_time,unit='ms',utc=True))
        expected=pd.date_range(job['start'],job['end_exclusive'],freq='5min',inclusive='left')
        missing=expected.difference(dates);extra=dates.difference(expected)
        report={'job':job,'download':receipt,'checksum':check_receipt,'official_checksum_verified':True,
                'rows':len(frame),'expected_rows':len(expected),'unique_dates':len(dates.unique()),
                'first':dates.min().isoformat(),'last':dates.max().isoformat(),
                'missing_count':len(missing),'extra_count':len(extra),'duplicate_count':int(dates.duplicated().sum()),
                'ordered':dates.is_monotonic_increasing,'missing':[x.isoformat() for x in missing],
                'extra':[x.isoformat() for x in extra],
                'interval_counts_seconds':{str(k.total_seconds()):int(v) for k,v in pd.Series(dates).diff().value_counts().items()},
                'first_rows':frame.head(2).to_dict(orient='records'),'last_rows':frame.tail(2).to_dict(orient='records'),
                'financial_runs':0,'orders_enabled':False,'data_repaired':False}
        reports.append(report)
        print(json.dumps({k:report[k] for k in ['rows','expected_rows','first','last','missing_count','extra_count','duplicate_count','interval_counts_seconds']}))
    source.save_new(output/'gap_report.json',{'registration_sha256':source.sha(path.read_bytes()),'reports':reports,
        'initial_acquirer_limitation':'v001 kept failure receipts but wrote ZIP bytes only after validation; invalid-grid payloads were not retained. This independent probe preserves those bytes, without rewriting v001 evidence.'})


if __name__=='__main__':main()
