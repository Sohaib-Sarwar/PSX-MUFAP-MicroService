"""
MUFAP Web Scraper Module
=========================
Scrapes mutual fund NAV data from the MUFAP website.
Uses requests + BeautifulSoup (no Selenium / browser needed).

Target page: NAV / Daily Prices Announcement
URL:  https://www.mufap.com.pk/Industry/IndustryStatDaily?tab=3

The page renders the full data table server-side, so a simple HTTP
GET is sufficient — no JavaScript execution required.

Table columns on the page:
  Sector | Fund | Category | Inception Date | Offer | Repurchase
  | NAV | Validity Date | Front-end | Back-end | Contingent
  | Market | Trustee
"""

import logging
from datetime import datetime
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import pandas as pd

from config import MUFAP_DAILY_NAV_URL, now_utc5

logger = logging.getLogger(__name__)

# Reusable session with connection pooling + automatic retries
_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})
_retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
_session.mount("https://", HTTPAdapter(max_retries=_retry, pool_maxsize=4))
_session.mount("http://", HTTPAdapter(max_retries=_retry, pool_maxsize=4))


# ──────────────────────────────────────────────────────────────────
#  Main entry point
# ──────────────────────────────────────────────────────────────────

def scrape_mufap_nav_data(url: Optional[str] = None) -> pd.DataFrame:
    """
    Scrape **all** mutual fund NAV data from MUFAP (no filters).

    Returns a DataFrame with columns:
        fund_name, fund_category, inception_date,
        offer_price, repurchase_price, nav,
        date_updated (validity date), trustee,
        scrape_timestamp
    """
    target_url = url or MUFAP_DAILY_NAV_URL
    logger.info(f"Starting MUFAP scrape from: {target_url}")

    try:
        response = _session.get(target_url, timeout=30)
        response.raise_for_status()
        html = response.text
        logger.info(f"Fetched page OK – {len(html):,} chars")
        response.close()
        del response  # free response body immediately

        soup = BeautifulSoup(html, "lxml")
        del html  # free raw HTML

        # Try the structured header-based parser first
        records = _parse_nav_table_with_headers(soup)

        if not records:
            logger.warning("Header-based parsing found 0 records; trying positional parser...")
            records = _parse_nav_table_positional(soup)

        # Free the lxml tree (C-allocated memory, invisible to Python GC)
        soup.decompose()
        del soup

        scrape_time = now_utc5().isoformat()
        df = pd.DataFrame(records)
        del records  # free the intermediate list

        if not df.empty:
            df["scrape_timestamp"] = scrape_time
            # Clean up NAV column
            df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
            df = df.dropna(subset=["nav"])
            df = df[df["nav"] > 0]
            logger.info(f"Successfully scraped {len(df)} fund records")
        else:
            logger.warning("No data was scraped from MUFAP website")

        return df

    except Exception as e:
        logger.error(f"Error scraping MUFAP data: {e}", exc_info=True)
        return pd.DataFrame()


# ──────────────────────────────────────────────────────────────────
#  Parser 1: header-driven (most reliable)
# ──────────────────────────────────────────────────────────────────

