import unittest
from book_replay import Book,nanoseconds


def snap():return {'lastUpdateId':100,'bids':[['100','2'],['99','4']],'asks':[['100.1','3'],['101','5']]}
def update(U=101,u=102,pu=100,b=None,a=None):return {'U':U,'u':u,'pu':pu,'b':b or [],'a':a or []}


class BookTests(unittest.TestCase):
    def test_submicrosecond_order_preserved(self):
        a=nanoseconds('2025-01-01T00:00:00.1234567Z');b=nanoseconds('2025-01-01T00:00:00.1234568Z')
        self.assertEqual(b-a,100)
        with self.assertRaises(ValueError):nanoseconds('2025-01-01T00:00:00.1234567891Z')

    def test_buffer_not_visible_before_snapshot(self):
        b=Book('spot');b.update(update());self.assertIsNone(b.observation())
        b.snapshot(snap());self.assertEqual(b.observation()['last_update_id'],102)

    def test_spot_gap_fails_closed(self):
        b=Book('spot');b.snapshot(snap());b.update(update())
        with self.assertRaises(ValueError):b.update(update(U=104,u=105))
        self.assertIsNone(b.observation())

    def test_futures_requires_overlap_then_pu_chain(self):
        b=Book('futures');b.snapshot(snap());b.update(update(U=99,u=101))
        b.update(update(U=103,u=105,pu=101))
        with self.assertRaises(ValueError):b.update(update(U=106,u=107,pu=104))

    def test_absolute_quantities_zero_deletion_and_unknown_zero(self):
        b=Book('spot');b.snapshot(snap());b.update(update(b=[['100','7'],['98','0']]))
        self.assertEqual(b.observation()['visible_bid_quantity_within20bps'],'7')
        b.update(update(U=103,u=103,b=[['100','0']]))
        self.assertEqual(b.observation()['bid'],'99')

    def test_sparse_outer_update_does_not_certify_unknown_band(self):
        b=Book('spot');b.snapshot({'lastUpdateId':100,'bids':[['100','2']],'asks':[['100.1','3']]})
        b.update(update(b=[['99','1']],a=[['101','1']]))
        r=b.observation();self.assertFalse(r['bid_band_complete']);self.assertFalse(r['ask_band_complete'])

    def test_disconnect_clears_known_state(self):
        b=Book('spot');b.snapshot(snap());b.update(update());b.reset()
        self.assertIsNone(b.observation())

    def test_crossed_book_rejected(self):
        b=Book('spot');b.snapshot(snap());b.update(update(b=[['100.2','1']]))
        with self.assertRaises(ValueError):b.observation()


if __name__=='__main__':unittest.main()
