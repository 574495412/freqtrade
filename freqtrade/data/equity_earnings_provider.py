"""Read-only earnings inputs exposed on the owning native stock DataProvider."""

from freqtrade.data.equity_earnings import consensus_surprise
from freqtrade.data.equity_provider import EquityDataProvider
from freqtrade.data.history.equity import EquityAdmissionError


class EquityEarningsDataProvider(EquityDataProvider):
    """Local input extension; no account, factor registration or orders."""

    def __init__(self, config, schedules, observations):
        super().__init__(config, schedules)
        self._earnings_observations = tuple(observations)
        identities = set()
        for actual, forecast in self._earnings_observations:
            if actual.pair not in schedules:
                raise EquityAdmissionError("Earnings security outside frozen provider universe")
            contract = schedules[actual.pair].contract
            price_identity = contract.calendar, contract.pair.rsplit("/", 1)[-1]
            if (actual.market, actual.currency) != price_identity:
                raise EquityAdmissionError("Earnings market/currency differs from price contract")
            key = actual.pair, actual.event_id
            if key in identities:
                raise EquityAdmissionError("Revision selection requires an explicit new contract")
            identities.add(key)
            # Validate identity even if every numeric value is not yet available.
            consensus_surprise(actual, forecast, at=contract.index()[0].to_pydatetime())

    def get_earnings_surprises(self, pair):
        self._check(pair, "1d")
        if self._equity_asof is None:
            raise EquityAdmissionError("Native callback clock required before earnings access")
        result = []
        for actual, forecast in self._earnings_observations:
            if actual.pair != pair:
                continue
            # Future and undated events do not expose their existence to a callback.
            if actual.observed_at is None or actual.observed_at > self._equity_asof:
                continue
            result.append(consensus_surprise(actual, forecast, at=self._equity_asof))
        return result
