# board

Serverless status board for a 7.5" 800×480 e-paper panel running SenseCraft HMI (Web function). GitHub Actions polls Flightradar24 every 5 minutes, falls back to free key-less ADS-B feeds, and commits a rendered `index.html`; GitHub Pages serves it, scaled to whatever viewport SenseCraft's renderer uses.

Pages sends `Cache-Control: max-age=600` on everything and offers no way to change it, so the board is also written to `board.svg` and pulled by the page with a fresh timestamp on load and every two minutes. A query string is part of the cache key, so that request always reaches the origin. The page still carries the inline SVG for a renderer without JavaScript.

## What is shown

Ident · origin → destination · phase · type · registration · transmitted callsign, over a map
carrying the flown path (solid), the remaining great circle (dotted), a position marker
rotated to track, a 5° graticule, country names and the sea or ocean underneath.

The screen follows the leg:

| Phase | Shown as | Map |
|---|---|---|
| entered ahead of time, nothing transmitting | SCHEDULED, counting down to the expected takeoff | whole route |
| transponder live at the gate | PREPARING | whole route |
| moving on the ground at the origin | TAXI | origin, 400 NM |
| airborne, climbing | CLIMB | aircraft centred, 1000 NM |
| airborne, level | CRUISE | aircraft centred, 1000 NM |
| airborne, descending | DESCENT | aircraft centred, 1000 NM |
| position older than 45 min | NO SIGNAL, last known values held | last known |
| on the ground at the destination | LANDED, with flight time and landing time | destination, 400 NM |
| the aircraft is still flying its previous leg | INBOUND | inbound route |

Expected takeoff and block time come from the previous few legs of the same flight
number, which is what makes the screen useful when a flight is entered hours ahead.

Outside a leg: photos from `idle/` (rotating hourly, dithered), or a date screen when
the folder is empty.

## Sources

Flightradar24 is primary. Everything below it is free and key-less, and runs whenever
FR24 errors, runs out of credit or returns nothing.

| Purpose | Source |
|---|---|
| Position, GS, altitude, V/S, track, route, ETA | Flightradar24 `live/flight-positions/full` |
| Expected takeoff, block time | Flightradar24 `flight-summary/light`, previous 4 days |
| Flown path since departure | Flightradar24 `flight-tracks`, once per leg |
| Fallback position | api.adsb.lol → api.airplanes.live → opendata.adsb.fi → api.adsb.one → OpenSky states |
| Fallback path | OpenSky `/tracks/all`, merged with positions recorded each run |
| Fallback route | adsbdb `/v0/callsign/…`, then adsb.lol `/api/0/routeset` |
| Airline IATA → ICAO | OpenFlights airlines.dat, fallback adsbdb `/v0/airline/…` |
| Airport coordinates and timezones | mwgg/Airports `airports.json` |
| Coastlines, borders, country and sea names | Natural Earth 50m |

### Credits

The Explorer plan gives 30 000 credits a month. A live position costs 8, the history
lookup about 6 per leg, and the one-off track backfill 40. A ten-hour leg polled every
five minutes comes to roughly 1 000 credits, so around thirty long-haul legs a month.
FR24 is only called while a leg is set; idle runs cost nothing.

Flightradar24's terms forbid keeping API-derived data for more than 30 days, so the leg
state lives in the Actions cache and is never committed.

## How the aircraft is found

0. Flightradar24 is asked directly for the flight number, or the registration when one is given. This alone resolves nearly every leg.
1. `reg` given → `/v2/reg/{reg}` on the feeds. Most reliable; many operators transmit alphanumeric callsigns that cannot be derived from the flight number.
2. Otherwise the ident is split into airline + number, the ICAO designator is looked up, and `ICAO+number` (plus zero-padded variants) is tried as a callsign.
3. Otherwise the ident's route is looked up in adsbdb, the feeds are scanned within 250 NM of six points along that route, and the aircraft whose transmitted callsign resolves (routeset) to the same airport pair is chosen.
4. Once found, the hex is cached in the leg state for the rest of the leg.

Free feeds have no satellite coverage: over oceans the position freezes and `pos age` grows; the last known values stay on screen and the status becomes NO SIGNAL after 45 minutes. The path resumes when coverage returns.

## Setup

1. Push this folder to a **public** repo. Set `OWNER` and `REPO` at the top of the script in `enter.html` if the repo is not `gozutok/status-board`.
2. Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save.
3. Settings → Secrets and variables → Actions → New repository secret → `FR24_TOKEN`, holding a Flightradar24 API token. Without it the board still runs on the free feeds alone.
4. Settings → Pages → Deploy from a branch → `main` / `/ (root)` → Save. Page: `https://YOUR_USER.github.io/status-board/`.
5. Put a few JPG/PNG photos into `idle/`.
6. Actions → **Update board** → Run workflow with empty fields once (activates the cron, renders the first idle photo).
7. SenseCraft HMI → device → **Web** → URL from step 3 → Set → shortest refresh interval → Preview → Save → Deploy.

## Daily use

Either: GitHub app → repo → Actions → Update board → Run workflow → `ident` (and `reg` if known) → Run.

An optional date pins which day's departure the countdown targets, resolved in the origin airport's timezone. FR24 has no schedule data — every endpoint is live or historic — so the time of day still comes from the median of recent legs; the date only decides which day that time lands on.

Or: open `https://YOUR_USER.github.io/status-board/enter.html` on the phone, add it to the home screen, and under Token save a fine-grained token (this repository only, Actions: Read and write). After that a leg is two fields and one tap. Polling stops `HOLD_AFTER_ARRIVAL_MIN` after arrival or 30 h after the input, then photos resume. Empty ident stops manually.

## Local test

```bash
pip install -r requirements.txt
echo '{"ident":"XX123","reg":"","set_at":"2026-01-01T00:00:00+00:00"}' > config.json
FR24_TOKEN=... python3 fetch.py && python3 render.py
```

## Env (workflow)

`LOCAL_TZ`, `IDLE_ROTATE_MIN`, `HOLD_AFTER_ARRIVAL_MIN`, and the `FR24_TOKEN` secret.
