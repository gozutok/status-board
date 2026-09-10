# Status board: FR24 integration, phase-aware screens, aircraft-centred map

Date: 2026-09-10

## Problem

Three independent faults, plus two feature gaps.

1. **`render.py` is truncated.** Commit `ad813b0` cut the file from 374 to 334 lines,
   removing the tail of `svg_leg`, `svg_idle`, `write_page`, `main` and the
   `__main__` guard. The last surviving line is `    gs, alt, vs` — a bare tuple
   expression, so the file still compiles and `python3 render.py` exits 0 without
   writing anything. The workflow goes green while `index.html` stays frozen at
   the 08:09 UTC render. This is why the panel never changes.
2. **The cron has never fired.** All 32 runs are `workflow_dispatch`; zero
   `schedule` events.
3. **Identification fails.** Live `data.json` reports `hex: null`,
   `status: "NOT FOUND"` for TK281. The free ADS-B callsign chain does not resolve.
4. The whole-route map is unreadable on a 7.5" panel.
5. Every state that is not "airborne with data" renders badly: a flight entered
   3-4 h ahead shows `NOT FOUND`, and after arrival the to-go timer sits at `0:00`.

## Non-goals

The existing visual design is kept. Header, right-hand data column, footer
provenance line and their type scale do not change. The only new marks are inside
the map box.

## Design

### A. Repair

- Restore the removed tail of `render.py` from `7515089`.
- Add a workflow guard: after `render.py`, fail the job unless `index.html` was
  rewritten during this run. A silent no-op render can never again report success.
- `cron: */5`.
- Commit only when a meaningful field changed, not on every `generated` bump.

### B. Flightradar24

Explorer plan: 30 000 credits/month, 10 req/min, 30 days of history.

| Call | When | Credits |
|---|---|---|
| `live/flight-positions/full` | every run during a leg | 8 |
| `flight-summary/light` (last 3 days of the flight number) | once per leg | ~6 |
| `flight-tracks` | once, at first airborne detection | 40 |

A 10 h leg costs roughly 1 000 credits, so ~30 long-haul legs per month.

- Token from the `FR24_TOKEN` repository secret via workflow `env`. Never in code.
- `registrations=` when a registration is known, otherwise `flights=`.
- One response supplies position, ground speed, altitude, vertical speed, track,
  hex, registration, type, callsign, origin, destination and FR24's own ETA.
- On any failure — HTTP error, quota exhaustion, empty result — the existing
  adsb.lol / airplanes.live / adsb.fi / adsb.one / OpenSky chain runs unchanged as
  fallback. No existing source is removed.
- `flight-summary/light` over the previous three days yields the typical takeoff
  time and block time for the flight number. This is what makes the pre-departure
  screen useful when no live position exists yet.
- Verified first against the free sandbox key, which consumes no credits.

### C. Phases

`fetch.py` currently collapses everything airborne into one state. It gains a
vertical-speed split and two pre-departure states.

| phase | status | entered when | map |
|---|---|---|---|
| `scheduled` | SCHEDULED | ident set, nothing found yet | whole route, dotted |
| `preparing` | PREPARING | transponder on, at origin, GS < 5 | origin, 100 NM |
| `taxi` | TAXI | at origin, GS >= 5 | origin, 100 NM |
| `climb` | CLIMB | airborne, V/S > +300 fpm | aircraft-centred |
| `cruise` | CRUISE | airborne, abs(V/S) <= 300 fpm | aircraft-centred |
| `descent` | DESCENT | airborne, V/S < -300 fpm | aircraft-centred |
| `airborne` | NO SIGNAL | position older than 45 min | last known |
| `landed` | LANDED | on ground near destination | destination, 100 NM |
| `inbound` | INBOUND | aircraft still flying its previous leg | inbound route |

Right-hand column by phase:

- **Before takeoff** — `SINCE OFF` becomes `OFF IN` counting down to the expected
  takeoff; `TO GO est` shows the typical block time, or an em dash when unknown.
  Progress bar empty. Speed and altitude read as em dashes until the transponder
  is live.
- **Airborne** — unchanged from the current design.
- **Landed** — `SINCE OFF` freezes at the total flown time; `TO GO est` reads
  `LANDED` rather than `0:00`; the progress bar is full; remaining distance and
  the speed block read as em dashes.

### D. Map

- Aircraft centred, fixed 1000 NM width; height follows the 470x328 box, ~700 NM.
- Before departure and while inbound, the existing whole-route fit is kept.
- Coastlines and borders from Natural Earth 50m.
- A hairline dotted graticule every 5 degrees, so a mid-ocean crop is never empty.
- Country names from `LABEL_X`/`LABEL_Y`, ranked by `LABELRANK`, at most six,
  each with a white halo; labels colliding with each other or with the route line
  are dropped.
- The sea or ocean under the aircraft, from Natural Earth marine polygons, set in
  letter-spaced capitals in the manner of a chart.

### E. Storage

Flightradar24's terms forbid retaining API-derived data beyond 30 days, and a
public repository's history is permanent. `data.json` therefore moves out of git
into the `actions/cache` entry already used for geodata. Only `index.html` and
`board.png` are committed.

## Verification

Exercised end to end through the deployed workflow, including `enter.html`, and
confirmed against the live page rather than locally only.
