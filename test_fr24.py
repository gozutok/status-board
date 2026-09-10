"""Exercise the Flightradar24 paths locally, with the API stubbed.

The token lives in a repository secret, so these paths used to be verifiable only
by pushing and reading a workflow log — which is how seven functions were deleted
and only noticed in production. Recorded shapes, taken from the OpenAPI spec and
from real logged responses, make them testable here instead.
"""
import sys

import fetch

CALLS = []


def stub(responses):
    def fake(path, params):
        CALLS.append((path, dict(params)))
        for key, body in responses:
            if key in path:
                return body
        return None
    fetch.fr24_get = fake
    fetch.FR24_TOKEN = "test"
    CALLS.clear()


def pos_row(source, **kw):
    row = {"fr24_id": "abc123", "flight": "TK25", "callsign": "THY25", "lat": 31.5, "lon": 71.7,
           "track": 305, "alt": 34000, "gspeed": 480, "vspeed": 0, "hex": "4bb1c2",
           "type": "B77W", "reg": "TC-JJO", "orig_icao": "RCTP", "dest_icao": "LTFM",
           "timestamp": "2026-09-10T19:52:00Z", "source": source}
    row.update(kw)
    return row


def check(name, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'HATA'} {name}: {got!r}" + ("" if ok else f"  (beklenen {want!r})"))
    return ok


def main():
    good = True

    stub([("live/flight-positions", {"data": [pos_row("ADSB")]})])
    got = fetch.fr24_position("TK25", "")
    good &= check("ADSB fix okunuyor", got["pos"]["lat"], 31.5)
    good &= check("ADSB tahmin sayılmıyor", got["pos"]["estimated"], False)
    good &= check("kaynak etiketi", got["pos"]["source"], "fr24 adsb")
    good &= check("data_sources gönderiliyor",
                  CALLS[0][1].get("data_sources"), "ADSB,MLAT,ESTIMATED")
    good &= check("en fazla 3 öğe", len(CALLS[0][1]["data_sources"].split(",")), 3)

    stub([("live/flight-positions", {"data": [pos_row("ESTIMATED")]})])
    got = fetch.fr24_position("TK25", "")
    good &= check("ESTIMATED işaretleniyor", got["pos"]["estimated"], True)
    good &= check("kaynak etiketi", got["pos"]["source"], "fr24 estimated")

    stub([("live/flight-positions", {"data": []})])
    good &= check("boş yanıt None döner", fetch.fr24_position("TK25", "TC-JJO"), None)
    good &= check("tescil sonra uçuş no denendi", len(CALLS), 2)
    good &= check("önce tescil", "registrations" in CALLS[0][1], True)
    good &= check("sonra uçuş no", "flights" in CALLS[1][1], True)

    stub([("live/flight-positions", {"data": []})])
    fetch.fr24_position("TK25", "TC-JJO", "THY25", first_only=True)
    good &= check("first_only tek sorgu", len(CALLS), 1)

    print("PASS" if good else "FAIL")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
