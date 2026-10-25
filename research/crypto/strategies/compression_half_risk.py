"""Uniform half-risk comparison; original compression signals and exits are unchanged."""

from compression_retest import CompressionBreakout


class CompressionBreakoutHalfRisk(CompressionBreakout):
    def custom_stake_amount(
        self,
        pair,
        current_time,
        current_rate,
        proposed_stake,
        min_stake,
        max_stake,
        leverage,
        entry_tag,
        side,
        **kwargs,
    ):
        distance = self.distance(self.closed_row(pair, current_time), current_rate)
        if distance is None or leverage != 2.0:
            return 0.0
        fee = float(self.config["fee"])
        sign = -1 if side == "short" else 1
        loss = distance + fee + fee * (1 - sign * distance)
        capital = self.wallets.get_total_stake_amount()
        stake = min(capital * 0.10, capital * 0.0025 / (loss * leverage), max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0


class CompressionRetestHalfRisk(CompressionBreakoutHalfRisk):
    use_retest = True
