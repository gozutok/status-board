import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

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
AIRLINES = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
AIRPORTS = "https://raw.githubusercontent.com/mwgg/Airports/master/airports.json"
CACHE = ".cache"
HOLD_AFTER_ARRIVAL = timedelta(minutes=int(os.environ.get("HOLD_AFTER_ARRIVAL_MIN", "60")))
EXPIRE_AFTER = timedelta(hours=30)
STALE_POS = timedelta(minutes=45)
LAST_CALL = {}


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


def norm_ac(a, feed, now_ms):
    alt = a.get("alt_baro")
    ground = alt == "ground"
    alt_ft = None if ground or alt is None else alt
    geo = False
    if alt_ft is None and a.get("alt_geom") is not None and not ground:
        alt_ft, geo = a["alt_geom"], True
    seen = a.get("seen_pos")
    ts = (now_ms / 1000 - (seen or 0)) if now_ms else None
    return {
        "hex": (a.get("hex") or "").lower(), "callsign": (a.get("flight") or "").strip(), "reg": a.get("r"),
        "type": a.get("t"), "lat": a.get("lat"), "lon": a.get("lon"), "alt_ft": alt_ft, "alt_geo": geo,
        "ground": ground, "gs_kt": a.get("gs"), "track": a.get("track"), "vs_fpm": a.get("baro_rate"),
        "pos_time": ts, "source": feed,
    }


def feed_lookup(kind, value):
    for name, base in FEEDS:
        if kind == "reg" and name in ("adsb.fi", "adsb.one"):
            continue
        j = get(f"{base}/{kind}/{value}")
        ac = (j or {}).get("ac") or []
        ac = [a for a in ac if a.get("lat") is not None]
        if ac:
            ac.sort(key=lambda a: a.get("seen_pos") or 0)
            return norm_ac(ac[0], name, j.get("now"))
    return None


def opensky_state(hex_):
    j = get(f"{OPENSKY}/states/all", params={"icao24": hex_})
    s = ((j or {}).get("states") or [None])[0]
    if not s or s[6] is None:
        return None
    return {
        "hex": hex_, "callsign": (s[1] or "").strip(), "reg": None, "type": None, "lat": s[6], "lon": s[5],
        "alt_ft": round(s[7] * 3.28084) if s[7] is not None else (round(s[13] * 3.28084) if s[13] is not None else None),
        "alt_geo": s[7] is None and s[13] is not None, "ground": bool(s[8]),
        "gs_kt": round(s[9] * 1.94384) if s[9] is not None else None, "track": s[10],
        "vs_fpm": round(s[11] * 196.85) if s[11] is not None else None, "pos_time": s[3], "source": "opensky",
    }


def opensky_track(hex_):
    j = get(f"{OPENSKY}/tracks/all", params={"icao24": hex_, "time": 0})
    if not j or not j.get("path"):
        return None
    return {"start": j.get("startTime"), "end": j.get("endTime"),
            "pts": [[p[0], p[1], p[2], round(p[3] * 3.28084) if p[3] is not None else None] for p in j["path"] if p[1] is not None]}


def route_for_callsign(callsign, lat=None, lon=None):
    j = get(f"{ADSBDB}/callsign/{callsign}")
    fr = ((j or {}).get("response") or {}).get("flightroute") if j else None
    if fr and fr.get("origin") and fr.get("destination"):
        o, d = fr["origin"], fr["destination"]
        return {"origin": {"icao": o.get("icao_code"), "iata": o.get("iata_code"), "lat": o.get("latitude"), "lon": o.get("longitude"), "name": o.get("municipality") or o.get("name")},
                "destination": {"icao": d.get("icao_code"), "iata": d.get("iata_code"), "lat": d.get("latitude"), "lon": d.get("longitude"), "name": d.get("municipality") or d.get("name")},
                "source": "adsbdb"}
    rs = post(ROUTESET, {"planes": [{"callsign": callsign, "lat": lat or 0, "lng": lon or 0}]})
    try:
        aps = rs[0]["_airports"]
        if len(aps) >= 2:
            o, d = aps[0], aps[-1]
            return {"origin": {"icao": o.get("icao"), "iata": o.get("iata"), "lat": o.get("lat"), "lon": o.get("lon"), "name": o.get("location") or o.get("name")},
                    "destination": {"icao": d.get("icao"), "iata": d.get("iata"), "lat": d.get("lat"), "lon": d.get("lon"), "name": d.get("location") or d.get("name")},
                    "source": "adsb.lol routeset"}
    except Exception:
        pass
    return None


