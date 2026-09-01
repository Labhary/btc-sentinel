"""Safe Phase 5 boundary errors."""

from btc_sentinel.errors import BtcSentinelError


class NewsError(BtcSentinelError):
    """Base class for expected news-engine failures."""


class NewsValidationError(NewsError):
    """A feed or record violated the news schema."""


class NewsTransportError(NewsError):
    """A fixed public news endpoint could not be read safely."""
