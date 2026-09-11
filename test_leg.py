"""Walk a whole leg, the way it is actually used, with the clock under control.

The one thing this board exists for is entering a flight three or four hours before
it leaves and watching it through to the gate. That path was broken all of one day
and nobody found out until the owner tried to use it, because the tests covered
pieces and never the journey.

So this runs the real fetch.main() and render.main() at each point of a leg, feeding
each run's state into the next exactly as the chain does, with time frozen at the
moment being simulated and the network answered from here. It asserts the board
never crashes and that the status at each point is the one a person would expect.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

import fetch
import render

OFF = dt.datetime(2026, 9, 11, 14, 0, tzinfo=dt.timezone.utc)   # takeoff
BLOCK = dt.timedelta(hours=11)
ADB = {"lat": 38.292, "lon": 27.157}          # origin, İzmir
IST = {"lat": 41.261, "lon": 28.742}          # destination

AIRPORTS = {"LTBJ": {"iata": "ADB", "lat": ADB["lat"], "lon": ADB["lon"],
                     "tz": "Europe/Istanbul", "city": "İzmir", "elevation": 412},
            "LTFM": {"iata": "IST", "lat": IST["lat"], "lon": IST["lon"],
                     "tz": "Europe/Istanbul", "city": "İstanbul", "elevation": 325}}


def iso(t, z=False):
    return t.strftime("%Y-%m-%dT%H:%M:%S") + ("Z" if z else "")


def leg(takeoff, landed=None, fid="live1"):
    return {"fr24_id": fid, "flight": "TK9", "callsign": "THY9", "reg": "TC-JJV",
            "type": "B77W", "orig_icao": "LTBJ", "dest_icao": "LTFM",
            "datetime_takeoff": iso(takeoff), "datetime_landed": iso(landed) if landed else None,
            "flight_ended": "false" if landed is None else "true"}


def at(f, t):
    """A position f of the way along the great circle, at time t."""
    la, lo = fetch.gc_point(ADB["lat"], ADB["lon"], IST["lat"], IST["lon"], f)
    return la, lo, iso(t, z=True)


def step(name, now, summary, positions, tracks, expect):
    """One turn of the chain, at a frozen moment."""
    fetch.now_utc = lambda: now
    fetch.airports_db = lambda: AIRPORTS
    fetch.get = lambda url, **kw: None
    fetch.post = lambda url, body: None
    fetch.feed_lookup = lambda kind, value: None
    fetch.opensky_state = lambda h: None
    fetch.opensky_track = lambda h: None
    fetch.route_for_callsign = lambda cs, lat=None, lon=None: None
    fetch.search_by_route = lambda p, r: None
    fetch.FR24_TOKEN = "test"

    def fr24(path, params):
        if "flight-summary" in path:
            return {"data": summary}
        if "flight-tracks" in path:
            return tracks
        return {"data": positions}
    fetch.fr24_get = fr24

    try:
        fetch.main()
        render.main()
        got = json.load(open("data.json"))["status"]
    except Exception as e:
        import traceback
        print(f"  HATA {name}: {type(e).__name__}: {e}")
        tb = traceback.format_exc().strip().split("\n")
        for line in tb[-6:]:
            print("        " + line)
        return False
    ok = got == expect
    d = json.load(open("data.json"))
    extra = ""
    if not ok or os.environ.get("LEG_DEBUG"):
        extra = (f"\n        pos={bool(d.get('last_pos'))} age={d.get('pos_age_s')}"
                 f" src={(d.get('last_pos') or {}).get('source')}"
                 f" current={bool((d.get('hist') or {}).get('current'))}"
                 f" gap={d.get('track_gap_s')} leg_started={d.get('leg_started')}")
    print(f"  {'ok  ' if ok else 'HATA'} {name:34} {got}"
          + ("" if ok else f"  (beklenen {expect})") + extra)
    return ok


def main():
    work = tempfile.mkdtemp()
    here = os.getcwd()
    good = True
    try:
        os.chdir(work)
        os.makedirs(".cache", exist_ok=True)
        os.makedirs("idle", exist_ok=True)
        # the geodata, when this machine already has it. Without it render downloads
        # its own copy, which is slower but not wrong — and CI runs these before the
        # cache is restored, where insisting on it failed the whole run.
        src = os.path.join(here, ".cache")
        if os.path.isdir(src):
            shutil.copytree(src, ".cache", dirs_exist_ok=True)
        json.dump({"ident": "TK9", "reg": "", "date": "",
                   "set_at": iso(OFF - dt.timedelta(hours=4)) + "+00:00"},
                  open("config.json", "w"))

        prior = [leg(OFF - dt.timedelta(days=1), OFF - dt.timedelta(days=1) + BLOCK, "old1")]
        live = [leg(OFF)]

        def pos(f, t, gs, alt, vs=0, ground=False):
            la, lo, ts = at(f, t)
            return [{"fr24_id": "live1", "flight": "TK9", "callsign": "THY9", "lat": la, "lon": lo,
                     "track": 30, "alt": alt, "gspeed": gs, "vspeed": vs, "hex": "4bb1c2",
                     "type": "B77W", "reg": "TC-JJV", "orig_icao": "LTBJ", "dest_icao": "LTFM",
                     "timestamp": ts, "source": "ADSB"}]

        def track(upto, t):
            pts = []
            n = 8
            for i in range(n + 1):
                f = upto * i / n
                la, lo, ts = at(f, t - dt.timedelta(minutes=(n - i) * 10))
                pts.append({"timestamp": ts, "lat": la, "lon": lo, "alt": 35000,
                            "gspeed": 470, "vspeed": 0, "track": 30, "source": "ADSB"})
            return [{"fr24_id": "live1", "tracks": pts}]

        # the journey, in the order it happens
        t = OFF - dt.timedelta(hours=4)
        good &= step("T-4s  girildi, yayin yok", t, prior, [], [], "SCHEDULED")

        t = OFF - dt.timedelta(minutes=40)
        good &= step("T-40d kapida, transponder acik", t, prior + live,
                     pos(0.0, t, 0, 412, ground=True), [], "PREPARING")

        t = OFF - dt.timedelta(minutes=10)
        good &= step("T-10d takside", t, prior + live, pos(0.002, t, 18, 412), [], "TAXI")

        t = OFF + dt.timedelta(minutes=8)
        good &= step("T+8d  tirmanista", t, prior + live,
                     pos(0.02, t, 300, 12000, vs=2400), track(0.02, t), "CLIMB")

        t = OFF + dt.timedelta(hours=3)
        good &= step("T+3s  duz ucus", t, prior + live,
                     pos(0.28, t, 470, 35000), track(0.28, t), "CRUISE")

        t = OFF + dt.timedelta(hours=6)
        good &= step("T+6s  canli konum yok, iz var", t, prior + live, [], track(0.55, t), "CRUISE")

        t = OFF + dt.timedelta(hours=9)
        good &= step("T+9s  alcaliyor", t, prior + live,
                     pos(0.9, t, 320, 11000, vs=-1800), track(0.9, t), "DESCENT")

        t = OFF + BLOCK
        landed_leg = [leg(OFF, t, "live1")]
        good &= step("T+11s indi", t, prior + landed_leg,
                     pos(1.0, t, 12, 325), track(1.0, t), "LANDED")
    finally:
        os.chdir(here)
        shutil.rmtree(work, ignore_errors=True)

    print("PASS" if good else "FAIL")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
