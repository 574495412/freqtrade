"""Verify frozen-quantity funding bounds without account or strategy execution."""
from decimal import Decimal

import numpy as np

from audit import one_book

D = Decimal


def test_ordering_bound_includes_fill_between_hour_and_exact_event():
    fills = {0:[D('2')], 100:[D('-1')]}
    cash, ambiguous, ties = one_book(fills,[103],[100],[0.01],np.array([100,103,105]))
    np.testing.assert_allclose(cash[:,:2],0.)
    np.testing.assert_allclose(cash[:,2],[-.01,-.01,-.02,-.01])
    assert ambiguous==1 and ties==0


def test_same_millisecond_exact_tie_is_booked_once_with_signed_funding():
    fills = {0:[D('2')], 100:[D('-2')]}
    cash, ambiguous, ties = one_book(fills,[100],[100],[-0.01],np.array([100,105]))
    np.testing.assert_allclose(cash[:,0],0.)
    np.testing.assert_allclose(cash[:,1],[.02,0.,0.,.02])
    assert ambiguous==1 and ties==1


def test_multiple_same_clock_fills_allow_partial_ordering_envelope():
    fills = {0:[D('2')], 100:[D('3'),D('-2')]}
    cash, _, _ = one_book(fills,[100],[100],[0.01],np.array([105]))
    np.testing.assert_allclose(cash[:,0],[-.02,-.03,-.05,0.])


def test_event_at_terminal_observation_is_not_booked_in_its_prefix():
    cash, _, _ = one_book({0:[D('1')]},[100],[100],[0.01],np.array([95,100]))
    np.testing.assert_allclose(cash,0.)
