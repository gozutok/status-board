import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

UA = {"User-Agent": "epaper-board/2.0 (github actions; personal status display)"}
FEEDS = [
    ("adsb.lol", "https://api.adsb.lol/v2"),
    ("airplanes.live", "https://api.airplanes.live/v2"),
    ("adsb.fi", "https://opendata.adsb.fi/api/v2"),
    ("adsb.one", "https://api.adsb.one/v2"),
]
OPENSKY = "https://opensky-network.org/api"
ADSBDB = "https://api.adsbdb.com/v0"
ROUTESET = "https://api.adsb.lol/api/0/routeset"
FR24 = "https://fr24api.flightradar24.com/api"
FR24_TOKEN = os.environ.get("FR24_TOKEN", "").strip()
AVSTACK = "http://api.aviationstack.com/v1/flights"
AIRLINES = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
AIRPORTS = "https://raw.githubusercontent.com/mwgg/Airports/master/airports.json"
CACHE = ".cache"
HOLD_AFTER_ARRIVAL = timedelta(minutes=int(os.environ.get("HOLD_AFTER_ARRIVAL_MIN", "60")))
EXPIRE_AFTER = timedelta(hours=30)
STALE_POS = timedelta(minutes=45)
EST_AFTER = timedelta(minutes=20)
SCHED_REFRESH = timedelta(minutes=60)
NEAR_NM = 15
LAST_CALL = {}
FR24_LOG = []


def now_utc():
    return datetime.now(timezone.utc)


def get(url, **kw):
    host = url.split("/")[2]
    wait = LAST_CALL.get(host, 0) + 1.1 - time.time()
    if wait > 0:
        time.sleep(wait)
    LAST_CALL[host] = time.time()
    try:
        r = requests.get(url, headers=UA, timeout=25, **kw)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def post(url, body):
    try:
        r = requests.post(url, headers={**UA, "Content-Type": "application/json"}, json=body, timeout=25)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def cached(url, fname, max_age_days=30):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, fname)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < max_age_days * 86400:
        return open(p, "rb").read()
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    open(p, "wb").write(r.content)
    return r.content


def airline_icao(iata):
    try:
        for line in cached(AIRLINES, "airlines.dat").decode("utf-8", "ignore").splitlines():
            f = [x.strip('"') for x in line.split(",")]
            if len(f) > 7 and f[3] == iata and f[4] and f[4] != r"\N" and f[7] == "Y":
                return f[4]
    except Exception:
        pass
    j = get(f"{ADSBDB}/airline/{iata}")
    try:
        return j["response"][0]["icao"]
    except Exception:
        return None


def airports_db():
    try:
        return json.loads(cached(AIRPORTS, "airports.json"))
    except Exception:
        return {}


def split_ident(ident):
    i = 0
    while i < len(ident) and not ident[i].isdigit():
        i += 1
    if i in (2, 3) and i < len(ident):
        return ident[:i], ident[i:]
    return None, None


def gc_dist_nm(lat1, lon1, lat2, lon2):
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2
    return 2 * math.asin(math.sqrt(a)) * 3440.065


def gc_point(lat1, lon1, lat2, lon2, f):
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    if d == 0:
        return lat1, lon1
    a, b = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
    x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
    y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
    z = a * math.sin(p1) + b * math.sin(p2)
    return math.degrees(math.atan2(z, math.sqrt(x * x + y * y))), math.degrees(math.atan2(y, x))


def parse_local(s, tzname):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if tzname:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tzname))
        except Exception:
            pass
    return dt.timestamp()


