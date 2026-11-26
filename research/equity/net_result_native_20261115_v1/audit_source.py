"""Read-only selected source-function witnesses, not factor or account evaluation."""
import hashlib
import importlib.util
import json
import socket
import sys
import types
from datetime import date
from pathlib import Path

HERE=Path(__file__).resolve().parent


def main():
    receipts=json.loads((HERE/'source_receipts.json').read_text())
    for row in receipts:
        assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256']
    root=HERE/'sources'/'edge_lab'/'form4_scanner'
    package=types.ModuleType('pinned_edge_witness');package.__path__=[str(root)]
    sys.modules[package.__name__]=package
    modules=[]
    for short in ['parse','cluster']:
        name=package.__name__+'.'+short
        spec=importlib.util.spec_from_file_location(name,root/(short+'.py'))
        loaded=importlib.util.module_from_spec(spec);sys.modules[name]=loaded
        spec.loader.exec_module(loaded);modules.append(loaded)
    parse,cluster=modules

    def xml(owners=('1','2'),price='25',footnote=''):
        return '<ownershipDocument><issuer><issuerCik>9</issuerCik><issuerTradingSymbol>ABC</issuerTradingSymbol></issuer>'+''.join(
            '<reportingOwner><reportingOwnerId><rptOwnerCik>'+c+'</rptOwnerCik></reportingOwnerId></reportingOwner>'for c in owners)+(
            '<nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>'
            '<transactionDate><value>2025-01-01</value></transactionDate><transactionCoding><transactionCode>P</transactionCode></transactionCoding>'
            '<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>'+price+
            '</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>'
            '</transactionAmounts></nonDerivativeTransaction></nonDerivativeTable><footnotes><footnote>'+footnote+
            '</footnote></footnotes></ownershipDocument>')
    def rows(*args,accession='a',**kw):
        return parse.parse_ownership_xml(xml(*args,**kw),accession,date(2025,1,2))
    txns=rows();group=cluster.group_by_issuer(parse.open_market_buys(txns,25000))[0]
    assert len(txns)==2 and len(group.economic_txns)==1
    assert group.total_value==25000 and group.distinct_buyers==1
    transitive=cluster.group_by_issuer(txns+rows(('2','3'),accession='b')+rows(('4',),accession='c'))[0]
    assert transitive.distinct_buyers==transitive.max_window_buyers()==2
    assert sorted(map(sorted,transitive.buyer_groups))==[['1','2','3'],['4']]
    missing=rows(price='');assert all(t.price==t.value==0 for t in missing)
    assert not parse.open_market_buys(missing,25000)
    private=rows(footnote='Private purchase pursuant to a negotiated agreement.')
    assert len(parse.open_market_buys(private,25000))==2
    negated=rows(footnote='This transaction was not made pursuant to a Rule 10b5-1 plan.')
    assert all(t.plan_10b5_1 for t in negated) and not parse.open_market_buys(negated,25000)
    result=dict(passed=True,selected_source_functions_executed=['parse_ownership_xml','open_market_buys','IssuerCluster'],
        external_downloader_or_financial_engine_executed=False,synthetic_witnesses_only=True,
        joint_owner_rows=2,underlying_rows=1,economic_total=25000,buyer_groups=1,
        transitive_joint_groups=[['1','2','3'],['4']],independent_decisions_not_authenticated=True,
        missing_numeric_source_behavior='zero,not UNKNOWN;positive threshold excludes this fixture',
        P_private_purchase_text_passes_filter=True,
        negated_10b5_1_text_still_flags=True,
        interpretation='Literal parser witnesses,not observed historical error rates or prediction evidence',
        source_retest_verdict_from_summary='INCONCLUSIVE;not locally replicated',
        factor_analysis_calls=0,accounts=0,accepted_new_alpha=0)
    (HERE/'source_witnesses.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS source parser/co-filer/private/negative-text/missing-value witnesses;0 accounts')


if __name__=='__main__':
    def offline(*args,**kwargs):raise RuntimeError('Selected source witness is offline')
    socket.socket.connect=offline
    main()
