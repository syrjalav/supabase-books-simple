import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "02_external_scraper.py"
SPEC = importlib.util.spec_from_file_location("external_scraper", SCRIPT_PATH)
assert SPEC and SPEC.loader
scraper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scraper
SPEC.loader.exec_module(scraper)


LISTING_HTML = """
<article class="product_pod"><h3><a href="catalogue/one_1/index.html">One</a></h3></article>
<li class="next"><a href="catalogue/page-2.html">next</a></li>
"""

BOOK_HTML = """
<ul class="breadcrumb">
  <li><a>Home</a></li><li><a>Books</a></li><li><a>Poetry</a></li>
</ul>
<div class="item active"><img src="../../media/one.jpg"></div>
<div class="product_main"><h1>Example Book</h1><p class="star-rating Four"></p></div>
<div id="product_description"></div><p>A description.</p>
<table class="table table-striped">
  <tr><th>UPC</th><td>abc123</td></tr>
  <tr><th>Product Type</th><td>Books</td></tr>
  <tr><th>Price (excl. tax)</th><td>£12.34</td></tr>
  <tr><th>Price (incl. tax)</th><td>£12.34</td></tr>
  <tr><th>Tax</th><td>£0.00</td></tr>
  <tr><th>Availability</th><td>In stock (7 available)</td></tr>
  <tr><th>Number of reviews</th><td>3</td></tr>
</table>
"""


class FakeCursor:
    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query):
        self.query = query

    def fetchone(self):
        return ("public.books",)

    def executemany(self, query, rows):
        self.upsert_query = query
        self.rows = list(rows)


class FakeConnection:
    def __init__(self):
        self.fake_cursor = FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.fake_cursor


class ScraperTests(unittest.TestCase):
    def test_listing_and_book_parsers(self):
        urls, next_url = scraper.parse_listing(
            LISTING_HTML, "https://books.toscrape.com/"
        )
        self.assertEqual(
            urls,
            ["https://books.toscrape.com/catalogue/one_1/index.html"],
        )
        self.assertEqual(
            next_url, "https://books.toscrape.com/catalogue/page-2.html"
        )

        book = scraper.parse_book(
            BOOK_HTML,
            "https://books.toscrape.com/catalogue/one_1/index.html",
        )
        self.assertEqual(book.name, "Example Book")
        self.assertEqual(book.price_pence, 1234)
        self.assertEqual(book.stock_count, 7)
        self.assertEqual(book.rating, 4)

    def test_parameterized_upsert_uses_expected_row(self):
        book = scraper.Book(
            upc="abc123",
            name="Example Book",
            price_pence=1234,
            currency="GBP",
            stock_count=7,
            rating=4,
            category="Poetry",
            scraped_at=datetime.now(timezone.utc),
        )
        connection = FakeConnection()
        settings = {
            "host": "pooler.example.com",
            "port": 5432,
            "dbname": "postgres",
            "user": "postgres.example",
            "password": "hidden",
        }
        with patch.object(scraper.psycopg, "connect", return_value=connection):
            count = scraper.save_to_supabase(settings, [book])

        self.assertEqual(count, 1)
        self.assertEqual(connection.fake_cursor.rows[0]["upc"], "abc123")
        self.assertIn("on conflict (upc) do update", connection.fake_cursor.upsert_query)

    def test_database_password_and_settings_are_passed_separately_from_uri(self):
        environment = {
            "SUPABASE_DB_PASSWORD": "p@ssword/with:symbols",
            "SUPABASE_DB_HOST": "aws-0-eu-west-1.pooler.supabase.com",
            "SUPABASE_DB_USER": "postgres.example",
        }
        with patch.dict(scraper.os.environ, environment, clear=True):
            settings = scraper.database_connection_from_environment()

        self.assertEqual(
            settings["host"], "aws-0-eu-west-1.pooler.supabase.com"
        )
        self.assertEqual(settings["port"], scraper.DEFAULT_DB_PORT)
        self.assertEqual(settings["user"], "postgres.example")
        self.assertEqual(settings["password"], "p@ssword/with:symbols")

    def test_database_host_and_user_are_required(self):
        environment = {
            "SUPABASE_DB_PASSWORD": "hidden",
        }
        with patch.dict(scraper.os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                ValueError, "SUPABASE_DB_HOST, SUPABASE_DB_USER"
            ):
                scraper.database_connection_from_environment()


if __name__ == "__main__":
    unittest.main()