def fetch_schedule(ident, key, date, now):
    params = {"access_key": key, "flight_iata": ident, "limit": 20}
    if date:
        params["flight_date"] = date
    try:
        r = requests.get(AVSTACK, params=params, headers=UA, timeout=30)
        js = r.json() if r.status_code == 200 else None
    except Exception:
        js = None
    rows = [x for x in (js or {}).get("data") or [] if not (x.get("flight") or {}).get("codeshared")]
    if not rows:
        return None
    cands = []
    for x in rows:
        dep, arr = x.get("departure") or {}, x.get("arrival") or {}
        std = parse_local(dep.get("scheduled"), dep.get("timezone"))
        cands.append((x, std))
    cands = [c for c in cands if c[1]]
    if not cands:
        return None
    if date:
        cands.sort(key=lambda c: abs(c[1] - now.timestamp()))
    else:
        future = [c for c in cands if c[1] > now.timestamp() - 3 * 3600]
        cands = sorted(future, key=lambda c: c[1]) if future else sorted(cands, key=lambda c: -c[1])
    x, std = cands[0]
    dep, arr, ac = x.get("departure") or {}, x.get("arrival") or {}, x.get("aircraft") or {}
    return {
        "source": "aviationstack", "fetched_at": now.isoformat(), "flight_date": x.get("flight_date"),
        "status": x.get("flight_status"), "callsign_hint": (x.get("flight") or {}).get("icao"),
        "origin": {"icao": dep.get("icao"), "iata": dep.get("iata"), "tz": dep.get("timezone"), "name": dep.get("airport"), "terminal": dep.get("terminal"), "gate": dep.get("gate")},
        "destination": {"icao": arr.get("icao"), "iata": arr.get("iata"), "tz": arr.get("timezone"), "name": arr.get("airport"), "terminal": arr.get("terminal"), "gate": arr.get("gate")},
        "std": std, "etd": parse_local(dep.get("estimated"), dep.get("timezone")), "atd": parse_local(dep.get("actual"), dep.get("timezone")),
        "sta": parse_local(arr.get("scheduled"), arr.get("timezone")), "eta": parse_local(arr.get("estimated"), arr.get("timezone")), "ata": parse_local(arr.get("actual"), arr.get("timezone")),
        "dep_delay": dep.get("delay"), "reg": ac.get("registration"), "hex": (ac.get("icao24") or "").lower() or None, "type": ac.get("icao") or ac.get("iata"),
    }


def fr24_get(path, params):
    """One FR24 call. Returns the parsed body, or None on any failure."""
    if not FR24_TOKEN:
        return None
    host = "fr24api.flightradar24.com"
    wait = LAST_CALL.get(host, 0) + 6.5 - time.time()
    if wait > 0:
        time.sleep(wait)
    LAST_CALL[host] = time.time()
    try:
        r = requests.get(f"{FR24}{path}", params=params, timeout=25, headers={
            **UA, "Accept": "application/json", "Accept-Version": "v1",
            "Authorization": f"Bearer {FR24_TOKEN}"})
    except Exception as e:
        FR24_LOG.append(f"fr24 {path}: {type(e).__name__}")
        return None
    if r.status_code != 200:
        FR24_LOG.append(f"fr24 {path}: HTTP {r.status_code} {r.text[:180]}")
        return None
    try:
        j = r.json()
    except Exception:
        FR24_LOG.append(f"fr24 {path}: bad json")
        return None
    rows = j.get("data") if isinstance(j, dict) else j
    FR24_LOG.append("fr24 {} {} -> {} rows".format(
        path, {k: v for k, v in params.items() if k != "limit"},
        len(rows) if isinstance(rows, list) else "?"))
    return j


def fr24_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def fr24_probe_sources():
    """Which data_sources syntax the API actually accepts.

    Asked against a registration that cannot exist, so every variant returns no
    results and costs one credit: whether a request is rejected does not depend on
    what it would have matched. Runs once, then never again.
    """
    out = []
    for v in ("ADSB,MLAT,ESTIMATED,UAT", "ADSB,MLAT,ESTIMATED", "ESTIMATED", "adsb,mlat,estimated", None):
        params = {"registrations": "ZZ-ZZZZ", "limit": 1}
        if v is not None:
            params["data_sources"] = v
        before = len(FR24_LOG)
        j = fr24_get("/live/flight-positions/full", params)
        note = FR24_LOG[before] if len(FR24_LOG) > before else "?"
        out.append(f"[{v or 'yok'}] {'OK' if j is not None else note.split(': ', 1)[-1]}")
    return out


def fr24_position(ident, reg, callsign=None, first_only=False):
    """Live position for the leg. 8 credits a query, and later ones only run empty."""
    queries = []
    if reg:
        queries.append({"registrations": reg})
    if ident:
        queries.append({"flights": ident})
    if callsign:
        queries.append({"callsigns": callsign})
    if first_only:
        queries = queries[:1]
    rows = []
    for q in queries:
        j = fr24_get("/live/flight-positions/full", dict(q, limit=5))
        rows = [x for x in (j or {}).get("data") or [] if x.get("lat") is not None]
        if rows:
            break
    if not rows:
        return None
    exact = [x for x in rows if (x.get("flight") or "").upper().replace(" ", "") == ident]
    x = (exact or rows)[0]
    flight = (x.get("flight") or "").upper().replace(" ", "")
    is_leg = flight == ident if flight else not reg
    alt, gs = x.get("alt"), x.get("gspeed")
    # FR24 reports barometric altitude above sea level, not above the field, so an
    # absolute ceiling puts every aircraft at Mexico City (7316 ft) or Quito (9200
    # ft) in the air while it is still at the gate. Ground speed is the reliable
    # signal — an airliner is never airborne below 40 kt — and the altitude test
    # only guards the case where speed is missing at cruise.
    ground = (gs or 0) < 40 and (alt or 0) < 15000
    pos = {
        "hex": (x.get("hex") or "").lower(), "callsign": (x.get("callsign") or "").strip(),
        "reg": x.get("reg"), "type": x.get("type"), "lat": x.get("lat"), "lon": x.get("lon"),
        "alt_ft": None if ground else alt, "alt_geo": False, "ground": ground,
        "gs_kt": gs, "track": x.get("track"), "vs_fpm": x.get("vspeed"),
        "pos_time": fr24_ts(x.get("timestamp")), "source": "fr24",
    }
    route = None
    if x.get("orig_icao") and x.get("dest_icao"):
        route = {"origin": {"icao": x.get("orig_icao"), "iata": x.get("orig_iata")},
                 "destination": {"icao": x.get("dest_icao"), "iata": x.get("dest_iata")},
                 "source": "fr24"}
    return {"pos": pos, "route": route, "eta": x.get("eta"), "fr24_id": x.get("fr24_id"),
            "is_leg": is_leg}