def _parse_nav_table_with_headers(soup: BeautifulSoup) -> list[dict]:
    """Parse the NAV table by mapping column headers to indices."""
    records = []

    for table in soup.find_all("table"):
        thead = table.find("thead")
        if not thead:
            continue

        raw_headers = [th.get_text(strip=True).lower()
                       for th in thead.find_all(["th", "td"])]

        # Must have at least "fund" and "nav" columns
        if not any("fund" in h for h in raw_headers):
            continue
        if not any("nav" in h for h in raw_headers):
            continue

        # Build column map
        col = {}
        for i, h in enumerate(raw_headers):
            if "fund" in h and "category" not in h and "fund" not in col:
                col["fund"] = i
            elif "category" in h:
                col["category"] = i
            elif "inception" in h:
                col["inception"] = i
            elif "offer" in h:
                col["offer"] = i
            elif "repurchase" in h or "redemption" in h:
                col["repurchase"] = i
            elif "nav" in h and "nav" not in col:
                col["nav"] = i
            elif "validity" in h or ("date" in h and "inception" not in h):
                col["validity"] = i
            elif "trustee" in h:
                col["trustee"] = i

        logger.info(f"MUFAP column map: {col} from headers: {raw_headers}")

        tbody = table.find("tbody") or table
        for row in tbody.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue
            texts = [c.get_text(strip=True) for c in cells]

            def _g(name, _col=col, _texts=texts):
                idx = _col.get(name)
                if idx is not None and idx < len(_texts):
                    return _texts[idx]
                return None

            fund_name = _g("fund")
            # Fund may also be stored inside <a> tag
            if col.get("fund") is not None:
                fidx = col["fund"]
                if fidx < len(cells):
                    a = cells[fidx].find("a")
                    if a:
                        fund_name = a.get_text(strip=True)

            nav_str = _g("nav")
            if not fund_name or not nav_str:
                continue

            nav_val = _try_float(nav_str)
            if nav_val is None or nav_val <= 0:
                continue

            record = {
                "fund_name": fund_name,
                "fund_category": _g("category") or "Unknown",
                "inception_date": _g("inception") or "",
                "offer_price": _try_float(_g("offer")),
                "repurchase_price": _try_float(_g("repurchase")),
                "nav": nav_val,
                "date_updated": _normalise_date(_g("validity")) or now_utc5().strftime("%Y-%m-%d"),
                "trustee": _g("trustee") or "",
            }
            records.append(record)

        if records:
            break  # found the table

    return records


# ──────────────────────────────────────────────────────────────────
#  Parser 2: positional (fallback)
# ──────────────────────────────────────────────────────────────────

def _parse_nav_table_positional(soup: BeautifulSoup) -> list[dict]:
    """Try every table; guess columns by position if headers don't match."""
    records = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue

        for row in rows:
            cells = row.find_all(["td"])
            if len(cells) < 4:
                continue
            texts = [c.get_text(strip=True) for c in cells]

            # Heuristic: first cell is fund name (long text), next has
            # category, then dates/numbers.
            fund_name = texts[0]
            a_tag = cells[0].find("a")
            if a_tag:
                fund_name = a_tag.get_text(strip=True)

            if not fund_name or len(fund_name) < 3:
                continue
            # skip if first cell is purely numeric
            if _try_float(fund_name.replace(",", "")) is not None:
                continue

            # Collect all parseable numbers from remaining cells
            nums = []
            date_found = None
            category = None
            for i, t in enumerate(texts[1:], start=1):
                val = _try_float(t)
                if val is not None:
                    nums.append(val)
                elif not date_found:
                    d = _normalise_date(t)
                    if d:
                        date_found = d
                    elif not category and len(t) > 3 and not t[0].isdigit():
                        category = t

            if len(nums) < 1:
                continue

            # Take the last number >= 0.01 as NAV (common pattern)
            nav_val = None
            for n in reversed(nums):
                if 0 < n < 100_000_000:
                    nav_val = n
                    break
            if nav_val is None:
                continue

            records.append({
                "fund_name": fund_name,
                "fund_category": category or "Unknown",
                "inception_date": "",
                "offer_price": nums[0] if len(nums) > 1 else None,
                "repurchase_price": nums[1] if len(nums) > 2 else None,
                "nav": nav_val,
                "date_updated": date_found or now_utc5().strftime("%Y-%m-%d"),
                "trustee": "",
            })

    return records


# ──────────────────────────────────────────────────────────────────
#  Tiny helpers
# ──────────────────────────────────────────────────────────────────

def _try_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    cleaned = text.replace(",", "").replace("%", "").strip()
    if cleaned in ("", "-", "--", "N/A"):
        return None
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _normalise_date(text: Optional[str]) -> Optional[str]:
    """Try to parse various date formats into YYYY-MM-DD."""
    if not text:
        return None
    fmts = [
        "%b %d, %Y", "%b %d %Y", "%d-%b-%Y", "%d-%m-%Y",
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%y",
        "%d %b %Y", "%d %B %Y",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
