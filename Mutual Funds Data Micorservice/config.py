"""
MUFAP Mutual Funds Data Microservice - Configuration
=====================================================
Scrapes mutual fund data from www.mufap.com.pk including:
- Fund Category
- Fund Name
- Current NAV
- Date of Update

Saves data to Excel files for export.
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

# MUFAP URLs
MUFAP_BASE_URL = "https://www.mufap.com.pk"
# tab=3 is the NAV / Daily Prices Announcement page (all funds, no filter)
MUFAP_DAILY_NAV_URL = f"{MUFAP_BASE_URL}/Industry/IndustryStatDaily?tab=3"
