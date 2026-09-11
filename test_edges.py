"""The states a leg reaches that the straight-through journey never visits.

Idle with no flight set, an aircraft still flying its previous leg, a position gone
stale enough to be extrapolated, one stale enough to give up on, and the hold after
landing running out. Each has rendered a board at some point today; none had ever
been run end to end.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

import fetch
import render
from test_leg import ADB, AIRPORTS, IST, OFF, at, iso, leg

WORK = None


def setup(ident="TK9", reg="", set_at=None, prev=None):
    json.dump({"ident": ident, "reg": reg, "date": "",
               "set_at": (set_at or iso(OFF - dt.timedelta(hours=4))) + "+00:00"},
              open("config.json", "w"))
    if prev is not None:
        json.dump(prev, open("data.json", "w"))
    elif os.path.exists("data.json"):
        os.remove("data.json")


def step(name, now, summary, positions, tracks, expect=None):
    fetch.now_utc = lambda: now
    fetch.airports_db = lambda: AIRPORTS
    for fn in ("get", "post"):
        setattr(fetch, fn, lambda *a, **k: None)
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
        d = json.load(open("data.json"))
        got = d.get("status") or d.get("mode")
    except Exception as e:
        import traceback
        print(f"  HATA {name}: {type(e).__name__}: {e}")
        print("        " + traceback.format_exc().strip().split("\n")[-3].strip())
        return False, None
    ok = expect is None or got == expect
    print(f"  {'ok  ' if ok else 'HATA'} {name:36} {got}" + ("" if ok else f"  (beklenen {expect})"))
    return ok, d


def pos(f, t, gs, alt, vs=0, src="ADSB", fid="live1", flight="TK9"):
    la, lo, ts = at(f, t)
    return [{"fr24_id": fid, "flight": flight, "callsign": "THY9", "lat": la, "lon": lo,
             "track": 30, "alt": alt, "gspeed": gs, "vspeed": vs, "hex": "4bb1c2",
             "type": "B77W", "reg": "TC-JJV", "orig_icao": "LTBJ", "dest_icao": "LTFM",
             "timestamp": ts, "source": src}]


def track(upto, t, n=8):
    pts = []
    for i in range(n + 1):
        la, lo, ts = at(upto * i / n, t - dt.timedelta(minutes=(n - i) * 10))
        pts.append({"timestamp": ts, "lat": la, "lon": lo, "alt": 35000, "gspeed": 470,
                    "vspeed": 0, "track": 30, "source": "ADSB"})
    return [{"fr24_id": "live1", "tracks": pts}]


def main():
    global WORK
    here = os.getcwd()
    WORK = tempfile.mkdtemp()
    good = True
    try:
        os.chdir(WORK)
        os.makedirs(".cache", exist_ok=True)
        os.makedirs("idle", exist_ok=True)
        # the geodata, when this machine already has it. Without it render downloads
        # its own copy, which is slower but not wrong — and CI runs these before the
        # cache is restored, where insisting on it failed the whole run.
        src = os.path.join(here, ".cache")
        if os.path.isdir(src):
            shutil.copytree(src, ".cache", dirs_exist_ok=True)
        live = [leg(OFF)]
        prior = [leg(OFF - dt.timedelta(days=1), OFF - dt.timedelta(days=1) + dt.timedelta(hours=11), "old1")]

        setup(ident="")
        ok, _ = step("ucus yok, bos ekran", OFF, [], [], [], "idle")
        good &= ok

        # the aircraft is still on its previous leg, found by registration
        setup(reg="TC-JJV")
        t = OFF - dt.timedelta(hours=2)
        ok, _ = step("gelen ucak (baska bacakta)", t, prior,
                     pos(0.5, t, 460, 34000, flight="TK8", fid="prev1"), [], "INBOUND")
        good &= ok

        # pushback speed is not taxi speed: the cabin is still being prepared
        setup()
        t = OFF - dt.timedelta(minutes=12)
        ok, _ = step("geri itiliyor, taksi degil", t, prior,
                     pos(0.002, t, 6, 412), [], "PREPARING")
        good &= ok
        ok, _ = step("taksi hizina cikti", t, prior,
                     pos(0.002, t, 18, 412), [], "TAXI")
        good &= ok

        # airborne, then the fix goes stale: extrapolated, then given up on
        setup()
        t = OFF + dt.timedelta(hours=2)
        ok, d = step("havada, taze konum", t, prior + live, pos(0.2, t, 470, 35000), track(0.2, t), "CRUISE")
        good &= ok

        t = OFF + dt.timedelta(hours=2, minutes=35)
        ok, d = step("konum 35 dk bayat -> kestirim", t, prior + live, [], [], "CRUISE")
        good &= ok
        if d:
            print(f"        est_pos={bool(d.get('est_pos'))} age={int((d.get('pos_age_s') or 0)/60)} dk")
            good &= bool(d.get("est_pos")) or (print("  HATA kestirim uretilmedi") or False)

        t = OFF + dt.timedelta(hours=3, minutes=30)
        ok, d = step("konum 90 dk bayat", t, prior + live, [], [], "NO SIGNAL")
        good &= ok

        # landed, then the hold runs out and the photos come back
        setup()
        t = OFF + dt.timedelta(hours=11)
        landed = [leg(OFF, t, "live1")]
        ok, _ = step("indi", t, prior + landed, pos(1.0, t, 10, 325), track(1.0, t), "LANDED")
        good &= ok
        t = OFF + dt.timedelta(hours=13)
        ok, _ = step("inisten 2 saat sonra -> bos ekran", t, prior + landed, [], [], "idle")
        good &= ok

        # the chain comes back after the aircraft has already left on its next leg:
        # the landing still happened, and the board used to call the next flight
        # INBOUND and count down to tomorrow's departure of a leg already flown
        setup()
        t = OFF + dt.timedelta(hours=11)
        landed = [leg(OFF, t, "live1")]
        t2 = t + dt.timedelta(minutes=30)
        ok, _ = step("inisi gormedik, ucak sonraki bacakta", t2, prior + landed,
                     pos(0.4, t2, 460, 34000, flight="TK8", fid="next1"), [], "LANDED")
        good &= ok

        setup()
        t2 = t + dt.timedelta(hours=3)
        ok, _ = step("gormedigimiz inisin uzerinden 3 saat", t2, prior + landed,
                     pos(0.4, t2, 460, 34000, flight="TK8", fid="next1"), [], "idle")
        good &= ok
        t2 += dt.timedelta(minutes=6)
        ok, d = step("bos ekran boyle kalir", t2, prior + landed,
                     pos(0.4, t2, 460, 34000, flight="TK8", fid="next1"), [], "idle")
        good &= ok
        if d and not d.get("landed_at"):
            print("  HATA inis bilgisi bos ekranda unutuldu")
            good = False

        # the summary in hand was written before the aircraft moved: no leg in the
        # air, no arrival, and the departure long past. It has to be refetched, or
        # the board reads the aircraft's next flight as inbound for ever.
        t = OFF + dt.timedelta(hours=11)
        landed = [leg(OFF, t, "live1")]
        t2 = t + dt.timedelta(hours=3)
        stale_prev = {"ident": "TK9", "mode": "leg", "phase": "inbound",
                      "expected_off": OFF.timestamp(),
                      "hist": {"source": "fr24 history", "tod": OFF.timestamp() % 86400,
                               "block_s": 11 * 3600, "samples": 3, "current": None,
                               "arrived": None,
                               "fetched_at": iso(t2 - dt.timedelta(minutes=20)) + "+00:00"}}
        setup(prev=stale_prev)
        ok, _ = step("bayat ozet yenilenir", t2, prior + landed,
                     pos(0.4, t2, 460, 34000, flight="TK8", fid="next1"), [], "idle")
        good &= ok

        # an earlier leg of the same flight number, flown before the flight was
        # entered, must not be read as the arrival of the one being waited for
        setup(set_at=iso(OFF - dt.timedelta(hours=3)))
        earlier = [leg(OFF - dt.timedelta(hours=8), OFF - dt.timedelta(hours=7), "earlier1")]
        ok, _ = step("ayni sefer sayisinin onceki bacagi", OFF - dt.timedelta(hours=2),
                     earlier, [], [], "SCHEDULED")
        good &= ok

        # a leg set more than thirty hours ago expires on its own
        setup(set_at=iso(OFF - dt.timedelta(hours=40)))
        ok, _ = step("40 saat once girilmis, suresi dolmus", OFF, prior, [], [], "idle")
        good &= ok
    finally:
        os.chdir(here)
        shutil.rmtree(WORK, ignore_errors=True)
    print("PASS" if good else "FAIL")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
