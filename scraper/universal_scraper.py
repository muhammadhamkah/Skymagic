"""Reusable scraper base. Handles retries, rate limits, UA rotation, output."""

import csv
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


@dataclass
class ScraperConfig:
    delay_seconds: float = 1.0
    delay_jitter: float = 0.5
    max_retries: int = 3
    backoff_base: float = 2.0
    timeout_seconds: int = 30
    headers: dict = field(default_factory=dict)


class Scraper:
    def __init__(self, config: Optional[ScraperConfig] = None):
        self.config = config or ScraperConfig()
        self.session = requests.Session()

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            **self.config.headers,
        }

    def fetch(self, url: str, params: Optional[dict] = None) -> requests.Response:
        last_err = None
        for attempt in range(self.config.max_retries):
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=self.config.timeout_seconds,
                )
                if resp.status_code == 429:
                    wait = self.config.backoff_base ** (attempt + 2)
                    logger.warning(f"429 rate limited, sleeping {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                last_err = e
                wait = self.config.backoff_base ** attempt
                logger.warning(f"fetch failed ({e}); retry in {wait}s")
                time.sleep(wait)
        raise RuntimeError(f"failed to fetch {url}: {last_err}")

    def fetch_html(self, url: str, params: Optional[dict] = None) -> BeautifulSoup:
        resp = self.fetch(url, params=params)
        return BeautifulSoup(resp.text, "lxml")

    def fetch_json(self, url: str, params: Optional[dict] = None) -> dict:
        return self.fetch(url, params=params).json()

    def polite_sleep(self):
        jitter = random.uniform(0, self.config.delay_jitter)
        time.sleep(self.config.delay_seconds + jitter)

    def paginate(
        self,
        start_url: str,
        next_link_selector: str,
        max_pages: int = 100,
    ) -> Iterable[BeautifulSoup]:
        url = start_url
        for page in range(max_pages):
            soup = self.fetch_html(url)
            yield soup
            self.polite_sleep()
            next_el = soup.select_one(next_link_selector)
            if not next_el or not next_el.get("href"):
                return
            url = urljoin(url, next_el["href"])


def write_csv(rows: list[dict], path: str):
    if not rows:
        logger.warning(f"no rows to write to {path}")
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({k for r in rows for k in r.keys()})
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"wrote {len(rows)} rows -> {path}")


def write_json(rows: list[dict], path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    logger.info(f"wrote {len(rows)} rows -> {path}")
