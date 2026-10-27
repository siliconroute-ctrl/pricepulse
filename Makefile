# Shortcuts. On Windows without make, run the commands shown after each target.
.PHONY: install test smoke lint sample api scrape

install:  ## pip install -r backend/requirements-dev.txt
	cd backend && pip install -r requirements-dev.txt

test:     ## cd backend && python -m pytest
	cd backend && python -m pytest

smoke:    ## python frontend/tests/run_smoke.py   (needs Node and Playwright)
	python frontend/tests/run_smoke.py

lint:     ## cd backend && ruff check . && black --check .
	cd backend && ruff check . && black --check .

sample:   ## cd backend && python make_sample.py
	cd backend && python make_sample.py

api:      ## cd backend && uvicorn api:app --reload
	cd backend && uvicorn api:app --reload

scrape:   ## cd backend && python run_scrapers.py --region paarl --debug
	cd backend && python run_scrapers.py --region paarl --debug
