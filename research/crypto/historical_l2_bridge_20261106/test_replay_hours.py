"""Exercise sequence and provenance gates with synthetic adjacent files."""
import pyarrow as pa
import pyarrow.parquet as pq

from replay_hours import HEADER, LEVEL, NS_PER_MS, NS_PER_SECOND, Replay, hour_ns, sha

DAY = '2026-08-31'
SYMBOL = 'BTCUSDT'


def snapshot(at, sequence, levels, *, checkpoint=False, synthetic_clock=False):
    event = at // NS_PER_MS
    if not checkpoint and not synthetic_clock:
        event -= 10
    transaction = None if checkpoint else event - 1
    return [(at, event, transaction, SYMBOL, 'snapshot', None, sequence, None, sequence,
             side, price, quantity, None)
            for side, price, quantity in levels]


def update(at, previous, sequence, *, conflicting_clock=False):
    event = at // NS_PER_MS + (10 if conflicting_clock else -10)
    return [(at, event, event - 1, SYMBOL, 'update', sequence, sequence, previous, None,
             'bid', '99', '2', None)]


def partition(tmp_path, hour, frames):
    row = dict(id=f'hour_{hour:02d}', day=DAY, hour=hour, symbol=SYMBOL,
               http_status=200, state='body_complete')
    start = hour_ns(row)
    rows = [level for frame in frames for level in frame(start)]
    table = pa.table(dict(zip((*HEADER, *LEVEL), zip(*rows, strict=True), strict=True)))
    path = tmp_path / (row['id'] + '.parquet')
    pq.write_table(table, path)
    data = path.read_bytes()
    return dict(row, path=str(path), sha256=sha(data), bytes=len(data))


LEVELS = [('bid', '99', '1'), ('bid', '98', '1'), ('ask', '100', '1'), ('ask', '101', '1')]


def seeded(tmp_path):
    replay = Replay()
    first = partition(tmp_path, 0, [
        lambda at: snapshot(at + NS_PER_SECOND, 10, LEVELS),
        lambda at: update(at + 2 * NS_PER_SECOND, 10, 11),
    ])
    replay.partition(first, [5])
    return replay


def test_advancing_pu_carries_original_boundaries_but_no_quote_before_bridge(tmp_path):
    replay = seeded(tmp_path)
    following = partition(tmp_path, 1, [
        lambda at: update(at + 6 * 60 * NS_PER_SECOND, 11, 12),
        lambda at: update(at + 11 * 60 * NS_PER_SECOND, 12, 13),
    ])
    result = replay.partition(following, [5, 10])
    before, after = (row['book'] for row in result['observations'])
    assert before is None
    assert result['cross_hour_update_pu_matches'] is True
    assert after['last_update_id'] == 12
    assert after['anchor']['partition'] == 'hour_00'
    assert after['bid_band_complete'] and after['ask_band_complete']


def test_wrong_pu_discards_carried_book(tmp_path):
    replay = seeded(tmp_path)
    following = partition(tmp_path, 1, [
        lambda at: update(at + NS_PER_SECOND, 98, 99),
        lambda at: update(at + 6 * 60 * NS_PER_SECOND, 99, 100),
    ])
    result = replay.partition(following, [5])
    assert result['cross_hour_update_pu_matches'] is False
    assert result['counts']['carry_rejected_pu'] == 1
    assert result['observations'][0]['book'] is None


def test_disagreeing_checkpoint_cannot_supply_raw_depth(tmp_path):
    replay = seeded(tmp_path)
    following = partition(tmp_path, 1, [
        lambda at: snapshot(at, 11, LEVELS, checkpoint=True),
        lambda at: update(at + NS_PER_SECOND, 11, 12),
        lambda at: update(at + 6 * 60 * NS_PER_SECOND, 12, 13),
    ])
    result = replay.partition(following, [5])
    quote = result['observations'][0]['book']
    assert result['counts']['checkpoint_disagreement'] == 1
    assert quote['anchor']['kind'] == 'hour_checkpoint'
    assert not quote['bid_band_complete'] and not quote['ask_band_complete']


def test_synthetic_snapshot_cannot_seed_an_unanchored_partition(tmp_path):
    replay = Replay()
    following = partition(tmp_path, 1, [
        lambda at: snapshot(at + NS_PER_SECOND, 11, LEVELS, synthetic_clock=True),
        lambda at: update(at + 2 * NS_PER_SECOND, 11, 12),
        lambda at: update(at + 6 * 60 * NS_PER_SECOND, 12, 13),
    ])
    result = replay.partition(following, [5])
    assert result['snapshots'][0]['status'] == 'excluded_clock'
    assert result['observations'][0]['book'] is None
