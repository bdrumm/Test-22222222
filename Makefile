# Local development on a Mac: the Python environment, site data, the local server and the iOS app.
#   make venv site-synthetic serve   # offline preview data on http://localhost:8000, then open Xcode
#   make site serve                  # the full build from the collected history, like the pipeline
PY ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PYTHON := $(VENV)/bin/python
SITE ?= _site
PORT ?= 8000

.PHONY: help local venv gtfs site-synthetic site data-branch serve serve-agent model test ios ios-build ios-test ios-ipa ios-testflight ios-organizer ios-fixtures

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

model: gtfs data-branch ## Train the arrival model on the archive + collected history with weather, alerts and day patterns (writes data/arrival.joblib)
	$(PYTHON) -m pipeline.train_model --data-dir data-branch --db data/mta.sqlite --out data --skip-ablation --max-iter 1500 --max-leaf-nodes 255 --min-samples-leaf 200

trips: ## Pull the phone's trips (when it is connected), merge them with the uploads, review them against the trains → data/trips/trip_review.md (IMPORT=file.json takes an export from the app)
	$(PYTHON) -m pipeline.trip_review --pull $(if $(IMPORT),--import "$(IMPORT)") --db data/mta.sqlite --out data/trips

trips-nightly: ## Install a launchd agent that runs `make trips` every night at 23:40 (remove with scripts/trips_agent.sh remove)
	scripts/trips_agent.sh install

serve: gtfs ## Serve $(SITE) on http://localhost:$(PORT); live.json and the timetable extract refresh from the feeds
	$(VENV)/bin/mta-insights serve --site $(SITE) --port $(PORT) --db data/mta.sqlite

serve-agent: gtfs ## Keep the local server running as a launchd agent: starts at login, restarts if it stops (scripts/serve_agent.sh remove|status)
	PORT=$(PORT) scripts/serve_agent.sh install

test: ## Python tests (the JS harness and the engine cross-checks included)
	$(PYTHON) -m pytest -q

ios: ## Open the app in Xcode
	open ios/WhichWay/WhichWay.xcodeproj

ios-build: ## Compile the app for the Simulator from the command line (what CI does)
	cd ios/WhichWay && xcodebuild -project WhichWay.xcodeproj -scheme WhichWay -configuration Debug -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build 2>&1 | grep -E "error:|BUILD" 

ios-test: ## The core package tests: predictor against the Python fixture, presets, nearest stations
	cd ios/WhichWayCore && swift test

ios-ipa: ## Archive the app (Release) and export build/WhichWay.ipa, signed with your team
	scripts/testflight.sh

ios-testflight: ## Archive the app and upload it to TestFlight (needs an App Store Connect API key, see scripts/testflight.sh)
	scripts/testflight.sh --upload

ios-organizer: ## Archive the app (Release) and hand it to Xcode's Organizer, to distribute with the signed-in Apple ID
	scripts/testflight.sh --organizer

ios-fixtures: ## Regenerate the Swift predictor fixture from the Python reference
	$(PYTHON) ios/WhichWayCore/Tests/make_fixtures.py
