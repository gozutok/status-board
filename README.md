# board

Serverless status board for a 7.5" 800×480 e-paper panel running SenseCraft HMI (Web function). GitHub Actions polls free, key-less ADS-B sources every 15 minutes, renders `board.png`, and commits it; GitHub Pages serves `index.html`, which scales the image to fill whatever viewport SenseCraft's renderer uses.

## What is shown

Ident · origin → destination · status · type · registration · transmitted callsign · map with the actual flown path (solid, from OpenSky tracks and recorded positions), remaining great circle to destination (dotted) and position marker rotated to track · since-off timer · to-go estimate · progress bar · local OFF and ETA · GS (kt) · altitude as FL and feet · V/S · track · remaining NM · position age · data provenance line.

Outside a leg: photos from `idle/` (rotating hourly, dithered), or a date screen when the folder is empty.

## Sources (no API keys)

| Purpose | Source |
|---|---|
| Position, GS, altitude, V/S, track | api.adsb.lol → api.airplanes.live → opendata.adsb.fi → api.adsb.one → OpenSky states |
| Flown path since departure | OpenSky `/tracks/all?icao24=…&time=0`, merged with positions recorded each run |
| Origin / destination + coordinates | adsbdb `/v0/callsign/…`, fallback adsb.lol `/api/0/routeset` |
| Airline IATA → ICAO | OpenFlights airlines.dat, fallback adsbdb `/v0/airline/…` |
| Airport timezones | mwgg/Airports `airports.json` |
| Coastlines | johan/world.geo.json |

## How the aircraft is found

1. `reg` given → `/v2/reg/{reg}` on the feeds. Most reliable; many operators transmit alphanumeric callsigns that cannot be derived from the flight number.
2. Otherwise the ident is split into airline + number, the ICAO designator is looked up, and `ICAO+number` (plus zero-padded variants) is tried as a callsign.
3. Otherwise the ident's route is looked up in adsbdb, the feeds are scanned within 250 NM of six points along that route, and the aircraft whose transmitted callsign resolves (routeset) to the same airport pair is chosen.
4. Once found, the hex is cached in `data.json` for the rest of the leg.

Free feeds have no satellite coverage: over oceans the position freezes and `pos age` grows; the last known values stay on screen and the status becomes NO SIGNAL after 45 minutes. The path resumes when coverage returns.

## Setup

1. Push this folder to a **public** repo. Set `OWNER` and `REPO` at the top of the script in `enter.html` if the repo is not `gozutok/status-board`.
2. Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save.
3. Settings → Pages → Deploy from a branch → `main` / `/ (root)` → Save. Page: `https://YOUR_USER.github.io/board/`.
4. Put a few JPG/PNG photos into `idle/`.
5. Actions → **Update board** → Run workflow with empty fields once (activates the cron, renders the first idle photo).
6. SenseCraft HMI → device → **Web** → URL from step 3 → Set → shortest refresh interval → Preview → Save → Deploy.

## Daily use

Either: GitHub app → repo → Actions → Update board → Run workflow → `ident` (and `reg` if known) → Run.

Or: open `https://YOUR_USER.github.io/board/set.html` on the phone, add it to the home screen, and in Settings save `owner/repo` plus a fine-grained token (this repository only, Actions: Read and write). After that a leg is two fields and one tap. Polling stops `HOLD_AFTER_ARRIVAL_MIN` after arrival or 30 h after the input, then photos resume. Empty ident stops manually.

## Local test

```bash
pip install -r requirements.txt
echo '{"ident":"XX123","reg":"","set_at":"2026-01-01T00:00:00+00:00"}' > config.json
python3 fetch.py && python3 render.py
```

## Env (workflow)

`LOCAL_TZ`, `IDLE_ROTATE_MIN`, `HOLD_AFTER_ARRIVAL_MIN`.
