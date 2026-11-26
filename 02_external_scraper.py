#!/usr/bin/env python3
"""Scrape books.toscrape.com and upsert the results into Supabase Postgres."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import psycopg
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://books.toscrape.com/"
ALLOWED_HOST = "books.toscrape.com"
RATING_VALUES = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
DEFAULT_DB_PORT = 5432
DEFAULT_DB_NAME = "postgres"


@dataclass(frozen=True)
class Book:
    upc: str
    name: str
    price_pence: int
    currency: str
    stock_count: int | None
    rating: int | None
    category: str | None
    scraped_at: datetime

    def json_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["scraped_at"] = self.scraped_at.isoformat()
        return record


class WebClient:
    """HTTP client with retries and a polite minimum request delay."""

    def __init__(self, delay: float, timeout: float) -> None:
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.75,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session = requests.Session()
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "User-Agent": (
                    "books-to-scrape-learning-project/2.0 "
                    "(manual educational scraper)"
                )
            }
        )
        self.delay = delay
        self.timeout = timeout
        self._last_request_at: float | None = None

    def get_html(self, url: str) -> str:
        validate_target_url(url)
        if self._last_request_at is not None:
            remaining = self.delay - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)

        try:
            response = self.session.get(url, timeout=self.timeout)
            self._last_request_at = time.monotonic()
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not download {url}: {exc}") from exc

        response.encoding = response.apparent_encoding or response.encoding
        return response.text

    def close(self) -> None:
        self.session.close()


def validate_target_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise ValueError(f"Unexpected scraping URL: {url}")


def clean_text(value: str) -> str:
    return " ".join(value.split())


def money_to_pence(value: str) -> int:
    cleaned = re.sub(r"[^0-9.-]", "", value)
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Unrecognised price: {value!r}") from exc
    return int(amount * 100)


def stock_number(value: str) -> int | None:
    match = re.search(r"\((\d+)\s+available\)", value, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_listing(html: str, page_url: str) -> tuple[list[str], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    product_urls = [
        urljoin(page_url, link["href"])
        for link in soup.select("article.product_pod h3 a[href]")
    ]
    product_urls = list(dict.fromkeys(product_urls))

    next_link = soup.select_one("li.next a[href]")
    next_url = urljoin(page_url, next_link["href"]) if next_link else None
    return product_urls, next_url


def parse_book(html: str, product_url: str) -> Book:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.select_one(".product_main h1")
    if title_node is None:
        raise ValueError(f"No book title found on {product_url}")

    product_info: dict[str, str] = {}
    for row in soup.select("table.table-striped tr"):
        key = row.find("th")
        value = row.find("td")
        if key and value:
            product_info[clean_text(key.get_text(" ", strip=True))] = clean_text(
                value.get_text(" ", strip=True)
            )

    required = {
        "UPC",
        "Price (incl. tax)",
        "Availability",
    }
    missing = required.difference(product_info)
    if missing:
        raise ValueError(
            f"Missing product fields on {product_url}: {', '.join(sorted(missing))}"
        )

    breadcrumb = soup.select("ul.breadcrumb li")
    category = None
    if len(breadcrumb) >= 3:
        category_link = breadcrumb[2].find("a")
        if category_link:
            category = clean_text(category_link.get_text(" ", strip=True))

    rating_node = soup.select_one("p.star-rating")
    star_rating = None
    if rating_node:
        star_rating = next(
            (
                RATING_VALUES[name]
                for name in rating_node.get("class", [])
                if name in RATING_VALUES
            ),
            None,
        )

    availability = product_info["Availability"]

    return Book(
        upc=product_info["UPC"],
        name=clean_text(title_node.get_text(" ", strip=True)),
        price_pence=money_to_pence(product_info["Price (incl. tax)"]),
        currency="GBP",
        stock_count=stock_number(availability),
        rating=star_rating,
        category=category,
        scraped_at=datetime.now(timezone.utc),
    )


def first_listing_url(start_page: int) -> str:
    if start_page == 1:
        return BASE_URL
    return f"{BASE_URL}catalogue/page-{start_page}.html"


def collect_product_urls(
    client: WebClient, limit: int, start_page: int
) -> list[str]:
    product_urls: list[str] = []
    listing_url: str | None = first_listing_url(start_page)

    while listing_url and len(product_urls) < limit:
        print(f"Listing: {listing_url}")
        page_urls, listing_url = parse_listing(
            client.get_html(listing_url), listing_url
        )
        for product_url in page_urls:
            if product_url not in product_urls:
                product_urls.append(product_url)
            if len(product_urls) >= limit:
                break

    if not product_urls:
        raise RuntimeError("The listing contained no product links.")
    return product_urls


def scrape_books(
    limit: int, start_page: int, delay: float, timeout: float
) -> list[Book]:
    client = WebClient(delay=delay, timeout=timeout)
    try:
        product_urls = collect_product_urls(client, limit, start_page)
        books: list[Book] = []
        for number, product_url in enumerate(product_urls, start=1):
            print(f"Book {number}/{len(product_urls)}: {product_url}")
            book = parse_book(client.get_html(product_url), product_url)
            books.append(book)
            print(
                f"  {book.name} | GBP "
                f"{book.price_pence / 100:.2f} | stock {book.stock_count}"
            )
        return books
    finally:
        client.close()


UPSERT_SQL = """
    insert into public.books (
        upc, name, price_pence, currency, stock_count, rating, category,
        first_scraped_at, last_scraped_at
    ) values (
        %(upc)s, %(name)s, %(price_pence)s, %(currency)s, %(stock_count)s,
        %(rating)s, %(category)s, %(scraped_at)s, %(scraped_at)s
    )
    on conflict (upc) do update set
        name = excluded.name,
        price_pence = excluded.price_pence,
        currency = excluded.currency,
        stock_count = excluded.stock_count,
        rating = excluded.rating,
        category = excluded.category,
        last_scraped_at = excluded.last_scraped_at
