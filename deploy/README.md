# Running your own instance

GitHub Pages is a review surface: it refreshes only while the hourly Actions
run is collecting. For continuous 30-second updates, a growing local history
and a JSON API, run the server yourself.

## Docker

```bash
docker compose up -d --build          # http://localhost:8000  (app + /api/*)
docker compose logs -f insights
```

A prebuilt image is published by the `image` workflow on every change:
`ghcr.io/bdrumm/mta-delay-insights:latest` (use it in `docker-compose.yml`
with `image:` instead of `build:`).

The container downloads the static GTFS on first start (and refreshes it
daily), polls all realtime feeds every 30 s into `/data/mta.sqlite`, refits
the propagation, journey and learned arrival models every 30 minutes from its
own store, and serves the site from `/app/site` with `data/live.json`
regenerated after every poll.

One-off jobs reuse the image:

```bash
docker compose run --rm insights backfill --days 30      # subwaydata.nyc history into /data/branch
docker compose run --rm insights context                 # Open Data, weather, alerts archive, events
docker compose run --rm insights build                   # static site build into /data/site
```

## systemd (bare VM)

```bash
sudo useradd -r -s /usr/sbin/nologin mta
sudo mkdir -p /opt/mta-insights /var/lib/mta-insights && sudo chown mta /var/lib/mta-insights
sudo git clone <repo> /opt/mta-insights && cd /opt/mta-insights
sudo python3 -m venv .venv && sudo .venv/bin/pip install .
sudo cp deploy/mta-insights.service /etc/systemd/system/ && sudo systemctl enable --now mta-insights
```

## JSON API (served by `mta-insights serve` and the container)

| endpoint | returns |
|---|---|
| `GET /api/live` | the full live snapshot (routes, stations, journeys, route choice, developing incidents, track changes) |
| `GET /api/plan?journey=<id>` | one journey's plan (options, best, stringline, leave-by) |
| `GET /api/routes` | route/direction status board |
| `GET /api/station?id=<target id>` | one monitored platform's forecast |
| `GET /api/incidents` | developing incidents right now |
| `GET /api/health` | age of the last poll, store size, model readiness |

Responses are JSON with `Cache-Control: no-store`; all endpoints are read-only.

## Sizing

The collector keeps the last polls in memory only; the SQLite store grows by
roughly 15 MB/day with all stops of all feeds. A 1 vCPU / 1 GB VM is enough for
collection and serving; model refits take a minute or two on that hardware
with a month of history and run in a background thread.