def fr24_history(ident, now):
    """Previous legs of this flight number: typical takeoff time, block time, route.

    This is what fills the screen when the flight is entered hours ahead and no
    live position exists yet.
    """
    j = fr24_get("/flight-summary/light", {
        "flights": ident, "limit": 10,
        "flight_datetime_from": (now - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%S"),
        "flight_datetime_to": now.strftime("%Y-%m-%dT%H:%M:%S")})
    rows = (j or {}).get("data") or []
    offs, blocks, last_row = [], [], None
    for r in rows:
        t, l = fr24_ts(r.get("datetime_takeoff")), fr24_ts(r.get("datetime_landed"))
        if t:
            offs.append(t % 86400)
            last_row = r
        if t and l and 0 < l - t < 20 * 3600:
            blocks.append(l - t)
    live = None
    for r in rows:
        t = fr24_ts(r.get("datetime_takeoff"))
        ended = r.get("flight_ended")
        if t and (ended is False or not r.get("datetime_landed")) and 0 < now.timestamp() - t < 20 * 3600:
            if live is None or t > live["off"]:
                live = {"fr24_id": r.get("fr24_id"), "off": t, "reg": r.get("reg"),
                        "type": r.get("type"), "callsign": r.get("callsign"),
                        "orig": r.get("orig_icao"), "dest": r.get("dest_icao_actual") or r.get("dest_icao")}
    if not offs:
        return {"source": "fr24 history", "tod": None, "block_s": None, "samples": 0,
                "current": live} if live else None
    offs.sort(); blocks.sort()
    out = {"source": "fr24 history", "tod": offs[len(offs) // 2], "current": live,
           "block_s": blocks[len(blocks) // 2] if blocks else None, "samples": len(offs)}
    if last_row and last_row.get("orig_icao") and last_row.get("dest_icao"):
        out["route"] = {"origin": {"icao": last_row["orig_icao"]}, "destination": {"icao": last_row["dest_icao"]},
                        "source": "fr24 history"}
    out["reg"], out["type"] = (last_row or {}).get("reg"), (last_row or {}).get("type")
    return out


def expected_off_ts(tod, now, date_str, origin_tz):
    """UTC timestamp of the departure being counted down to.

    `tod` is the usual takeoff time expressed as seconds into the UTC day, so the
    calendar day it belongs to has to be chosen. With a date given, pick the
    candidate whose date *at the origin* matches — a 02:00 local departure falls on
    the previous UTC day, and asking in UTC would silently pick the wrong leg.
    Without one, take the next occurrence still ahead of us.
    """
    if tod is None:
        return None
    base = int(now.timestamp()) // 86400 * 86400
    cands = [base + tod + k * 86400 for k in (-1, 0, 1, 2)]
    if date_str:
        try:
            want = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            want = None
        if want:
            try:
                tz = ZoneInfo(origin_tz) if origin_tz else timezone.utc
            except Exception:
                tz = timezone.utc
            for c in cands:
                if datetime.fromtimestamp(c, timezone.utc).astimezone(tz).date() == want:
                    return c
            return None
    for c in cands:
        if c > now.timestamp() - 3 * 3600:
            return c
    return cands[-1]


def backtest_dr(pts, dest, block_s, origin):
    """Measure the extrapolation against the track it came from.

    Takes a fix an hour before the end, guesses forward from it by exactly the rule
    the board uses, and compares with where the aircraft actually was. Costs
    nothing — the track is already paid for — and turns "is the estimate any good"
    into a number instead of an opinion.
    """
    if not pts or len(pts) < 4 or dest.get("lat") is None:
        return None
    end = pts[-1]
    past = None
    for q in reversed(pts[:-1]):
        if end[0] - q[0] >= 3600:
            past = q
            break
    if past is None:
        return None
    before = None
    for q in reversed([x for x in pts if x[0] <= past[0]][:-1]):
        if past[0] - q[0] >= 180:
            before = q
            break
    gs = None
    if before and past[0] > before[0]:
        gs = gc_dist_nm(before[1], before[2], past[1], past[2]) / ((past[0] - before[0]) / 3600)
        if not 60 <= gs <= 620:
            gs = None
    guess = dead_reckon({"lat": past[1], "lon": past[2], "gs_kt": gs}, end[0] - past[0],
                        None, block_s, origin, dest, None)
    if not guess:
        return None
    return {"gap_min": round((end[0] - past[0]) / 60),
            "err_nm": round(gc_dist_nm(guess["lat"], guess["lon"], end[1], end[2]))}


def dead_reckon(last, age_s, off_time, block_s, origin, dest, now):
    """Where the aircraft should be, when the last real fix has gone stale.

    Anchored on that fix rather than on the departure: the fix is measured, and only
    the gap since it needs filling. With no fix at all — nothing was ever received —
    it walks the great circle from the origin on elapsed time instead.
    """
    if dest.get("lat") is None:
        return None
    if last and last.get("lat") is not None and age_s is not None:
        gs = last.get("gs_kt")
        if not gs or not 100 <= gs <= 620:
            total = gc_dist_nm(origin["lat"], origin["lon"], dest["lat"], dest["lon"]) \
                if origin.get("lat") is not None else None
            gs = (total / (block_s / 3600)) if total and block_s else 460
        left = gc_dist_nm(last["lat"], last["lon"], dest["lat"], dest["lon"])
        if left <= 0:
            return None
        f = min(gs * (age_s / 3600) / left, 0.98)
        lat, lon = gc_point(last["lat"], last["lon"], dest["lat"], dest["lon"], f)
        return {"lat": lat, "lon": lon, "from_s": age_s, "anchor": "fix"}
    if off_time and block_s and now is not None and origin.get("lat") is not None:
        f = min(max((now.timestamp() - off_time) / block_s, 0.0), 0.98)
        lat, lon = gc_point(origin["lat"], origin["lon"], dest["lat"], dest["lon"], f)
        return {"lat": lat, "lon": lon, "from_s": now.timestamp() - off_time, "anchor": "off"}
    return None


def main():
    now = now_utc()
    cfg = json.load(open("config.json")) if os.path.exists("config.json") else {}
    data = json.load(open("data.json")) if os.path.exists("data.json") else {}
    ident = (cfg.get("ident") or "").strip().upper().replace(" ", "")
    reg = (cfg.get("reg") or "").strip().upper()
    set_at = cfg.get("set_at")
    out = {"ident": ident, "reg_input": reg, "generated": now.isoformat(), "providers": {}, "log": []}

    if not ident and not reg:
        out["mode"] = "idle"
        json.dump(out, open("data.json", "w"), indent=1)
        return
    if set_at and now - datetime.fromisoformat(set_at) > EXPIRE_AFTER:
        out["mode"] = "idle"
        out["log"].append("expired")
        json.dump(out, open("data.json", "w"), indent=1)
        return
    prev = data if data.get("ident") == ident and data.get("mode") != "idle" else {}
    if prev.get("landed_at") and now - datetime.fromisoformat(prev["landed_at"]) > HOLD_AFTER_ARRIVAL:
        out["mode"] = "idle"
        out["log"].append("arrived, hold expired")
        json.dump(out, open("data.json", "w"), indent=1)
        return

    ap = airports_db()
    al_iata, number = split_ident(ident)
    al_icao = airline_icao(al_iata) if al_iata else None
    date = (cfg.get("date") or "").strip() or None
    av_key = os.environ.get("AVIATIONSTACK_KEY")

    hist = prev.get("hist")
    hist_at = datetime.fromisoformat(hist["fetched_at"]) if hist and hist.get("fetched_at") else None
    prev_phase = prev.get("phase")
    lost = (prev_phase in ("scheduled", "airborne")
            and not (prev.get("last_pos") or {}).get("lat")
            and not (hist or {}).get("current"))
    stale = hist_at is not None and now - hist_at > (timedelta(minutes=10) if lost else SCHED_REFRESH)
    if FR24_TOKEN and ident and (not hist or "current" not in hist
                                 or (prev_phase in (None, "scheduled", "preparing", "inbound") and stale)):
        fresh_hist = fr24_history(ident, now)
        if fresh_hist:
            fresh_hist["fetched_at"] = now.isoformat()
            hist = fresh_hist
        elif not hist:
            out["log"].append("fr24 history: no previous legs")
    out["hist"] = hist

    sched = prev.get("sched")
    fetched = datetime.fromisoformat(sched["fetched_at"]) if sched and sched.get("fetched_at") else None
    phase_prev = prev.get("phase")
    if av_key and (not sched or (phase_prev in (None, "scheduled", "preparing", "inbound", "taxi") and now - fetched > SCHED_REFRESH)):
        fresh = fetch_schedule(ident, av_key, date, now)
        if fresh:
            if sched:
                for k in ("reg", "hex", "type"):
                    fresh[k] = fresh.get(k) or sched.get(k)
            sched = fresh
            out["providers"]["schedule"] = "aviationstack"
        elif sched:
            out["providers"]["schedule"] = "aviationstack (cached)"
        else:
            out["log"].append("schedule lookup failed")
    elif sched:
        out["providers"]["schedule"] = sched.get("source", "cached")
    out["sched"] = sched

    if sched:
        for k in ("origin", "destination"):
            a = ap.get(sched[k].get("icao") or "")
            if a:
                sched[k]["lat"], sched[k]["lon"] = a.get("lat"), a.get("lon")
                sched[k]["tz"] = sched[k].get("tz") or a.get("tz")

    hex_ = prev.get("hex") or (sched or {}).get("hex")
    reg = reg or (sched or {}).get("reg") or prev.get("reg") or ""
    pos = None
    fr24_route = fr24_eta = fr24_id = None
    fr24_is_leg = False

    fr24_inbound_route = None
    track_pts_seed = None
    if FR24_TOKEN and not prev.get("src_probed"):
        out["src_probed"] = True
        out["log"].append("data_sources: " + " | ".join(fr24_probe_sources()))
    else:
        out["src_probed"] = prev.get("src_probed") or False

    misses = prev.get("live_misses") or 0
    if FR24_TOKEN:
        thin = misses >= 3 and misses % 5 != 0
        got = fr24_position(ident, reg,
                            None if thin else (f"{al_icao}{number}" if al_icao and number else None),
                            first_only=thin)
        out["live_misses"] = 0 if got else misses + 1
        if got:
            pos = got["pos"]
            fr24_eta, fr24_id, fr24_is_leg = got["eta"], got["fr24_id"], got["is_leg"]
            # Matched by registration on a different flight number: the aircraft is
            # still flying its previous leg, so its route is the inbound one.
            if fr24_is_leg:
                fr24_route = got["route"]
            else:
                fr24_inbound_route = got["route"]
            out["providers"]["identify"] = "fr24 " + ("registration" if reg else "flight number")
    if not pos and hex_:
        pos = feed_lookup("hex", hex_) or opensky_state(hex_)
        if pos:
            out["providers"]["identify"] = "hex"
    if not pos and reg:
        pos = feed_lookup("reg", reg)
        if pos:
            out["providers"]["identify"] = "registration"
    if not pos and al_icao and number:
        for cs in [f"{al_icao}{number}"] + ([f"{al_icao}{int(number):0{n}d}" for n in (2, 3, 4)]
                                            if number.isdigit() else []):
            pos = feed_lookup("callsign", cs)
            if pos:
                out["providers"]["identify"] = f"callsign {cs}"
                break
    # Three live filters can all miss a flight FR24 is plainly tracking, and over
    # China the free feeds have no receivers either. The leg is still reachable:
    # flight-summary names the one in the air right now, and its track ends at the
    # aircraft's current position. Last because it costs forty credits against
    # eight for a live fix, and the free feeds have just had their chance.
    # Forty credits is worth paying for a position, and worth paying once for the
    # same position. Over China the track advanced roughly hourly while we asked
    # every six minutes, so back off whenever an answer brings nothing newer, and
    # snap back the moment it does.
    track_gap = prev.get("track_gap_s") or 0
    track_asked = prev.get("track_asked_at") or 0
    may_ask = now.timestamp() - track_asked >= track_gap
    out["track_gap_s"], out["track_asked_at"] = track_gap, track_asked
    if not pos and may_ask and (hist or {}).get("current", {}).get("fr24_id"):
        cur = hist["current"]
        tr = fr24_track(cur["fr24_id"])
        out["track_asked_at"] = now.timestamp()
        newest = ((tr or {}).get("pts") or [[0]])[-1][0]
        if newest > (prev.get("track_end") or 0):
            out["track_end"], out["track_gap_s"] = newest, 0
        else:
            out["track_gap_s"] = min(max(track_gap * 2, 600), 1800)
            out["track_end"] = prev.get("track_end") or 0
        pts = (tr or {}).get("pts") or []
        if pts:
            p0 = pts[-1]
            # Two adjacent fixes can be seconds and a rounding apart, which turns
            # into a ground speed of hundreds of knots either way. Walk back for a
            # gap wide enough to average over.
            prev_p = None
            for q in reversed(pts[:-1]):
                if p0[0] - q[0] >= 180:
                    prev_p = q
                    break
            brg = None
            if prev_p and (prev_p[1], prev_p[2]) != (p0[1], p0[2]):
                brg = math.degrees(math.atan2(
                    math.sin(math.radians(p0[2] - prev_p[2])) * math.cos(math.radians(p0[1])),
                    math.cos(math.radians(prev_p[1])) * math.sin(math.radians(p0[1]))
                    - math.sin(math.radians(prev_p[1])) * math.cos(math.radians(p0[1]))
                    * math.cos(math.radians(p0[2] - prev_p[2])))) % 360
            gs = None
            if prev_p and p0[0] > prev_p[0]:
                gs = round(gc_dist_nm(prev_p[1], prev_p[2], p0[1], p0[2]) / ((p0[0] - prev_p[0]) / 3600))
                if not 60 <= gs <= 620:
                    gs = None
            pos = {"hex": (hex_ or ""), "callsign": cur.get("callsign") or "", "reg": cur.get("reg"),
                   "type": cur.get("type"), "lat": p0[1], "lon": p0[2],
                   "alt_ft": p0[3], "alt_geo": False,
                   "ground": (gs or 0) < 40 and (p0[3] or 0) < 15000,
                   "gs_kt": gs, "track": brg, "vs_fpm": None,
                   "pos_time": p0[0], "source": "fr24 tracks"}
            fr24_id = cur["fr24_id"]
            fr24_is_leg = True
            out["providers"]["identify"] = "fr24 summary"
            out["fr24_track_done"] = True
            track_pts_seed = pts
    elif not pos and prev.get("last_pos") and (hist or {}).get("current"):
        pos = None  # nothing new to say; the stale fix and the estimate carry it
    route = fr24_route or prev.get("route")
    if not route and hist and hist.get("route"):
        route = hist["route"]
    if not route and sched and sched["origin"].get("lat") is not None and sched["destination"].get("lat") is not None:
        route = {"origin": dict(sched["origin"]), "destination": dict(sched["destination"]), "source": "aviationstack"}
    if not pos and al_icao and number and not route:
        route = route_for_callsign(f"{al_icao}{number}")
    if not pos and al_icao and route and prev.get("leg_started"):
        pos = search_by_route(al_icao, route)
        if pos:
            out["providers"]["identify"] = "route match"

    if pos:
        hex_ = pos["hex"] or hex_
        out["providers"]["position"] = pos["source"]
    out["hex"] = hex_
    out["callsign"] = (pos or {}).get("callsign") or prev.get("callsign")
    out["reg"] = (pos or {}).get("reg") or prev.get("reg") or reg or None
    out["type"] = (pos or {}).get("type") or prev.get("type") or (sched or {}).get("type")

    if not route and out["callsign"]:
        route = route_for_callsign(out["callsign"], (pos or {}).get("lat"), (pos or {}).get("lon"))
    if not route and al_icao and number:
        route = route_for_callsign(f"{al_icao}{number}")
    if route:
        for k in ("origin", "destination"):
            a = ap.get(route[k].get("icao") or "")
            if a:
                route[k]["tz"] = route[k].get("tz") or a.get("tz")
                route[k]["iata"] = route[k].get("iata") or a.get("iata")
                route[k]["name"] = route[k].get("name") or a.get("city") or a.get("name")
                route[k].setdefault("lat", a.get("lat"))
                route[k].setdefault("lon", a.get("lon"))
        out["providers"]["route"] = route.get("source")
    out["route"] = route
    origin = (route or {}).get("origin") or {}
    dest = (route or {}).get("destination") or {}

    last = pos or prev.get("last_pos")
    if pos:
        out["last_pos"] = pos
    elif prev.get("last_pos"):
        out["last_pos"] = prev["last_pos"]
        out["providers"]["position"] = "last known"
    age = (now.timestamp() - last["pos_time"]) if last and last.get("pos_time") else None
    out["pos_age_s"] = age

    near_origin = last is not None and origin.get("lat") is not None and last.get("lat") is not None and gc_dist_nm(last["lat"], last["lon"], origin["lat"], origin["lon"]) < NEAR_NM
    near_dest = last is not None and dest.get("lat") is not None and last.get("lat") is not None and gc_dist_nm(last["lat"], last["lon"], dest["lat"], dest["lon"]) < NEAR_NM
    std = (sched or {}).get("std")
    etd = (sched or {}).get("etd") or std

    seen_ground = prev.get("seen_ground_at_origin") or (bool(pos) and pos.get("ground") and near_origin)
    leg_started = prev.get("leg_started") or False
    if pos and not pos.get("ground") and not leg_started:
        if fr24_is_leg:
            leg_started = True
        elif seen_ground or (near_origin and (pos.get("alt_ft") or 0) < 15000 and (std is None or now.timestamp() > std - 3600)):
            leg_started = True
        elif not sched and not route:
            leg_started = True
    out["seen_ground_at_origin"] = seen_ground
    out["leg_started"] = leg_started

    inbound = None
    if pos and not leg_started:
        cs = pos.get("callsign")
        ir = fr24_inbound_route
        if ir:
            for k in ("origin", "destination"):
                a = ap.get(ir[k].get("icao") or "")
                if a:
                    ir[k].setdefault("lat", a.get("lat"))
                    ir[k].setdefault("lon", a.get("lon"))
                    ir[k]["iata"] = ir[k].get("iata") or a.get("iata")
        elif cs and cs != prev.get("inbound_cs"):
            ir = route_for_callsign(cs, pos.get("lat"), pos.get("lon"))
        else:
            ir = prev.get("inbound_route")
        inbound = {"callsign": cs, "route": ir, "ground": pos.get("ground")}
        out["inbound_cs"], out["inbound_route"] = cs, ir
        if ir and ir.get("destination", {}).get("lat") is not None and not pos.get("ground") and pos.get("gs_kt"):
            rem_in = gc_dist_nm(pos["lat"], pos["lon"], ir["destination"]["lat"], ir["destination"]["lon"])
            inbound["eta"] = (now + timedelta(hours=rem_in / pos["gs_kt"])).isoformat()
            inbound["remaining_nm"] = round(rem_in)
    out["inbound"] = inbound

    flying = bool(pos) and not pos.get("ground")
    track, track_src = None, None
    done = prev.get("fr24_track_done") or False
    if track_pts_seed:
        track, track_src, done = {"start": track_pts_seed[0][0], "pts": track_pts_seed}, "fr24 tracks", True
    elif flying and fr24_id and not done:
        track = fr24_track(fr24_id)
        if track:
            track_src, done = "fr24 tracks", True
    if not track and hex_ and flying:
        track = opensky_track(hex_)
        track_src = "opensky tracks" if track else None
    out["fr24_track_done"] = done
    path = prev.get("path") or [] if leg_started else []
    if leg_started:
        if track and track["pts"]:
            merged = {round(p[0] / 30): p for p in path}
            for p in track["pts"]:
                merged[round(p[0] / 30)] = p
            path = [merged[k] for k in sorted(merged) if merged[k][0] >= track["start"] - 60]
            out["providers"]["path"] = track_src or "tracks"
        if pos and pos.get("lat") is not None:
            p = [pos["pos_time"] or now.timestamp(), pos["lat"], pos["lon"], pos["alt_ft"]]
            if not path or abs(path[-1][0] - p[0]) > 60:
                path.append(p)
            out["providers"].setdefault("path", "recorded")
        if len(path) > 1500:
            path = path[::2]
    out["path"] = path
    if inbound and track and track["pts"]:
        out["inbound_path"] = [[p[0], p[1], p[2], p[3]] for p in track["pts"]][-400:]
    elif inbound and prev.get("inbound_path"):
        out["inbound_path"] = prev["inbound_path"]

    airborne = [p for p in path if p[3] and p[3] > 1000]
    out["off_time"] = airborne[0][0] if airborne else prev.get("off_time")
    if not out["off_time"] and leg_started and pos and not pos.get("ground"):
        out["off_time"] = pos.get("pos_time") or now.timestamp()
    if not prev.get("off_block") and pos and pos.get("ground") and near_origin and (pos.get("gs_kt") or 0) >= 5:
        out["off_block"] = pos.get("pos_time") or now.timestamp()
    else:
        out["off_block"] = prev.get("off_block")

    if leg_started and not prev.get("landed_at") and dest.get("lat") is not None \
            and (age is None or age > EST_AFTER.total_seconds() or not last or last.get("lat") is None):
        est = dead_reckon(last, age, out.get("off_time"), out.get("block_s") or (hist or {}).get("block_s"),
                          origin, dest, now)
        if est:
            out["est_pos"] = est
            bt = backtest_dr(out.get("path") or [], dest,
                             out.get("block_s") or (hist or {}).get("block_s"), origin)
            if bt:
                out["log"].append(f"dead reckon backtest: {bt['err_nm']} NM over {bt['gap_min']} min")

    ref = out.get("est_pos") or last
    if ref and ref.get("lat") is not None and dest.get("lat") is not None and leg_started:
        rem = gc_dist_nm(ref["lat"], ref["lon"], dest["lat"], dest["lon"])
        out["remaining_nm"] = round(rem)
        if fr24_eta:
            out["eta"] = fr24_eta
        elif last.get("gs_kt") and last["gs_kt"] > 100:
            out["eta"] = (now + timedelta(hours=rem / last["gs_kt"])).isoformat()
        elif prev.get("eta"):
            out["eta"] = prev["eta"]
    if not out.get("eta") and (sched or {}).get("sta"):
        out["eta_sched"] = datetime.fromtimestamp(sched["eta"] or sched["sta"], timezone.utc).isoformat()

    landed = bool(prev.get("landed_at"))
    if leg_started and last and dest.get("lat") is not None:
        if last.get("ground") and near_dest:
            landed = True
        if age is not None and age > 1800 and near_dest and out.get("off_time"):
            landed = True
    out["landed_at"] = prev.get("landed_at") or (now.isoformat() if landed else None)

    if hist:
        tod = hist.get("tod")
        if tod is None and hist.get("expected_off"):
            tod = hist["expected_off"] % 86400
        out["expected_off"] = expected_off_ts(tod, now, date, origin.get("tz"))
        out["block_s"] = hist.get("block_s")
        out["date"] = date
    if out.get("off_time") and out.get("landed_at"):
        out["flown_s"] = datetime.fromisoformat(out["landed_at"]).timestamp() - out["off_time"]

    vs = (last or {}).get("vs_fpm") if last else None
    # A single vertical-speed reading taken once every six minutes is noisy: level
    # cruise routinely shows a few hundred feet a minute. Where two samples are
    # available the altitude between them says it far better — 300 fpm sustained is
    # 1800 ft over that gap — so V/S is only the fallback for the first fix.
    prev_pos = prev.get("last_pos") or {}
    d_alt = None
    if pos and pos.get("alt_ft") is not None and prev_pos.get("alt_ft") is not None \
            and pos.get("pos_time") != prev_pos.get("pos_time"):
        d_alt = pos["alt_ft"] - prev_pos["alt_ft"]
    fresh_pos = bool(pos) or (last and age is not None and age < STALE_POS.total_seconds())
    if landed:
        phase, status = "landed", "LANDED"
    elif leg_started and pos and pos.get("ground") and near_dest:
        phase, status = "landed", "LANDED"
    elif leg_started and pos and pos.get("ground") and near_origin:
        phase, status = "taxi", "TAXI"
    elif leg_started and fresh_pos:
        if d_alt is not None:
            rising, falling = d_alt > 400, d_alt < -400
        else:
            rising, falling = (vs or 0) > 300, (vs or 0) < -300
        if rising:
            phase, status = "climb", "CLIMB"
        elif falling:
            phase, status = "descent", "DESCENT"
        else:
            phase, status = "cruise", "CRUISE"
    elif leg_started:
        phase, status = "airborne", "NO SIGNAL"
    elif pos and pos.get("ground") and near_origin:
        if (pos.get("gs_kt") or 0) >= 5:
            phase, status = "taxi", "TAXI"
            leg_started = True
            out["leg_started"] = True
        else:
            phase, status = "preparing", "PREPARING"
    elif pos and not pos.get("ground"):
        phase, status = "inbound", "INBOUND"
    elif pos:
        phase, status = "preparing", "ON GROUND"
    elif route or sched or hist:
        # No position is two different facts. Before the expected departure it means
        # the aircraft has not started broadcasting yet, which is ordinary. After it,
        # the flight is very likely airborne and we have simply lost it — saying
        # "transponder off" there is an assertion the board cannot support, and the
        # route distance is no longer anything to call "remaining".
        exp = out.get("expected_off")
        overdue = exp is not None and now.timestamp() > exp + 900
        phase, status = "scheduled", ("NO POSITION" if overdue else "SCHEDULED")

    else:
        phase, status = "scheduled", "NOT FOUND"
    out["phase"], out["status"] = phase, status
    out["mode"] = "leg"
    json.dump(out, open("data.json", "w"), indent=1)
    out["log"] += FR24_LOG
    print(status, out["providers"], FR24_LOG or "")


if __name__ == "__main__":
    main()