"""


def save_to_supabase(
    connection_settings: dict[str, Any], books: list[Book]
) -> int:
    rows = [asdict(book) for book in books]
    try:
        with psycopg.connect(
            **connection_settings,
            connect_timeout=20,
            sslmode="require",
            application_name="books_to_scrape_manual",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("select to_regclass('public.books')")
                table_name = cursor.fetchone()
                if not table_name or table_name[0] is None:
                    raise RuntimeError(
                        "public.books does not exist. "
                        "Run 01_setup_supabase.sql first."
                    )
                cursor.executemany(UPSERT_SQL, rows)
        return len(rows)
    except psycopg.Error as exc:
        raise RuntimeError(f"Supabase database operation failed: {exc}") from exc


def save_json(path: Path, books: list[Book]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([book.json_record() for book in books], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def database_connection_from_environment() -> dict[str, Any]:
    database_url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if database_url:
        return {"conninfo": database_url}

    password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if sys.stdin.isatty():
        password = password or getpass.getpass(
            "Paste your Supabase database password (input hidden): "
        )
    if not password:
        raise ValueError(
            "No database password supplied. Set SUPABASE_DB_PASSWORD "
            "or use --dry-run."
        )

    host = os.environ.get("SUPABASE_DB_HOST", "").strip()
    user = os.environ.get("SUPABASE_DB_USER", "").strip()
    missing = [
        name
        for name, value in (
            ("SUPABASE_DB_HOST", host),
            ("SUPABASE_DB_USER", user),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Missing required database configuration: "
            + ", ".join(missing)
            + ". Copy the pooler values from Supabase Connect."
        )

    port_value = os.environ.get("SUPABASE_DB_PORT", str(DEFAULT_DB_PORT)).strip()
    try:
        port = int(port_value)
    except ValueError as exc:
        raise ValueError("SUPABASE_DB_PORT must be a whole number.") from exc

    return {
        "host": host,
        "port": port,
        "dbname": os.environ.get("SUPABASE_DB_NAME", DEFAULT_DB_NAME).strip(),
        "user": user,
        "password": password,
    }


def integer_between(minimum: int, maximum: int):
    def parse(value: str) -> int:
        number = int(value)
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}"
            )
        return number

    return parse


def nonnegative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape books.toscrape.com and upsert books into Supabase Postgres."
        )
    )
    parser.add_argument(
        "--limit",
        type=integer_between(1, 1000),
        default=5,
        help="maximum books to collect (default: 5)",
    )
    parser.add_argument(
        "--start-page",
        type=integer_between(1, 50),
        default=1,
        help="listing page at which to start (default: 1)",
    )
    parser.add_argument(
        "--delay",
        type=nonnegative_float,
        default=0.5,
        help="minimum seconds between requests (default: 0.5)",
    )
    parser.add_argument(
        "--timeout",
        type=positive_float,
        default=20.0,
        help="HTTP timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scrape and display results without connecting to Supabase",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="optionally save the scraped records to a JSON file",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        books = scrape_books(
            limit=args.limit,
            start_page=args.start_page,
            delay=args.delay,
            timeout=args.timeout,
        )
        if args.json_output:
            save_json(args.json_output, books)
            print(f"Saved JSON preview to {args.json_output.resolve()}")

        if args.dry_run:
            print(f"\nDry run complete: scraped {len(books)} books; database unchanged.")
            return 0

        saved = save_to_supabase(database_connection_from_environment(), books)
        print(f"\nCompleted: {saved} books upserted into public.books.")
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
