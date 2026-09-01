from decimal import Decimal
from unittest import TestCase

from btc_sentinel.analysis.models import Direction, PriceZone
from btc_sentinel.analysis.structure import analyze_structure
from btc_sentinel.market_data.enums import MarketInterval
from tests.analysis_fixtures import analysis_series


class StructureTests(TestCase):
    def test_uptrend_structure_has_higher_swings(self) -> None:
        structure = analyze_structure(analysis_series(MarketInterval.ONE_DAY), Decimal("10"))
        self.assertIs(structure.direction, Direction.BULLISH)
        self.assertTrue(structure.higher_highs)
        self.assertTrue(structure.higher_lows)

    def test_downtrend_structure_has_lower_swings(self) -> None:
        structure = analyze_structure(
            analysis_series(MarketInterval.ONE_DAY, slope=Decimal("-0.8")), Decimal("10")
        )
        self.assertIs(structure.direction, Direction.BEARISH)
        self.assertTrue(structure.lower_highs)
        self.assertTrue(structure.lower_lows)

    def test_structure_zones_are_bounded(self) -> None:
        structure = analyze_structure(analysis_series(MarketInterval.FOUR_HOURS), Decimal("10"))
        self.assertLessEqual(len(structure.support_zones), 3)
        self.assertLessEqual(len(structure.resistance_zones), 3)

    def test_support_zones_are_below_latest_close(self) -> None:
        series = analysis_series(MarketInterval.FOUR_HOURS)
        structure = analyze_structure(series, Decimal("10"))
        self.assertTrue(all(zone.lower <= series.latest.close for zone in structure.support_zones))

    def test_zone_rejects_inverted_bounds(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid"):
            PriceZone(Decimal("10"), Decimal("9"), 1)

    def test_zone_requires_a_touch(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid"):
            PriceZone(Decimal("9"), Decimal("10"), 0)
