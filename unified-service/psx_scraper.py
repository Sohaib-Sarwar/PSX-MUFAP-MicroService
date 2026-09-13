"""
PSX Web Scraper Module
=======================
Scrapes stock market data from Pakistan Stock Exchange (dps.psx.com.pk).
Uses requests + BeautifulSoup only (NO Selenium / Chrome needed).

The PSX market-watch page renders all stock data server-side in HTML,
so a simple HTTP GET is sufficient to retrieve every row.

Data extracted:
  - Market Watch: Symbol, LDCP, Open, High, Low, Current, Change, Change%, Volume
  - Indices: KSE100, KSE30, KMI30, etc.
"""

import re
import logging
from datetime import datetime
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import pandas as pd

from config import PSX_HOME_URL, PSX_MARKET_WATCH_URL, now_utc5

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
#  HTTP helpers – reusable session with connection pooling + retries
# ──────────────────────────────────────────────────────────────────

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


def _fetch_page(url: str, timeout: int = 20) -> Optional[BeautifulSoup]:
    """Fetch a URL and return a BeautifulSoup object, or None on failure.

    IMPORTANT: Caller must call soup.decompose() and del soup after use
    to free the lxml C-allocated DOM tree.
    """
    try:
        resp = _session.get(url, timeout=timeout)
        resp.raise_for_status()
        html = resp.text
        resp.close()
        del resp  # free response body immediately
        soup = BeautifulSoup(html, "lxml")
        del html  # free raw HTML string
        return soup
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None


def _parse_number(text: str) -> Optional[float]:
    """Parse a string into a float, handling commas and percentage signs."""
    if not text:
        return None
    cleaned = text.replace(",", "").replace("%", "").replace("+", "").strip()
    if cleaned in ("", "-", "--", "N/A"):
        return None
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────────────────────────
#  Market Watch scraper  —  uses /market-watch for ALL stocks
# ──────────────────────────────────────────────────────────────────

def scrape_psx_market_watch() -> pd.DataFrame:
    """
    Scrape the **full** market watch table from dps.psx.com.pk/market-watch.

    The page renders all stock data in a server-side HTML table,
    so no browser / Selenium is needed.

    Returns DataFrame with columns:
        symbol, ldcp, open, high, low, current, change,
        change_pct, volume, date, scrape_timestamp
    """
    logger.info("Starting PSX Market Watch scrape (requests-only)...")

    soup = _fetch_page(PSX_MARKET_WATCH_URL)
    if soup is None:
        return pd.DataFrame()

    records = _parse_market_watch_table(soup)

    if not records:
        logger.warning("Header-based parsing found 0 records, trying positional fallback...")
        records = _parse_market_watch_positional(soup)

    # Extract date before freeing the tree
    market_date = _extract_market_date(soup)

    # Free the lxml tree (C-allocated memory, invisible to Python GC)
    soup.decompose()
    del soup

    scrape_time = now_utc5().isoformat()
    df = pd.DataFrame(records)
    del records  # free intermediate list

    if not df.empty:
        df["scrape_timestamp"] = scrape_time
        df["date"] = market_date or now_utc5().strftime("%Y-%m-%d")
        logger.info(f"Successfully scraped {len(df)} stock records")
    else:
        logger.warning("No market watch data scraped")

    return df


# ──────────────────────────────────────────────────────────────────
#  Parsing helpers
# ──────────────────────────────────────────────────────────────────

def _parse_market_watch_table(soup: BeautifulSoup) -> list[dict]:
    """Parse the market watch table using header-driven column mapping.

    Expected column order on /market-watch:
        SYMBOL | SECTOR | IDX | LDCP | OPEN | HIGH | LOW | CURRENT | CHANGE | CHANGE(%) | VOLUME
    """
    records: list[dict] = []

    for table in soup.find_all("table"):
        thead = table.find("thead")
        if not thead:
            continue

        headers = [th.get_text(strip=True).lower() for th in thead.find_all(["th", "td"])]

        # Must contain key columns
        if not any("symbol" in h for h in headers):
            continue
        if not any("current" in h or "ldcp" in h for h in headers):
            continue

        # Build column-index map
        col_map: dict[str, int] = {}
        for i, h in enumerate(headers):
            if "symbol" in h:
                col_map["symbol"] = i
            elif "ldcp" in h:
                col_map["ldcp"] = i
            elif "open" in h:
                col_map["open"] = i
            elif "high" in h:
                col_map["high"] = i
            elif "low" in h:
                col_map["low"] = i
            elif "current" in h:
                col_map["current"] = i
            elif "change" in h and "%" in h:
                col_map["change_pct"] = i
            elif "change" in h:
                col_map["change"] = i
            elif "volume" in h:
                col_map["volume"] = i

        logger.info(f"Column map: {col_map}  (headers: {headers})")

        tbody = table.find("tbody") or table
        for row in tbody.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 5:
                continue
            texts = [c.get_text(strip=True) for c in cells]

            sym_idx = col_map.get("symbol", 0)
            sym_cell = cells[sym_idx] if sym_idx < len(cells) else cells[0]
            a_tag = sym_cell.find("a")
            symbol = (a_tag.get_text(strip=True) if a_tag else sym_cell.get_text(strip=True)).strip()
            if not symbol or len(symbol) < 1:
                continue

            def _col(name: str) -> Optional[float]:
                idx = col_map.get(name)
                if idx is not None and idx < len(texts):
                    return _parse_number(texts[idx])
                return None

            record = {
                "symbol": symbol,
                "ldcp": _col("ldcp"),
                "open": _col("open"),
                "high": _col("high"),
                "low": _col("low"),
                "current": _col("current"),
                "change": _col("change"),
                "change_pct": _col("change_pct"),
                "volume": int(_col("volume") or 0),
            }

            if record["current"] is not None:
                records.append(record)

        if records:
            break

    return records


