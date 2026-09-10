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
FIXTURE = os.path.join("fixtures", "live_position.json")


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

    # nothing found anywhere: every filter tried on receptions, then all again on
    # estimates, and never the other way round
    stub(lambda p: [])
    good &= check("bos yanit None", fetch.fr24_position("TK25", "TC-JJO", "THY25"), None)
    order = [(next(k for k in p if k in ("registrations", "flights", "callsigns")), p.get("data_sources"))
             for _, p in CALLS]
    good &= check("sorgu sirasi", order, [
        ("registrations", "ADSB,MLAT"), ("flights", "ADSB,MLAT"), ("callsigns", "ADSB,MLAT"),
        ("registrations", "ESTIMATED"), ("flights", "ESTIMATED"), ("callsigns", "ESTIMATED")])
    good &= check("hicbir sorgu 3 ogeyi asmiyor",
                  max(len(p["data_sources"].split(",")) for _, p in CALLS) <= 3, True)
    good &= check("endpoint", CALLS[0][0], "/live/flight-positions/full")

    # a reception exists: estimates are never asked for
    stub(lambda p: [{"lat": 1.0, "lon": 2.0, "source": "ADSB"}] if p.get("data_sources") == "ADSB,MLAT" else [])
    fetch.fr24_position("TK25", "TC-JJO")
    good &= check("olculmus varken kestirim sorulmuyor",
                  [p.get("data_sources") for _, p in CALLS], ["ADSB,MLAT"])

    # nothing received, so the estimate is taken — and marked as one
    stub(lambda p: [{"lat": 1.0, "lon": 2.0, "source": "ESTIMATED"}] if p.get("data_sources") == "ESTIMATED" else [])
    got = fetch.fr24_position("TK25", "TC-JJO")
    good &= check("kestirime dusuluyor", got["pos"]["estimated"], True)
    good &= check("kaynak etiketi", got["pos"]["source"], "fr24 estimated")

    stub(lambda p: [])
    fetch.fr24_position("TK25", "TC-JJO", "THY25", first_only=True)
    good &= check("first_only tek filtre", len({p for _, p in
                                                [(c, next(k for k in q if k in ("registrations", "flights", "callsigns")))
                                                 for c, q in CALLS]}), 1)

    if os.path.exists(FIXTURE):
        row = json.load(open(FIXTURE, encoding="utf-8"))
        fetch.fr24_get = lambda path, params: {"data": [row]}
        got = fetch.fr24_position((row.get("flight") or "").upper(), "")
        good &= check("kayitli yanit ayristiriliyor", got["pos"]["lat"], row["lat"])
        good &= check("kayitli yanitta rota", bool(got["route"]), bool(row.get("dest_icao")))
    else:
        print(f"  ATLA  {FIXTURE} yok — ayristirma testi gercek yanit kaydedilince acilacak")

    print("PASS" if good else "FAIL")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