def search_by_route(icao_prefix, route):
    o, d = route["origin"], route["destination"]
    if o.get("lat") is None or d.get("lat") is None:
        return None
    centers = [gc_point(o["lat"], o["lon"], d["lat"], d["lon"], f) for f in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
    seen, cands = set(), []
    for lat, lon in centers:
        j = get(f"https://api.adsb.lol/v2/point/{lat:.3f}/{lon:.3f}/250")
        for a in (j or {}).get("ac") or []:
            cs = (a.get("flight") or "").strip()
            if cs.startswith(icao_prefix) and a.get("hex") not in seen and a.get("lat") is not None:
                seen.add(a["hex"])
                cands.append((a, j.get("now")))
    if not cands:
        return None
    rs = post(ROUTESET, {"planes": [{"callsign": (a.get("flight") or "").strip(), "lat": a["lat"], "lng": a["lon"]} for a, _ in cands]}) or []
    pair = {o.get("icao"), d.get("icao")}
    for a, now_ms in cands:
        cs = (a.get("flight") or "").strip()
        for r in rs:
            if r.get("callsign") == cs and set((r.get("airport_codes") or "").split("-")) == pair and a.get("alt_baro") != "ground":
                return norm_ac(a, "adsb.lol", now_ms)
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
    hex_ = prev.get("hex")
    pos = None

    if hex_:
        pos = feed_lookup("hex", hex_) or opensky_state(hex_)
        out["providers"]["identify"] = "cached hex"
    if not pos and reg:
        pos = feed_lookup("reg", reg)
        if pos:
            out["providers"]["identify"] = "registration"
    if not pos and al_icao and number:
        cands = [f"{al_icao}{number}"]
        if number.isdigit():
            cands += [f"{al_icao}{int(number):0{n}d}" for n in (2, 3, 4) if f"{al_icao}{int(number):0{n}d}" not in cands]
        for cs in cands:
            pos = feed_lookup("callsign", cs)
            if pos:
                out["providers"]["identify"] = f"callsign {cs}"
                break
    route = prev.get("route")
    if not pos and al_icao and number and not route:
        route = route_for_callsign(f"{al_icao}{number}")
    if not pos and al_icao and route:
        pos = search_by_route(al_icao, route)
        if pos:
            out["providers"]["identify"] = "route match"

    if pos:
        hex_ = pos["hex"] or hex_
        out["providers"]["position"] = pos["source"]
    out["hex"] = hex_
    out["callsign"] = (pos or {}).get("callsign") or prev.get("callsign")
    out["reg"] = (pos or {}).get("reg") or prev.get("reg") or reg or None
    out["type"] = (pos or {}).get("type") or prev.get("type")

    if not route and out["callsign"]:
        route = route_for_callsign(out["callsign"], (pos or {}).get("lat"), (pos or {}).get("lon"))
    if not route and al_icao and number:
        route = route_for_callsign(f"{al_icao}{number}")
    if route:
        for k in ("origin", "destination"):
            a = ap.get(route[k].get("icao") or "")
            if a:
                route[k]["tz"] = a.get("tz")
                route[k].setdefault("lat", a.get("lat"))
                route[k].setdefault("lon", a.get("lon"))
        out["providers"]["route"] = route.get("source")
    out["route"] = route

    track = opensky_track(hex_) if hex_ else None
    path = prev.get("path") or []
    if track and track["pts"]:
        merged = {round(p[0] / 30): p for p in path}
        for p in track["pts"]:
            merged[round(p[0] / 30)] = p
        path = [merged[k] for k in sorted(merged)]
        out["providers"]["path"] = "opensky tracks"
    if pos and pos.get("lat") is not None:
        p = [pos["pos_time"] or now.timestamp(), pos["lat"], pos["lon"], pos["alt_ft"]]
        if not path or abs(path[-1][0] - p[0]) > 60:
            path.append(p)
        out["providers"].setdefault("path", "recorded")
    if len(path) > 1500:
        path = path[::2]
    out["path"] = path

    if not out.get("off_time"):
        airborne = [p for p in path if p[3] and p[3] > 1000]
        out["off_time"] = prev.get("off_time") or (airborne[0][0] if airborne else None)

    last = pos or prev.get("last_pos")
    if pos:
        out["last_pos"] = pos
    elif prev.get("last_pos"):
        out["last_pos"] = prev["last_pos"]
        out["providers"]["position"] = "last known"
    age = None
    if last and last.get("pos_time"):
        age = now.timestamp() - last["pos_time"]
    out["pos_age_s"] = age

    dest = (route or {}).get("destination") or {}
    if last and last.get("lat") is not None and dest.get("lat") is not None:
        rem = gc_dist_nm(last["lat"], last["lon"], dest["lat"], dest["lon"])
        out["remaining_nm"] = round(rem)
        if last.get("gs_kt") and last["gs_kt"] > 100:
            out["eta"] = (now + timedelta(hours=rem / last["gs_kt"])).isoformat()
        elif prev.get("eta"):
            out["eta"] = prev["eta"]
    landed = False
    if last and dest.get("lat") is not None:
        near = gc_dist_nm(last["lat"], last["lon"], dest["lat"], dest["lon"]) < 15
        if last.get("ground") and near:
            landed = True
        if age is not None and age > 1800 and near and out.get("off_time"):
            landed = True
    if prev.get("landed_at"):
        landed = True
    out["landed_at"] = prev.get("landed_at") or (now.isoformat() if landed else None)
    if landed:
        out["status"] = "ARRIVED"
    elif pos and pos.get("ground"):
        out["status"] = "ON GROUND"
    elif pos:
        out["status"] = "AIRBORNE"
    elif last and age is not None and age < STALE_POS.total_seconds():
        out["status"] = "AIRBORNE"
    elif last:
        out["status"] = "NO SIGNAL"
    else:
        out["status"] = "NOT FOUND"
    out["mode"] = "leg"
    json.dump(out, open("data.json", "w"), indent=1)
    print(out["status"], out["providers"])


if __name__ == "__main__":
    main()
