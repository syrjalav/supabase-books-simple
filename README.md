# Two-script Supabase book scraper

This version is deliberately simple:

1. Run `01_setup_supabase.sql` once in Supabase SQL Editor.
2. Run `02_external_scraper.py` manually from a terminal, IDE, scheduled job, or
   another trusted program.

The website is scraped from HTML. It does not use a Books to Scrape API or the
Supabase Data API. The Python program connects directly to PostgreSQL.

## 1. Create the Supabase table

Open your Supabase project, choose **SQL Editor**, paste the complete contents of
`01_setup_supabase.sql`, and click **Run**.

Successful setup returns a row similar to:

```text
status | existing_book_rows
ready  | 0
```

The SQL is idempotent, so running it again does not delete existing books. It
enables row-level security and removes browser access for the `anon` and
`authenticated` roles. The external program uses a trusted PostgreSQL connection.

The table is intentionally compact: `id`, `upc`, `name`, `price_pence`,
`currency`, `stock_count`, `rating`, `category`, and two scrape timestamps. It
does not store product URLs, descriptions, images, review counts, or tax details.

## 2. Install Python dependencies

Python 3.11 or newer is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate with:

```powershell
.\.venv\Scripts\Activate.ps1
```

## 3. Test scraping without touching Supabase

```bash
python 02_external_scraper.py --limit 5 --dry-run
```

This downloads and parses five books, displays them, and makes no database
connection. You can also save the test result:

```bash
python 02_external_scraper.py --limit 5 --dry-run --json-output books.json
```

## 4. Connect the external program to Supabase

This copy is already configured with this project's verified **Session pooler**
host, port, database name, and username. You only need the Supabase database
password. The password is passed to PostgreSQL separately, so characters such as
`@`, `#`, `/`, and `:` do not need URL encoding.

Run:

```bash
python 02_external_scraper.py --limit 5
```

The script asks:

```text
Paste your Supabase database password (input hidden):
```

Paste the password and press Enter. Nothing appears while you paste because the
input is hidden. The script does not save the password.

For an automated run, provide `SUPABASE_DB_PASSWORD` through the IDE or hosting
service's secret/environment settings. Do not put it in source code or Git.
`SUPABASE_DB_HOST`, `SUPABASE_DB_PORT`, `SUPABASE_DB_NAME`, and
`SUPABASE_DB_USER` can override the included project settings. A correctly
URL-encoded `SUPABASE_DB_URL` remains supported for advanced use.

To scrape more books later:

```bash
python 02_external_scraper.py --limit 50
```

For batches, select a starting listing page:

```bash
python 02_external_scraper.py --limit 20 --start-page 6
```

Repeated runs upsert by UPC. Existing books are updated instead of duplicated,
while `first_scraped_at` remains unchanged and `last_scraped_at` is refreshed.

## 5. Verify in Supabase

Run this in SQL Editor:

```sql
select
    id,
    name,
    price_pence / 100.0 as price_gbp,
    stock_count,
    rating,
    category,
    last_scraped_at
from public.books
order by last_scraped_at desc, name;
```

## Useful commands

```bash
python 02_external_scraper.py --help
python -m unittest discover -s tests -v
```

The scraper restricts requests to `books.toscrape.com`, waits between requests,
retries temporary failures, uses parameterized SQL, and performs each database
batch in a transaction.
