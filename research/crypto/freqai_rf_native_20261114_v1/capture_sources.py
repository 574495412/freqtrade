"""Pinned official documents/configuration; no community profitability claims."""

import hashlib
import json
from pathlib import Path

import requests

HERE=Path(__file__).resolve().parent
PIN='c051e9065c5b677f03817dd1f83cb980af1ed684'


def main():
    directory=HERE/'sources';directory.mkdir(exist_ok=True);rows=[]
    for relative in ['docs/freqai-running.md','docs/freqai-configuration.md','docs/freqai-feature-engineering.md',
                     'config_examples/config_freqai.example.json','freqtrade/templates/FreqaiExampleStrategy.py',
                     'freqtrade/freqai/prediction_models/SKLearnRandomForestClassifier.py','LICENSE']:
        url=f'https://raw.githubusercontent.com/freqtrade/freqtrade/{PIN}/{relative}'
        path=directory/Path(relative).name;assert not path.exists()
        result=requests.get(url,timeout=35);path.write_bytes(result.content)
        rows.append(dict(url=url,path=str(path),status=result.status_code,bytes=len(result.content),
                         sha256=hashlib.sha256(result.content).hexdigest()))
        (HERE/'source_manifest.json').write_text(json.dumps(dict(repository='freqtrade/freqtrade',commit=PIN,files=rows),indent=2)+'\n')
        assert result.status_code==200,rows[-1]
    print('7 pinned primary source documents captured')


if __name__=='__main__':main()