def _parse_market_watch_positional(soup: BeautifulSoup) -> list[dict]:
    """Fallback: iterate all <tr> elements and pick rows that look like stocks."""
    records: list[dict] = []

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 8:
            continue
        texts = [c.get_text(strip=True) for c in cells]

        sym = texts[0]
        a_tag = cells[0].find("a")
        if a_tag:
            sym = a_tag.get_text(strip=True)
        sym = sym.strip()
        if not sym or not re.match(r"^[A-Z0-9]", sym):
            continue
        if texts[1].isalpha():
            continue

        nums = [_parse_number(t) for t in texts[1:]]
        nums = [n for n in nums if n is not None]

        if len(nums) >= 8:
            records.append({
                "symbol": sym,
                "ldcp": nums[0],
                "open": nums[1],
                "high": nums[2],
                "low": nums[3],
                "current": nums[4],
                "change": nums[5],
                "change_pct": nums[6],
                "volume": int(nums[7]),
            })

    return records


def _extract_market_date(soup: BeautifulSoup) -> Optional[str]:
    """Extract the market date from the page."""
    text = soup.get_text()
    patterns = [
        r"(?:As of|Date:?)\s+(\w+ \d{1,2},?\s*\d{4})",
        r"(\w{3} \d{1,2},?\s*\d{4})",
        r"(\d{2}-\d{2}-\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            date_str = match.group(1)
            for fmt in ["%b %d, %Y", "%b %d %Y", "%d-%m-%Y"]:
                try:
                    return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
    return None


# ──────────────────────────────────────────────────────────────────
#  Index data scraper
# ──────────────────────────────────────────────────────────────────

def scrape_psx_indices() -> pd.DataFrame:
    """Scrape PSX index data (KSE100, KSE30, KMI30, etc.) from homepage."""
    logger.info("Starting PSX indices scrape...")

    soup = _fetch_page(PSX_HOME_URL)
    if soup is None:
        return pd.DataFrame()

    records = _parse_indices(soup)

    # Free the lxml tree
    soup.decompose()
    del soup

    scrape_time = now_utc5().isoformat()
    df = pd.DataFrame(records)
    del records

    if not df.empty:
        df["scrape_timestamp"] = scrape_time
        df["date"] = now_utc5().strftime("%Y-%m-%d")
        logger.info(f"Scraped {len(df)} index records")

    return df


def _parse_indices(soup: BeautifulSoup) -> list[dict]:
    """Parse index data from the PSX homepage."""
    records: list[dict] = []
    text = soup.get_text()

    index_names = [
        "KSE100", "KSE100PR", "ALLSHR", "KSE30", "KMI30",
        "BKTI", "OGTI", "KMIALLSHR", "PSXDIV20", "UPP9",
        "NITPGI", "NBPPGI", "MZNPI", "JSMFI", "ACI",
        "JSGBKTI", "HBLTTI", "MII30",
    ]

    for idx_name in index_names:
        pattern = rf"{idx_name}\s+([\d,]+\.?\d*)\s+([+-]?[\d,]+\.?\d*)\s+\(([+-]?[\d.]+%?)\)"
        match = re.search(pattern, text)
        if match:
            records.append({
                "index_name": idx_name,
                "value": _parse_number(match.group(1)),
                "change": _parse_number(match.group(2)),
                "change_pct": _parse_number(match.group(3)),
            })

    return records


def scrape_psx_performers() -> dict:
    """Scrape top active, advancers, and decliners from homepage tables.

    Kept for backward compatibility with existing imports/endpoints.
    """
    logger.info("Starting PSX performers scrape...")

    soup = _fetch_page(PSX_HOME_URL)
    if soup is None:
        return {"top_active": [], "top_advancers": [], "top_decliners": []}

    performers = {
        "top_active": [],
        "top_advancers": [],
        "top_decliners": [],
    }

    headings = soup.find_all(["h2", "h3", "h4", "h5"])
    for heading in headings:
        heading_text = heading.get_text(strip=True).lower()
        if "active" in heading_text:
            performers["top_active"] = _parse_performer_table(heading)
        elif "advancer" in heading_text:
            performers["top_advancers"] = _parse_performer_table(heading)
        elif "decliner" in heading_text:
            performers["top_decliners"] = _parse_performer_table(heading)

    # Free the lxml tree
    soup.decompose()
    del soup

    return performers


def _parse_performer_table(heading_element) -> list[dict]:
    """Parse performer table directly following a heading element."""
    records: list[dict] = []
    table = heading_element.find_next("table")
    if not table:
        return records

    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        texts = [c.get_text(strip=True) for c in cells]
        if len(texts) < 3:
            continue

        symbol = texts[0].strip()
        if not symbol or not re.match(r"^[A-Z]", symbol):
            continue

        price = _parse_number(texts[1]) if len(texts) > 1 else None
        change_text = texts[2] if len(texts) > 2 else ""
        volume = _parse_number(texts[-1]) if len(texts) > 3 else None

        change_match = re.search(r"([+-]?[\d,.]+)\s*\(([+-]?[\d.]+%?)\)", change_text)
        change = _parse_number(change_match.group(1)) if change_match else None
        change_pct = _parse_number(change_match.group(2)) if change_match else None

        if price is not None:
            records.append({
                "symbol": symbol,
                "price": price,
                "change": change,
                "change_pct": change_pct,
                "volume": int(volume) if volume is not None else None,
            })

    return records
