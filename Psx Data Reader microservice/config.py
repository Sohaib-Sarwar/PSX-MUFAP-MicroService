"""
PSX Data Reader Microservice - Configuration
==============================================
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
EXCEL_OUTPUT_DIR = os.getenv("EXCEL_OUTPUT_DIR", "./output")
SCRAPE_INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "30"))

# PSX URLs
PSX_BASE_URL = "https://dps.psx.com.pk"
PSX_MARKET_WATCH_URL = f"{PSX_BASE_URL}/market-watch"
PSX_HOME_URL = PSX_BASE_URL
