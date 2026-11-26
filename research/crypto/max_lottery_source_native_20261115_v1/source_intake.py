"""Record reviewed source scope and limits; never execute an external account engine."""
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent


class Plain(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.skip=0

    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:self.skip+=1

    def handle_endtag(self,tag):
        if tag in {'script','style'}and self.skip:self.skip-=1

    def handle_data(self,data):
        if not self.skip:self.parts.append(data)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    source=HERE/'sources/article.html'
    parser=Plain();parser.feed(source.read_text())
    text=' '.join(' '.join(parser.parts).split())
    (HERE/'sources/article.visible.txt').write_text(text)
    assert '28 trading days' in text and '7 and 28' in text
    assert 'positive and statistically significant' in text
    assert 'January 2014' in text and 'September 2020' in text
    assert 'Creative Commons Attribution 4.0' in text
    source_code=(HERE/'sources/MaxRet.py').read_text()
    assert 'to_period("M")'in source_code and '.max()'in source_code
    receipt=[]
    url='https://www.douyin.com/shipin/7644368945084237874'
    try:
        response=requests.get(url,timeout=25)
        p=HERE/'sources/douyin.html';p.write_bytes(response.content)
        receipt.append(dict(url=url,path=str(p),status=response.status_code,bytes=len(response.content),sha256=sha(p)))
    except requests.RequestException as exc:receipt.append(dict(url=url,error=str(exc)))
    (HERE/'social_receipts.json').write_text(json.dumps(receipt,indent=2)+'\n')
    previous=HERE.parent/'residual_momentum_source_native_20261114_v1/source_intake.json'
    data=dict(
        primary=dict(url='https://link.springer.com/article/10.1186/s40854-021-00291-9',
            path=str(source),sha256=sha(source),doi='10.1186/s40854-021-00291-9',
            published='2021-09-08',license='CC BY4.0',
            read_scope='Introduction, methods/variable construction, univariate portfolio section, conclusion, data availability and footnotes; not every table or author implementation audited',
            rule='Maximum of28completed daily returns, weekly7day forecast; positive direction from crypto paper, footnote9pins length',
            author_code_and_dataset='Upon request; not obtained, no author contacted',
            source_samples='CoinMarketCap spot2014Jan-2020Sep, cap>5mUSD and6monthseasoning; not reproduced by local fixed40perpetuals',
            return_definition='Close-to-close simple price return is explicit local convention; paper labels daily R without full source-data/code contract',
            source_claims_used_as_local_accounts=False),
        github=dict(url='https://github.com/OpenSourceAP/CrossSection/blob/9f053398c4995651d822e90d81526d0644c6f3e5/Signals/pyCode/Predictors/MaxRet.py',
            commit='9f053398c4995651d822e90d81526d0644c6f3e5',license='GPL-2.0',
            complete_reviewed_file=True,external_code_executed=False,
            scope='Stocks CRSP calendar-month grouped maximum. It does not implement crypto28day weekly timing or an account',
            no_stock_direction_copied=True,no_external_account_engine_adopted=True),
        local_related_definitions=[dict(name='core_equity_max_month',scope='negative calendar-month equity MAX, separate market/clock'),
            dict(name='core_equity_neg_max_return_7',scope='negative7session log-return stock MAX, separate source/contract'),
            dict(name='skew_vol annual360',scope='older local lottery/skewness family, remains failed/UNKNOWN as documented; not repaired')],
        x_twitter=dict(new_max_rule='Searches did not yield a directly reviewed complete MAX algorithm/account',
            previous_intake=str(previous),previous_sha256=sha(previous),previous_access_explicitly_reused=True,
            historical_sentiment_inputs_not_used=True,new_tweets_or_strategy_accounts_admitted=False),
        youtube=dict(new_max_rule='No complete verified MAX transcript/rule set obtained; no video watched',
            previous_intake=str(previous),previous_sha256=sha(previous),previous_metadata_status_reused=True,
            full_transcript_read=False,new_strategy_admitted=False),
        douyin=dict(url=url,platform_display_name='自由琦谈',
            reviewed='Web tool read the main platform AI transcript paragraph for factor combination, not the audio/video; direct response stored separately',
            takeaway='Equal-weight feature baseline, linear fit overfitting concerns, and factor combo distinct from funded risk-constrained portfolio',
            limitations='Automatic transcript has recognition errors; no reproducible full numerical formulas, input vintage or cash ledger. Promotional returns not adopted',
            related_video_paragraphs_not_adopted=True,video_watched=False,strategy_admitted=False),
        eos_followup=dict(url='https://gist.github.com/hoangtuanictvn/71530b7dc55c514597b24582636dbb84',
            reviewed='Web header and market-type fields; serverTime1612072950783 and isSpotTradingAllowed=true, permissionsSPOT/MARGIN',
            excluded='2021 spot snapshot cannot certify actual2025 EOSUSDT perpetual minima; Copula metadata remains unadmitted'),
        new_accepted_alphas=0,financial_accounts=0,production_approval=False)
    (HERE/'source_intake.json').write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
    print('SOURCE CONTRACT AUDITED:28days/7days/positive crypto direction; social limits retained;0external account engines')


if __name__=='__main__':main()
