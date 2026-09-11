"""Run main() against the shapes it will really meet, offline.

Every flight set today failed on one line:

    (hist or {}).get("current", {}).get("fr24_id")

The default in .get(key, default) applies when the key is absent, not when its
value is None — and fr24_history always writes "current", leaving it None while
nothing is in the air. Entering a flight before it departs crashed every run.

check.py cannot see that, and a test that re-implements the expression would only
prove the re-implementation. So this drives the real main(), with the network
stubbed, through the states a leg actually passes through.
"""
import datetime as _dt
import json
import os
import shutil
import sys
import tempfile

import fetch

AIRPORTS = {"RKSI": {"iata": "ICN", "lat": 37.469, "lon": 126.451, "tz": "Asia/Seoul",
                     "city": "Seoul", "elevation": 23},
            "LTFM": {"iata": "IST", "lat": 41.261, "lon": 28.742, "tz": "Europe/Istanbul",
                     "city": "Istanbul", "elevation": 325}}

def _iso(hours_ago, z=False):
    """Fixtures anchored to now, so the tests do not rot as the date moves."""
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + ("Z" if z else "")


PAST_LEG = {"datetime_takeoff": _iso(48), "datetime_landed": _iso(37),
            "flight_ended": "true", "orig_icao": "RKSI", "dest_icao": "LTFM",
            "reg": "TC-JJF", "type": "B77W", "fr24_id": "old1", "callsign": "THY91"}

LIVE_LEG = dict(PAST_LEG, datetime_takeoff=_iso(3), datetime_landed=None,
                flight_ended="false", fr24_id="live1")

POSITION = {"fr24_id": "live1", "flight": "TK91", "callsign": "THY91", "lat": 45.0, "lon": 90.0,
            "track": 280, "alt": 35000, "gspeed": 470, "vspeed": 0, "hex": "4bb1c2",
            "type": "B77W", "reg": "TC-JJF", "orig_icao": "RKSI", "dest_icao": "LTFM",
            "timestamp": _iso(0, z=True), "source": "ADSB"}

TRACK = [{"fr24_id": "live1", "tracks": [
    {"timestamp": _iso(1, z=True), "lat": 43.0, "lon": 100.0, "alt": 35000,
     "gspeed": 470, "vspeed": 0, "track": 280, "source": "ADSB"},
    {"timestamp": _iso(0, z=True), "lat": 45.0, "lon": 90.0, "alt": 35000,
     "gspeed": 470, "vspeed": 0, "track": 280, "source": "ADSB"}]}]


def check(name, fn):
    try:
        fn()
        print(f"  ok   {name}")
        return True
    except Exception as e:
        print(f"  HATA {name}: {type(e).__name__}: {e}")
        return False


def offline(summary_rows, position_rows, tracks):
    """No network at all: every outbound call in fetch.py is answered from here."""
    def fr24(path, params):
        if "flight-summary" in path:
            return {"data": summary_rows}
        if "flight-tracks" in path:
            return tracks
        return {"data": position_rows}
    fetch.fr24_get = fr24
    fetch.FR24_TOKEN = "test"
    fetch.airports_db = lambda: AIRPORTS
    fetch.get = lambda url, **kw: None
    fetch.post = lambda url, body: None
    fetch.feed_lookup = lambda kind, value: None
    fetch.opensky_state = lambda hex_: None
    fetch.opensky_track = lambda hex_: None
    fetch.route_for_callsign = lambda cs, lat=None, lon=None: None
    fetch.search_by_route = lambda prefix, route: None


def run(case, summary_rows, position_rows, tracks, prev=None):
    work = tempfile.mkdtemp()
    here = os.getcwd()
    try:
        os.chdir(work)
        os.makedirs(".cache", exist_ok=True)
        json.dump({"ident": "TK91", "reg": "", "date": "",
                   "set_at": "2026-09-11T12:00:00+00:00"}, open("config.json", "w"))
        if prev:
            json.dump(prev, open("data.json", "w"))
        offline(summary_rows, position_rows, tracks)
        fetch.main()
        out = json.load(open("data.json"))
        print(f"  ok   {case}: {out.get('status')}")
        return True, out
    except Exception as e:
        print(f"  HATA {case}: {type(e).__name__}: {e}")
        return False, None
    finally:
        os.chdir(here)
        shutil.rmtree(work, ignore_errors=True)


def main():
    good = True

    # the state every flight was in this morning: history found, nothing airborne
    ok, _ = run("kalkis oncesi (gecmis var, havada bacak yok)", [PAST_LEG], [], [])
    good &= ok

    ok, _ = run("hic gecmis yok", [], [], [])
    good &= ok

    ok, st = run("havada, canli konum var", [LIVE_LEG], [POSITION], TRACK)
    good &= ok

    # airborne but the live filters find nothing — the route through the track
    ok, st = run("havada, canli konum yok, iz var", [LIVE_LEG], [], TRACK)
    good &= ok
    if st:
        print(f"       konum kaynagi: {(st.get('last_pos') or {}).get('source')}")

    ok, _ = run("hicbir sey yok", [], [], [])
    good &= ok

    # naive FR24 timestamps are UTC, whatever the machine's clock says
    import datetime as dt
    a = fetch.fr24_ts("2026-09-11T14:00:00")
    b = fetch.fr24_ts("2026-09-11T14:00:00Z")
    good &= check("Z'siz damga UTC sayiliyor", lambda: (a == b) or 1 / 0)
    print(f"       (bu makine {dt.datetime.now().astimezone().tzname()}, ikisi de "
          f"{dt.datetime.fromtimestamp(a, dt.timezone.utc).strftime('%H:%M')} UTC)")

    # and the pre-departure board says it is waiting, not that it lost the aircraft
    ok, st = run("kalkis oncesi durum dogru mu", [PAST_LEG], [], [])
    good &= ok
    if st:
        exp = st.get("expected_off")
        late = exp and fetch.now_utc().timestamp() > exp + 900
        want = "NO POSITION" if late else "SCHEDULED"
        good &= check(f"durum {want} olmali", lambda: (st["status"] == want) or 1 / 0)

    print("PASS" if good else "FAIL")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
