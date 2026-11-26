"""Read-only hourly observation before the first native detail event at that clock.

Never change, consume ahead, reorder, or suppress a native yielded event.
"""
import pandas as pd


def attach(account, observer, start, end, capital):
    last = None
    edge_start, edge_end = pd.Timestamp(start), pd.Timestamp(end)
    operation = account._time_pair_generator_det

    def observed(*args, **kwargs):
        nonlocal last
        for event in operation(*args, **kwargs):
            clock = pd.Timestamp(event[0])
            if clock.minute == 0 and clock.second == 0 and edge_start <= clock <= edge_end:
                if last is None or clock > last:
                    observer.capture(clock)
                    last = clock
            yield event

    account._time_pair_generator_det = observed
    return {'policy': 'before first yielded detail event at each hour; all original events yielded unchanged',
            'start': str(edge_start), 'end': str(edge_end), 'capital': capital}


def complete_initial_cash(frame, start, first_event, capital):
    """Only the known initial no-event interval may be filled with starting cash."""
    start, first_event = pd.Timestamp(start), pd.Timestamp(first_event)
    before = pd.date_range(start, first_event, freq='h', inclusive='left')
    if not len(before):
        return frame
    if (frame.date < first_event).any():
        raise ValueError('Pre-event rows already observed')
    rows = pd.DataFrame({'date': before, 'equity': capital, 'hourly_mark_equity': capital,
                         'open_trades': 0, 'gross': 0, 'closed_realized_pnl': 0})
    return pd.concat([rows, frame], ignore_index=True)
