# Publication audit

Audit date: 26 November 2026

This is a sanitized record of the publication audit requested in the course submission guidelines. It contains no credentials, private configuration values, connection strings, or secret scan output.

## What was checked

The first pass was performed without modifying files or executing the scraper. It reviewed:

- Git status, tracked files, recent history, and repository organization
- current files and every reachable commit for common high-risk credential patterns
- history for sensitive filenames such as environment files, private keys, certificates, and credential files
- ignore rules for virtual environments, caches, generated data, reports, and local configuration
- dependency pins, placeholder configuration, password handling, URL validation, parsing, SQL, and parameterized upserts
- README coverage of purpose, collection, persistence, interaction, setup, learning, limitations, and assistance
- the demonstration screenshot for accidental personal or sensitive information

## First-pass findings

### Confirmed issue requiring action

1. `.gitignore` correctly excluded virtual environments, `.env`, Python bytecode, and generated `books.json`, but it did not cover common test caches, coverage reports, build output, or package metadata.

### Recommended improvements

- A production deployment should use a dedicated least-privilege database role instead of a project-owner pooler account. This is already disclosed in the README and is outside the scope of this learning project.

### Checks with no confirmed issue

- No credential, API-token, private-key, database-URL, or other high-risk secret pattern was found in the current tracked files or reachable file history.
- No sensitive configuration or key filename was found in reachable file history.
- Supabase values in the README are placeholders; no project reference, password, host, or connection string is committed.
- Database values are passed separately to Psycopg and upserts use parameters rather than string-built SQL.
- Row-level security is enabled and browser roles are denied because the scraper uses a trusted direct PostgreSQL connection.
- The connected Supabase project contains the expected `public.books` table and 10 stored book records.
- The README contains the required course documentation and a screenshot.
- The screenshot shows a dry run only and contains no shell username, local path, password, host, or connection string.

## Fix made

- Expanded `.gitignore` for test caches, type-checker caches, coverage output, build output, and package metadata.

## Repeat audit and evidence

After the fix:

- `git diff --check` reported no whitespace errors.
- The isolated Python environment installed the pinned requirements without dependency conflicts.
- All four unit tests passed.
- A live, non-writing dry run collected three books from Books to Scrape successfully.
- The connected database was checked separately and still contained 10 stored records; no credential was printed or added to the repository.
- The high-risk credential and sensitive-filename scans were repeated with no confirmed finding.
- Common virtual-environment, cache, coverage, build, generated-data, and local-configuration files are ignored.
- The public repository returned HTTP 200 in an anonymous request.

## Remaining limitations

- Unit tests mock the database. The existing stored rows confirm persistence, but the repeat audit deliberately did not perform another database write.
- HTML selectors can require maintenance if Books to Scrape changes its page structure.
- A secret scan reduces risk but cannot prove that a repository is safe.
