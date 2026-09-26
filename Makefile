# Local development on a Mac: the Python environment, site data, the local server and the iOS app.
#   make venv site-synthetic serve   # offline preview data on http://localhost:8000, then open Xcode
#   make site serve                  # the full build from the collected history, like the pipeline
PY ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PYTHON := $(VENV)/bin/python
SITE ?= _site
PORT ?= 8000

.PHONY: help local venv gtfs site-synthetic site data-branch serve test ios ios-build ios-test ios-fixtures

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{ printf "  %-16s %s\n", $$1, $$2 }'

local: ## Everything for a Mac in one go: env, offline data, Xcode config, local server, open Xcode (--real via the script)
	scripts/local_setup.sh

venv: ## Python environment with the package and the dev tools
	test -x $(PYTHON) || $(PY) -m venv $(VENV)
	$(PIP) install -q -U pip && $(PIP) install -q -e ".[dev]"

gtfs: data/gtfs_subway.zip ## The MTA static schedule (downloaded once into data/)
data/gtfs_subway.zip:
	mkdir -p data && curl -sSL --retry 3 -o $@ https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip

site-synthetic: ## Offline preview site with recorded feeds and a synthetic history (no network needed)
	$(PYTHON) -m pipeline.build_site --synthetic --out $(SITE)

data-branch: ## The collected history (the repository's data branch) as a worktree in ./data-branch
	test -d data-branch || (git fetch origin data && git worktree add data-branch origin/data)

site: gtfs data-branch ## Full site build from the collected history: engine tables, models, geometry (takes minutes)
	$(PYTHON) -m pipeline.build_site --data-dir data-branch --out $(SITE)

serve: gtfs ## Serve $(SITE) on http://localhost:$(PORT); live.json and the timetable extract refresh from the feeds
	$(VENV)/bin/mta-insights serve --site $(SITE) --port $(PORT) --db data/mta.sqlite

test: ## Python tests (the JS harness and the engine cross-checks included)
	$(PYTHON) -m pytest -q

ios: ## Open the app in Xcode
	open ios/WhichWay/WhichWay.xcodeproj

ios-build: ## Compile the app for the Simulator from the command line (what CI does)
	cd ios/WhichWay && xcodebuild -project WhichWay.xcodeproj -scheme WhichWay -configuration Debug -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build 2>&1 | grep -E "error:|BUILD" 

ios-test: ## The core package tests: predictor against the Python fixture, presets, nearest stations
	cd ios/WhichWayCore && swift test

ios-fixtures: ## Regenerate the Swift predictor fixture from the Python reference
	$(PYTHON) ios/WhichWayCore/Tests/make_fixtures.py
