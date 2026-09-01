"""Fixed public Phase 5 source catalog."""

from btc_sentinel.news.models import SourceSpec, SourceTier

FED_MONETARY = SourceSpec(
    "fed_monetary",
    "Federal Reserve monetary-policy releases",
    "https://www.federalreserve.gov/feeds/press_monetary.xml",
    SourceTier.OFFICIAL,
    True,
    "rss",
)
FED_SPEECHES = SourceSpec(
    "fed_speeches",
    "Federal Reserve speeches",
    "https://www.federalreserve.gov/feeds/speeches.xml",
    SourceTier.OFFICIAL,
    False,
    "rss",
)
SEC_RELEASES = SourceSpec(
    "sec_releases",
    "SEC press releases",
    "https://www.sec.gov/news/pressreleases.rss",
    SourceTier.OFFICIAL,
    True,
    "rss",
)
SEC_STATEMENTS = SourceSpec(
    "sec_statements",
    "SEC speeches and statements",
    "https://www.sec.gov/news/speeches-statements.rss",
    SourceTier.OFFICIAL,
    False,
    "rss",
)
BLS_CALENDAR = SourceSpec(
    "bls_calendar",
    "BLS release calendar",
    "https://www.bls.gov/schedule/news_release/bls.ics",
    SourceTier.OFFICIAL,
    True,
    "ical",
)
COINBASE_STATUS = SourceSpec(
    "coinbase_status",
    "Coinbase status incidents",
    "https://status.coinbase.com/history.rss",
    SourceTier.OFFICIAL,
    False,
    "rss",
)
GDELT_DISCOVERY = SourceSpec(
    "gdelt_discovery",
    "GDELT news discovery",
    "https://api.gdeltproject.org/api/v2/doc/doc",
    SourceTier.AGGREGATOR,
    False,
    "json",
)

OFFICIAL_FEEDS = (
    FED_MONETARY,
    FED_SPEECHES,
    SEC_RELEASES,
    SEC_STATEMENTS,
    BLS_CALENDAR,
    COINBASE_STATUS,
)
