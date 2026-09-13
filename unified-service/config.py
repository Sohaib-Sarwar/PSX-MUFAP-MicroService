"""
PK Finance Unified Service - Configuration
============================================
Combined configuration for MUFAP Mutual Funds + PSX Stock Exchange.
"""

import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# Timezone: UTC+5 (Pakistan Standard Time)
UTC5 = timezone(timedelta(hours=5))


def now_utc5() -> datetime:
    """Current time in UTC+5 (Pakistan)."""
    return datetime.now(UTC5)


# Configuration
SCRAPE_INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "30"))

# MUFAP URLs
MUFAP_BASE_URL = "https://www.mufap.com.pk"
MUFAP_DAILY_NAV_URL = f"{MUFAP_BASE_URL}/Industry/IndustryStatDaily?tab=3"

# PSX URLs
PSX_BASE_URL = "https://dps.psx.com.pk"
PSX_MARKET_WATCH_URL = f"{PSX_BASE_URL}/market-watch"
PSX_HOME_URL = PSX_BASE_URL
