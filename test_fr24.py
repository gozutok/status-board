"""Check the requests this code sends, and parse only responses FR24 really sent.

The token is a repository secret, so these paths were once verifiable only by
pushing and reading a workflow log — which is how a slice edit deleted seven
functions and nobody noticed until the chain died.

What is asserted here is what this code controls: which endpoint, which filters, in
which order, with which sources. The response shape is FR24's, not ours, so nothing
is invented for it: the parsing checks run against fixtures/live_position.json, a row
recorded verbatim from a real reply, and are skipped with a warning until one exists.
"""
import json
import os
import sys

import fetch

CALLS = []


def stub(rows_for):
    def fake(path, params):
        CALLS.append((path, dict(params)))
        return {"data": rows_for(dict(params))}
    fetch.fr24_get = fake
    fetch.FR24_TOKEN = "test"
    CALLS.clear()


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'HATA'} {name}: {got!r}" + ("" if ok else f"  (beklenen {want!r})"))
    return ok


def main():
    good = True

    # nothing found: every filter tried once, all sources asked for each time
    stub(lambda p: [])
    good &= check("bos yanit None", fetch.fr24_position("TK25", "TC-JJO", "THY25"), None)
    order = [(next(k for k in p if k in ("registrations", "flights", "callsigns")), p.get("data_sources"))
             for _, p in CALLS]
    good &= check("sorgu sirasi", order, [
        ("registrations", "ADSB,MLAT,ESTIMATED"), ("flights", "ADSB,MLAT,ESTIMATED"),
        ("callsigns", "ADSB,MLAT,ESTIMATED")])
    good &= check("3 ogeyi asmiyor",
                  max(len(p["data_sources"].split(",")) for _, p in CALLS) <= 3, True)
    good &= check("endpoint", CALLS[0][0], "/live/flight-positions/full")

    # whichever source answers, the board is told which it was
    stub(lambda p: [{"lat": 1.0, "lon": 2.0, "source": "ADSB"}])
    got = fetch.fr24_position("TK25", "TC-JJO")
    good &= check("olculmus tahmin sayilmiyor", got["pos"]["estimated"], False)
    good &= check("tek cagri yetiyor", len(CALLS), 1)

    stub(lambda p: [{"lat": 1.0, "lon": 2.0, "source": "ESTIMATED"}])
    got = fetch.fr24_position("TK25", "TC-JJO")
    good &= check("kestirim isaretleniyor", got["pos"]["estimated"], True)
    good &= check("kaynak etiketi", got["pos"]["source"], "fr24 estimated")

    stub(lambda p: [])
    fetch.fr24_position("TK25", "TC-JJO", "THY25", first_only=True)
    good &= check("first_only tek filtre", len({p for _, p in
                                                [(c, next(k for k in q if k in ("registrations", "flights", "callsigns")))
                                                 for c, q in CALLS]}), 1)

    # Everything below parses responses Flightradar24 actually sent, captured from
    # the sandbox — which returns the production schema — rather than rows written
    # here from the specification, which would only ever have agreed with itself.
    def fixture(name):
        path = os.path.join("fixtures", name + ".json")
        return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None

    row = fixture("live_position")
    if row:
        fetch.fr24_get = lambda path, params: {"data": [row]}
        got = fetch.fr24_position((row.get("flight") or "").upper(), "")
        good &= check("kayitli konum ayristi", got["pos"]["lat"], row["lat"])
        good &= check("kaynak okundu", got["pos"]["source"], "fr24 " + row["source"].lower())
        good &= check("rota okundu", got["route"]["destination"]["icao"], row["dest_icao"])
        good &= check("zaman damgasi cozuldu", got["pos"]["pos_time"] > 0, True)
    else:
        print("  ATLA  fixtures/live_position.json yok")

    row = fixture("flight_summary")
    if row:
        fetch.fr24_get = lambda path, params: {"data": [row]}
        import datetime as _dt
        t = fetch.fr24_ts(row["datetime_takeoff"])
        now = _dt.datetime.fromtimestamp(t + 3600, _dt.timezone.utc)
        hist = fetch.fr24_history((row.get("flight") or "").upper(), now)
        good &= check("flight_ended metni cozuldu", bool(hist and hist.get("current")), True)
        if hist and hist.get("current"):
            good &= check("havadaki bacagin kimligi", hist["current"]["fr24_id"], row["fr24_id"])

    trk = fixture("flight_tracks")
    if trk:
        fetch.fr24_get = lambda path, params: trk
        got = fetch.fr24_track(trk[0]["fr24_id"])
        pts = trk[0]["tracks"]
        good &= check("iz noktalari okundu", len(got["pts"]), len(pts))
        good &= check("hiz turetilmiyor, okunuyor", got["last"]["gs"], pts[-1]["gspeed"])
        good &= check("yon turetilmiyor, okunuyor", got["last"]["track"], pts[-1]["track"])
        good &= check("dikey hiz okunuyor", got["last"]["vs"], pts[-1]["vspeed"])

    print("PASS" if good else "FAIL")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
