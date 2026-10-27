# Test fixtures

**These are SYNTHETIC pages**, written to match our best guess of each store's
markup. They test the parsing code, not whether the guesses are right: the build
environment could not reach the retailer websites.

To replace them with real pages once the sites are reachable:

    python run_scrapers.py --region paarl --store Checkers --limit 2 --save-fixtures

The raw pages land in `tests/fixtures/live/{store}/`. Copy one over the matching
synthetic file here, update the expected names and prices in
`tests/test_scraper_parsing.py`, and run `python -m pytest`.
