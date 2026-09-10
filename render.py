import base64
import html
import json
import math
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 800, 480
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
WORLD_GEOJSON = f"{NE}/ne_50m_admin_0_countries.geojson"
MARINE_GEOJSON = f"{NE}/ne_50m_geography_marine_polys.geojson"
PLACES_GEOJSON = f"{NE}/ne_50m_populated_places.geojson"
LAKES_GEOJSON = f"{NE}/ne_50m_lakes.geojson"
MAX_LABELS = 6
MAX_CITIES = 5
CACHE = ".cache"
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "Europe/Istanbul"))
IDLE_DIR = "idle"
IDLE_ROTATE = timedelta(minutes=int(os.environ.get("IDLE_ROTATE_MIN", "60")))


def font(size, bold=False):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def geo(url, fname):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, fname)
    if not os.path.exists(p):
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        open(p, "wb").write(r.content)
    return json.load(open(p))


def world():
    return geo(WORLD_GEOJSON, "ne50_countries.geojson")


def marine():
    return geo(MARINE_GEOJSON, "ne50_marine.geojson")


def places():
    return geo(PLACES_GEOJSON, "ne50_places_full.geojson")


def lakes():
    return geo(LAKES_GEOJSON, "ne50_lakes.geojson")


def rings(feat):
    g = feat.get("geometry") or {}
    coords = g.get("coordinates") or []
    polys = coords if g.get("type") == "MultiPolygon" else [coords]
    for poly in polys:
        for ring in poly:
            yield ring


def point_in_ring(ring, lat, lon):
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


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


def gc_line(p1, p2, n=60):
    return [gc_point(p1[0], p1[1], p2[0], p2[1], i / n) for i in range(n + 1)]


class MapView:
    def __init__(self, box, lats, lons, center=None, width_nm=None):
        self.x0, self.y0, self.x1, self.y1 = box
        self.pw, self.ph = self.x1 - self.x0, self.y1 - self.y0
        if center is not None and width_nm:
            clat, clon = center
            half_lat = (width_nm / 2) * (self.ph / self.pw) / 60
            half_lon = (width_nm / 2) / 60 / max(math.cos(math.radians(clat)), 0.15)
            self.lat_min, self.lat_max = clat - half_lat, clat + half_lat
            self.lon_min, self.lon_max = clon - half_lon, clon + half_lon
            return
        pad = 4
        lat_min, lat_max = min(lats) - pad, max(lats) + pad
        lon_min, lon_max = min(lons) - pad, max(lons) + pad
        span_lat, span_lon = max(lat_max - lat_min, 6), max(lon_max - lon_min, 8)
        if span_lon / span_lat < self.pw / self.ph:
            extra = span_lat * self.pw / self.ph - span_lon
            lon_min -= extra / 2
            lon_max += extra / 2
        else:
            extra = span_lon * self.ph / self.pw - span_lat
            lat_min -= extra / 2
            lat_max += extra / 2
        self.lat_min, self.lat_max, self.lon_min, self.lon_max = lat_min, lat_max, lon_min, lon_max

    def xy(self, lat, lon):
        return (self.x0 + (lon - self.lon_min) / (self.lon_max - self.lon_min) * self.pw,
                self.y0 + (self.lat_max - lat) / (self.lat_max - self.lat_min) * self.ph)

    def visible(self, ring, margin=2.0):
        """Reject a ring whose bounding box cannot touch the viewport."""
        lons = [c[0] for c in ring]
        lats = [c[1] for c in ring]
        return not (max(lons) < self.lon_min - margin or min(lons) > self.lon_max + margin
                    or max(lats) < self.lat_min - margin or min(lats) > self.lat_max + margin)

    def thinned(self, ring, min_px=1.2):
        """Ring in screen space, dropping points that land on the same pixel.

        Natural Earth 50m carries far more detail than a 470px box can show; without
        this the emitted SVG path runs to megabytes.
        """
        out = []
        for lon, lat in ((c[0], c[1]) for c in ring):
            x, y = self.xy(lat, lon)
            if out and abs(x - out[-1][0]) < min_px and abs(y - out[-1][1]) < min_px:
                continue
            out.append((x, y))
        return out

    @property
    def span(self):
        return max(self.lat_max - self.lat_min, self.lon_max - self.lon_min)

    def contains(self, lat, lon, inset=0):
        x, y = self.xy(lat, lon)
        return self.x0 + inset < x < self.x1 - inset and self.y0 + inset < y < self.y1 - inset

    def center(self):
        return (self.lat_min + self.lat_max) / 2, (self.lon_min + self.lon_max) / 2


def draw_world(d, view, g):
    for feat in g["features"]:
        for ring in rings(feat):
            if not view.visible(ring):
                continue
            pts = view.thinned(ring)
            if len(pts) > 2:
                d.polygon(pts, fill=255, outline=0)


def draw_marker(d, x, y, heading, size=15):
    h = math.radians(heading or 0)
    pts = [(0, -size), (size * 0.6, size * 0.7), (0, size * 0.3), (-size * 0.6, size * 0.7)]
    rot = [(x + px * math.cos(h) - py * math.sin(h), y + px * math.sin(h) + py * math.cos(h)) for px, py in pts]
    d.polygon(rot, fill=0)
    d.ellipse([x - size - 4, y - size - 4, x + size + 4, y + size + 4], outline=0, width=2)


def hm(secs):
    if secs is None:
        return "--:--"
    secs = int(abs(secs))
    return f"{secs // 3600}:{(secs % 3600) // 60:02d}"


def local(ts, tz):
    if ts is None:
        return "--:--"
    dt = datetime.fromtimestamp(ts, timezone.utc) if isinstance(ts, (int, float)) else datetime.fromisoformat(ts)
    try:
        return dt.astimezone(ZoneInfo(tz)).strftime("%H:%M") if tz else dt.astimezone(LOCAL_TZ).strftime("%H:%M")
    except Exception:
        return dt.astimezone(LOCAL_TZ).strftime("%H:%M")


def render_idle(state, now):
    files = sorted(f for f in os.listdir(IDLE_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png"))) if os.path.isdir(IDLE_DIR) else []
    if not files:
        img = Image.new("1", (W, H), 1)
        d = ImageDraw.Draw(img)
        d.text((W // 2, 200), now.astimezone(LOCAL_TZ).strftime("%d %B %Y"), font=font(52, True), fill=0, anchor="mm")
        d.text((W // 2, 280), now.astimezone(LOCAL_TZ).strftime("%A"), font=font(32), fill=0, anchor="mm")
        return img, state
    idx = state.get("idle_index", -1)
    at = state.get("idle_at")
    if at is None or now - datetime.fromisoformat(at) >= IDLE_ROTATE or idx < 0:
        idx = (idx + 1) % len(files)
        state = {**state, "idle_index": idx, "idle_at": now.isoformat()}
    src = Image.open(os.path.join(IDLE_DIR, files[idx % len(files)])).convert("L")
    scale = max(W / src.width, H / src.height)
    src = src.resize((round(src.width * scale), round(src.height * scale)), Image.LANCZOS)
    left, top = (src.width - W) // 2, (src.height - H) // 2
    return ImageOps.autocontrast(src.crop((left, top, left + W, top + H)), cutoff=1).convert("1"), state


def render_leg(dt, now):
    img = Image.new("1", (W, H), 1)
    d = ImageDraw.Draw(img)
    route = dt.get("route") or {}
    o, dest = route.get("origin") or {}, route.get("destination") or {}
    last = dt.get("last_pos") or {}
    path = dt.get("path") or []
    status = dt.get("status") or ""
    o_code, d_code = o.get("iata") or o.get("icao") or "???", dest.get("iata") or dest.get("icao") or "???"

    d.rectangle([0, 0, W, 70], fill=0)
    d.text((16, 35), dt.get("ident") or dt.get("callsign") or "", font=font(44, True), fill=1, anchor="lm")
    d.text((215, 35), f"{o_code} → {d_code}", font=font(40, True), fill=1, anchor="lm")
    d.text((W - 16, 22), status, font=font(28, True), fill=1, anchor="rm")
    sub = " · ".join(x for x in [dt.get("type"), dt.get("reg"), dt.get("callsign")] if x)
    d.text((W - 16, 52), sub, font=font(18), fill=1, anchor="rm")

    lat, lon = last.get("lat"), last.get("lon")
    box = (0, 72, 470, 400)
    have_dest = dest.get("lat") is not None
    pts_path = [(p[1], p[2]) for p in path if p[1] is not None]
    if lat is not None or pts_path or (o.get("lat") is not None and have_dest):
        lats = [p[0] for p in pts_path] + ([lat] if lat is not None else []) + [x["lat"] for x in (o, dest) if x.get("lat") is not None]
        lons = [p[1] for p in pts_path] + ([lon] if lon is not None else []) + [x["lon"] for x in (o, dest) if x.get("lon") is not None]
        rem = gc_line((lat, lon), (dest["lat"], dest["lon"])) if lat is not None and have_dest else []
        lats += [p[0] for p in rem]
        lons += [p[1] for p in rem]
        view = MapView(box, lats, lons)
        m = Image.new("1", (view.pw, view.ph), 1)
        md = ImageDraw.Draw(m)
        view.x0, view.y0 = 0, 0
        draw_world(md, view, world())
        if rem:
            rp = [view.xy(*p) for p in rem]
            for i in range(0, len(rp) - 1, 2):
                md.line([rp[i], rp[i + 1]], fill=0, width=2)
        if len(pts_path) > 1:
            md.line([view.xy(*p) for p in pts_path], fill=0, width=4)
        for ap, code in ((o, o_code), (dest, d_code)):
            if ap.get("lat") is None:
                continue
            x, y = view.xy(ap["lat"], ap["lon"])
            md.ellipse([x - 6, y - 6, x + 6, y + 6], fill=0)
            if x > view.pw - 60:
                md.text((x - 10, y + 8), code, font=font(20, True), fill=0, anchor="ra")
            else:
                md.text((x + 10, y + 8), code, font=font(20, True), fill=0)
        if lat is not None:
            x, y = view.xy(lat, lon)
            draw_marker(md, x, y, last.get("track"))
        img.paste(m, (box[0], box[1]))
    else:
        d.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), "Waiting for first position", font=font(24), fill=0, anchor="mm")
    d.rectangle(box, outline=0)

    x = 486
    off = dt.get("off_time")
    since = now.timestamp() - off if off else None
    eta = dt.get("eta")
    togo = (datetime.fromisoformat(eta) - now).total_seconds() if eta and status != "LANDED" else None
    d.text((x, 78), "SINCE OFF", font=font(16), fill=0)
    d.text((x, 94), hm(since), font=font(44, True), fill=0)
    d.text((x + 152, 78), "TO GO est", font=font(16), fill=0)
    d.text((x + 152, 94), hm(togo), font=font(44, True), fill=0)
    total = (since or 0) + (togo or 0)
    frac = 1.0 if status == "LANDED" else (since / total if since and total else 0.0)
    d.rectangle([x, 158, W - 16, 174], outline=0, width=2)
    d.rectangle([x, 158, x + int((W - 16 - x) * frac), 174], fill=0)
    d.text((x, 180), f"OFF {local(off, o.get('tz'))} {o_code}", font=font(17), fill=0)
    d.text((W - 16, 180), f"ETA {local(eta, dest.get('tz'))} {d_code}", font=font(17), fill=0, anchor="ra")

    gs, alt, vs = last.get("gs_kt"), last.get("alt_ft"), last.get("vs_fpm")
    d.text((x, 212), "GS", font=font(16), fill=0)
    d.text((x, 228), f"{int(gs)} kt" if gs is not None else "—", font=font(34, True), fill=0)
    d.text((x + 152, 212), "ALT" + (" geo" if last.get("alt_geo") else ""), font=font(16), fill=0)
    d.text((x + 152, 228), f"FL{int(round(alt / 100)):03d}" if alt is not None else "—", font=font(34, True), fill=0)
    d.text((x, 276), f"V/S {int(vs):+d} fpm" if vs is not None else "V/S —", font=font(17), fill=0)
    d.text((x + 152, 276), f"{int(alt):,} ft".replace(",", " ") if alt is not None else "", font=font(17), fill=0)
    trk = last.get("track")
    d.text((x, 308), "TRK", font=font(16), fill=0)
    d.text((x, 324), f"{int(trk):03d}°" if trk is not None else "—", font=font(34, True), fill=0)
    d.text((x + 152, 308), "REMAINING", font=font(16), fill=0)
    d.text((x + 152, 326), f"{dt['remaining_nm']} NM" if dt.get("remaining_nm") is not None else "—", font=font(30, True), fill=0)
    d.text((x, 372), f"pos age {int(dt['pos_age_s'] // 60)} min" if dt.get("pos_age_s") is not None and dt["pos_age_s"] >= 60 else (f"pos age {int(dt['pos_age_s'])} s" if dt.get("pos_age_s") is not None else ""), font=font(17), fill=0)

    d.line([0, 402, W, 402], fill=0, width=2)
    if lat is not None:
        age = max(0, dt.get("pos_age_s") or 0) if dt.get("pos_age_s") is not None else None
        age_txt = f"{int(age // 60)} min" if age is not None and age >= 60 else (f"{int(age)} s" if age is not None else "")
        src = (dt.get("providers") or {}).get("position") or ""
        d.text((16, 412), f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}   {src} {age_txt}", font=font(21), fill=0)
    else:
        d.text((16, 412), "No position yet", font=font(21), fill=0)
    pv = dt.get("providers") or {}
    foot = f"path: {pv.get('path', '—')} {len(path)} pts · route: {pv.get('route', '—')} · id: {pv.get('identify', '—')}"
    d.text((16, 447), foot, font=font(16), fill=0)
    d.text((W - 16, 447), f"upd {now.astimezone(LOCAL_TZ).strftime('%d.%m %H:%M')}", font=font(16), fill=0, anchor="ra")
    return img


def grid_step(span):
    for step in (1, 2, 5, 10, 20, 30):
        if span / step <= 7:
            return step
    return 45


def svg_graticule(view):
    """Lat/lon grid.

    The panel is 1-bit: it has no grey, so a hairline at 55% opacity — which is
    what this used to be — thresholds away to nothing. Solid black, a full pixel
    wide. The dashes are long enough not to be mistaken for the sea stipple
    underneath them.
    """
    step = grid_step(view.span)
    out = []
    lo = int(math.floor(view.lon_min / step)) * step
    while lo <= view.lon_max:
        x, _ = view.xy(view.lat_min, lo)
        out.append(f'<line x1="{x:.0f}.5" y1="{view.y0}" x2="{x:.0f}.5" y2="{view.y1}"/>')
        lo += step
    la = int(math.floor(view.lat_min / step)) * step
    while la <= view.lat_max:
        _, y = view.xy(la, view.lon_min)
        out.append(f'<line x1="{view.x0}" y1="{y:.0f}.5" x2="{view.x1}" y2="{y:.0f}.5"/>')
        la += step
    if not out:
        return ""
    return ('<g stroke="#000" stroke-width="1" stroke-dasharray="6 5">' + "".join(out) + "</g>")


def svg_sea(view, avoid):
    """Name the water under the centre of the crop, set like a chart."""
    clat, clon = view.center()
    best = None
    for f in marine()["features"]:
        p = f["properties"]
        if not any(point_in_ring(r, clat, clon) for r in rings(f)):
            continue
        rank = p.get("scalerank") if p.get("scalerank") is not None else 9
        if best is None or rank > best[0]:
            best = (rank, (p.get("name_tr") or p.get("label") or p.get("name") or "").upper())
    if not best or not best[1]:
        return ""
    label = best[1]
    half = 5.6 * len(label) / 2
    x = (view.x0 + view.x1) / 2
    for y in (view.y0 + 26, view.y1 - 14):
        if not any(abs(x - px) < half + pw and abs(y - py) < 24 for px, py, pw in avoid):
            avoid.append((x, y, half))
            return (f'<text x="{x:.0f}" y="{y:.0f}" font-size="13" letter-spacing="3" text-anchor="middle" '
                    f'fill="#000" paint-order="stroke" stroke="#fff" stroke-width="3.5">'
                    f'{esc(label)}</text>')
    return ""


def svg_cities(view, avoid):
    """Nearby towns, once the crop is tight enough for them to mean anything.

    At whole-route zoom these are noise, so they only appear when the view has
    narrowed to roughly a cruise crop or closer.
    """
    if view.span > 30:
        return ""
    cands = []
    for f in places()["features"]:
        p = f["properties"]
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        lon, lat = coords[0], coords[1]
        name = p.get("NAME_TR") or p.get("NAME")
        if lat is None or not name or not view.contains(lat, lon, inset=8):
            continue
        cands.append(((p.get("SCALERANK") if p.get("SCALERANK") is not None else 9),
                      -(p.get("POP_MAX") or 0), name, lat, lon))
    cands.sort()
    out = []
    for _, _, name, lat, lon in cands:
        x, y = view.xy(lat, lon)
        half = 3.4 * len(name)
        tx, ty = x + 5, y + 4
        if not (view.x0 + 2 < tx and tx + 2 * half < view.x1 - 2):
            tx = x - 5 - 2 * half
        if any(abs(x - px) < half + pw and abs(y - py) < 16 for px, py, pw in avoid):
            continue
        avoid.append((x + half, y, half + 6))
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="#000"/>'
                   f'<text x="{tx:.0f}" y="{ty:.0f}" font-size="11" fill="#000" '
                   f'paint-order="stroke" stroke="#fff" stroke-width="3">{esc(name)}</text>')
        if len(out) >= MAX_CITIES:
            break
    return "".join(out)


def svg_countries(view, avoid, limit=MAX_LABELS):
    """Country names by Natural Earth label point.

    MIN_LABEL is the scale at which Natural Earth intends a name to appear; keying
    off it keeps a whole-route view to the countries a reader actually orients by,
    instead of whichever small state happens to fall inside the crop.
    """
    span = view.span
    thresh = 2.0 if span > 60 else 3.0 if span > 25 else 5.0 if span > 10 else 99
    cands = []
    for f in world()["features"]:
        p = f["properties"]
        lx, ly, name = p.get("LABEL_X"), p.get("LABEL_Y"), p.get("NAME_TR") or p.get("NAME")
        if lx is None or ly is None or not name:
            continue
        mn = p.get("MIN_LABEL")
        if mn is not None and mn > thresh:
            continue
        cands.append(((mn if mn is not None else 9), (p.get("LABELRANK") or 9), name, lx, ly,
                       p.get("ABBREV")))
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    out = []
    for _, _, name, lx, ly, abbrev in cands:
        x, y = view.xy(ly, lx)
        label, half = None, 0
        # "Amerika Birleşik Devletleri" will not fit in a 470px box at any useful
        # zoom, so fall back to the abbreviation rather than dropping the country.
        for cand in (name.upper(), (abbrev or "").upper()):
            if not cand:
                continue
            w = 4.3 * len(cand)
            if view.x0 + 3 + w < x < view.x1 - 3 - w:
                label, half = cand, w
                break
        if label is None or not (view.y0 + 14 < y < view.y1 - 8):
            continue
        if any(abs(x - px) < half + pw and abs(y - py) < 20 for px, py, pw in avoid):
            continue
        avoid.append((x, y, half))
        out.append(f'<text x="{x:.0f}" y="{y:.0f}" font-size="13" text-anchor="middle" fill="#000" '
                   f'paint-order="stroke" stroke="#fff" stroke-width="3">{esc(label)}</text>')
        if len(out) >= limit:
            break
    return "".join(out)


STATUS_TR = {
    "SCHEDULED": "PLANLANDI",
    "NO POSITION": "KONUM YOK",
    "PREPARING": "HAZIRLANIYOR",
    "ON GROUND": "YERDE",
    "TAXI": "TAKSİDE",
    "CLIMB": "TIRMANIŞTA",
    "CRUISE": "DÜZ UÇUŞ",
    "DESCENT": "ALÇALIYOR",
    "NO SIGNAL": "SİNYAL YOK",
    "LANDED": "İNDİ",
    "INBOUND": "GELİYOR",
    "NOT FOUND": "BULUNAMADI",
}


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def fmt_ts(ts, tz):
    return local(ts, tz) if ts else "--:--"


AIR = ("climb", "cruise", "descent", "airborne")
FLOWN = AIR + ("taxi", "landed")
PRE = ("scheduled", "preparing")


def gc_dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def svg_leg(dt, now):
    route = dt.get("route") or {}
    o, dest = route.get("origin") or {}, route.get("destination") or {}
    last = dt.get("last_pos") or {}
    path = dt.get("path") or []
    phase = dt.get("phase") or "cruise"
    status = dt.get("status") or ""
    inbound = dt.get("inbound") or {}
    o_code = o.get("iata") or o.get("icao") or "???"
    d_code = dest.get("iata") or dest.get("icao") or "???"
    est = dt.get("est_pos") or {}
    lat = est.get("lat", last.get("lat"))
    lon = est.get("lon", last.get("lon"))
    box = (0, 72, 470, 400)
    F = 'font-family="Helvetica Neue,Helvetica,Arial,sans-serif"'
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" {F} style="background:#fff">',
             f'<rect width="{W}" height="{H}" fill="#fff"/>',
             f'<rect width="{W}" height="70" fill="#000"/>',
             f'<text x="16" y="35" font-size="44" font-weight="700" fill="#fff" dominant-baseline="central">{esc(dt.get("ident") or dt.get("callsign"))}</text>',
             '<text x="215" y="35" font-size="40" font-weight="700" fill="#fff" dominant-baseline="central">'
             + esc(o_code) + " \u2192 " + esc(d_code) + '</text>',
             f'<text x="{W - 16}" y="22" font-size="28" font-weight="700" fill="#fff" text-anchor="end" dominant-baseline="central">{esc(STATUS_TR.get(status, status))}</text>']
    sub = "\u0020\u00b7\u0020".join(x for x in [dt.get("type"), dt.get("reg"),
                                                dt.get("callsign") if phase in FLOWN else None] if x)
    parts.append(f'<text x="{W - 16}" y="52" font-size="18" fill="#fff" text-anchor="end" dominant-baseline="central">{esc(sub)}</text>')

    pts_path = [(p[1], p[2]) for p in path if p[1] is not None] if phase in FLOWN else []
    in_path = [(p[1], p[2]) for p in (dt.get("inbound_path") or []) if p[1] is not None] if phase == "inbound" else []
    in_route = (inbound.get("route") or {}) if phase == "inbound" else {}
    have_o, have_d = o.get("lat") is not None, dest.get("lat") is not None
    show_ac = lat is not None and phase != "scheduled"

    if have_o and have_d or lat is not None:
        plan = gc_line((o["lat"], o["lon"]), (dest["lat"], dest["lon"])) if have_o and have_d else []
        rem = gc_line((lat, lon), (dest["lat"], dest["lon"])) if phase in AIR and lat is not None and have_d else []
        in_rem = gc_line((lat, lon), (o["lat"], o["lon"])) if phase == "inbound" and lat is not None and have_o else []

        if phase in AIR and lat is not None:
            view = MapView(box, [], [], center=(lat, lon), width_nm=1000)
        elif phase in ("taxi", "landed") and lat is not None:
            view = MapView(box, [], [], center=(lat, lon), width_nm=400)
        else:
            lats, lons = [], []
            for a in (o, dest, in_route.get("origin") or {}, in_route.get("destination") or {}):
                if a.get("lat") is not None:
                    lats.append(a["lat"]); lons.append(a["lon"])
            if show_ac:
                lats.append(lat); lons.append(lon)
            for pt in plan + rem + in_rem:
                lats.append(pt[0]); lons.append(pt[1])
            view = MapView(box, lats, lons)

        # Land and sea were both white, leaving nothing to tell them apart. A 1-bit
        # panel has no grey to shade with, so the water carries a stipple instead:
        # paint the whole box with it, then lay the white land polygons on top.
        parts.append('<defs><pattern id="sea" width="6" height="6" patternUnits="userSpaceOnUse">'
                     '<rect width="1.5" height="1.5" fill="#000"/></pattern></defs>')
        parts.append(f'<clipPath id="m"><rect x="{box[0]}" y="{box[1]}" width="{view.pw}" height="{view.ph}"/></clipPath>'
                     f'<g clip-path="url(#m)">')
        parts.append(f'<rect x="{box[0]}" y="{box[1]}" width="{view.pw}" height="{view.ph}" fill="url(#sea)"/>')
        d = []
        for feat in world()["features"]:
            for ring in rings(feat):
                if not view.visible(ring):
                    continue
                pts = view.thinned(ring)
                if len(pts) > 2:
                    d.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
        parts.append(f'<path d="{" ".join(d)}" fill="#fff" stroke="#000" stroke-width="1"/>')
        w = []
        for feat in lakes()["features"]:
            for ring in rings(feat):
                if not view.visible(ring, margin=0.5):
                    continue
                pts = view.thinned(ring)
                if len(pts) > 2:
                    w.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
        if w:
            parts.append(f'<path d="{" ".join(w)}" fill="url(#sea)" stroke="#000" stroke-width="1"/>')
        parts.append(svg_graticule(view))

        def pl(pts, width, dash=None):
            return ('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in (view.xy(*pt) for pt in pts)) +
                    f'" fill="none" stroke="#000" stroke-width="{width}" stroke-linejoin="round"'
                    + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")

        if phase in PRE + ("taxi", "inbound") and plan:
            parts.append(pl(plan, 2, "6 6"))
        if rem:
            parts.append(pl(rem, 2, "6 6"))
        if in_rem:
            parts.append(pl(in_rem, 2, "3 5"))
        if len(pts_path) > 1:
            parts.append(pl(pts_path, 4))
        # The gap between the last measured fix and the estimate is the part nobody
        # knows. Fine dots for it, so the eye reads solid-measured, dotted-guessed,
        # dashed-still-to-fly.
        if est and last.get("lat") is not None:
            parts.append(pl(gc_line((last["lat"], last["lon"]), (lat, lon), 20), 3, "2 4"))
        if len(in_path) > 1:
            parts.append(pl(in_path, 3))

        avoid = []
        marks = [(o, o_code), (dest, d_code)]
        if in_route.get("origin", {}).get("lat") is not None:
            marks.append((in_route["origin"], in_route["origin"].get("iata") or in_route["origin"].get("icao") or ""))
        for ap, code in marks:
            if ap.get("lat") is None or not view.contains(ap["lat"], ap["lon"]):
                continue
            x, y = view.xy(ap["lat"], ap["lon"])
            avoid.append((x, y, 14))
            avoid.append((x, y + 24, 6.0 * len(code)))
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="#000"/>')
            end = x > view.pw - 60
            parts.append(f'<text x="{x + (-10 if end else 10):.1f}" y="{y + 24:.1f}" font-size="20" font-weight="700"'
                         + (' text-anchor="end"' if end else "")
                         + f' paint-order="stroke" stroke="#fff" stroke-width="3">{esc(code)}</text>')
        if show_ac:
            x, y = view.xy(lat, lon)
            avoid.append((x, y, 24))
            sz = 15
            hdg = float(last.get("track") or 0)
            if est:
                parts.append(f'<g transform="translate({x:.1f},{y:.1f}) rotate({hdg:.0f})">'
                             f'<polygon points="0,{-sz} {sz * 0.6:.1f},{sz * 0.7:.1f} 0,{sz * 0.3:.1f} {-sz * 0.6:.1f},{sz * 0.7:.1f}"'
                             f' fill="#fff" stroke="#000" stroke-width="2"/>'
                             f'<circle r="{sz + 4}" fill="none" stroke="#000" stroke-width="2" stroke-dasharray="3 3"/></g>')
            else:
                parts.append(f'<g transform="translate({x:.1f},{y:.1f}) rotate({hdg:.0f})">'
                             f'<polygon points="0,{-sz} {sz * 0.6:.1f},{sz * 0.7:.1f} 0,{sz * 0.3:.1f} {-sz * 0.6:.1f},{sz * 0.7:.1f}" fill="#000"/>'
                             f'<circle r="{sz + 4}" fill="none" stroke="#000" stroke-width="2"/></g>')
        sea = svg_sea(view, avoid)
        city = svg_cities(view, avoid)
        parts.append(svg_countries(view, avoid, 3 if city else MAX_LABELS))
        parts.append(city)
        parts.append(sea)
        parts.append('</g>')
    else:
        msg = "Rota bekleniyor" if phase == "scheduled" else "İlk konum bekleniyor"
        parts.append(f'<text x="{(box[0] + box[2]) // 2}" y="{(box[1] + box[3]) // 2}" font-size="24" '
                     f'text-anchor="middle" dominant-baseline="central">{msg}</text>')
    parts.append(f'<rect x="{box[0] + 0.5}" y="{box[1] + 0.5}" width="{box[2] - box[0] - 1}" '
                 f'height="{box[3] - box[1] - 1}" fill="none" stroke="#000"/>')

    x = 486
    off = dt.get("off_time")
    landed_at = dt.get("landed_at")
    eta = dt.get("eta") or dt.get("eta_sched")
    exp_off = dt.get("expected_off")
    block_s = dt.get("block_s")
    gs, alt, vs, trk = last.get("gs_kt"), last.get("alt_ft"), last.get("vs_fpm"), last.get("track")
    age = dt.get("pos_age_s")
    age = max(0, age) if age is not None else None
    age_txt = (f"{int(age // 60)} dk" if age >= 60 else f"{int(age)} sn") if age is not None else ""
    rem_nm = dt.get("remaining_nm")
    DASH = "\u2014"

    if phase == "landed":
        flown = dt.get("flown_s")
        if flown is None and off and landed_at:
            flown = datetime.fromisoformat(landed_at).timestamp() - off
        l1, b1 = "UÇUŞ SÜRESİ", hm(flown)
        l2, b2 = "İNİŞ SAATİ", local(landed_at, dest.get("tz"))
        frac = 1.0
        off_txt = f'KALKIŞ {local(off, o.get("tz"))} {o_code}'
        eta_txt = f'İNİŞ {local(landed_at, dest.get("tz"))} {d_code}'
        gs = alt = vs = trk = None
        rem_txt = DASH
        note = "vardı"
    elif phase in AIR:
        since = now.timestamp() - off if off else None
        togo = (datetime.fromisoformat(eta) - now).total_seconds() if eta else None
        if togo is not None and togo < 0:
            togo = 0
        total = (since or 0) + (togo or 0)
        frac = (since / total) if since and total else 0.0
        l1, b1 = "GEÇEN SÜRE", hm(since)
        l2, b2 = "KALAN SÜRE", hm(togo)
        off_txt = f'KALKIŞ {local(off, o.get("tz"))} {o_code}'
        eta_txt = f'VARIŞ {local(eta, dest.get("tz"))} {d_code}'
        rem_txt = f"{rem_nm} NM" if rem_nm is not None else DASH
        note = f"konum {age_txt}" if age_txt else ""
    else:
        to_off = (exp_off - now.timestamp()) if exp_off else None
        l1, b1 = "KALKIŞA", (hm(to_off) if to_off is not None and to_off > 0 else "--:--")
        l2, b2 = "TAH. SÜRE", hm(block_s)
        frac = 0.0
        off_txt = f'KALKIŞ ~{local(exp_off, o.get("tz"))} {o_code}' if exp_off else f'KALKIŞ --:-- {o_code}'
        arr = (exp_off + block_s) if exp_off and block_s else None
        eta_txt = f'VARIŞ ~{local(arr, dest.get("tz"))} {d_code}' if arr else f'VARIŞ --:-- {d_code}'
        if have_o and have_d and status != "NO POSITION":
            rem_txt = f'{round(gc_dist_nm(o["lat"], o["lon"], dest["lat"], dest["lon"]))} NM'
        else:
            rem_txt = DASH
        if last.get("ground"):
            vs = None
            if (gs or 0) < 5:
                trk = None
        if phase == "scheduled":
            note = "konum alınamıyor" if status == "NO POSITION" else "kalkış bekleniyor"
        elif phase == "inbound":
            note = f"gelen {esc(inbound.get('callsign') or '')}".strip()
        else:
            note = f"konum {age_txt}" if age_txt else ""

    trk_txt = "{:03d}\u00b0".format(int(trk)) if trk is not None else DASH
    vs_txt = "V/S {:+d} fpm".format(int(vs)) if vs is not None else ""
    alt_txt = "{:,} ft".format(int(alt)).replace(",", " ") if alt is not None else ""

    def lab(px, py, t):
        return f'<text x="{px}" y="{py}" font-size="16" fill="#000">{esc(t)}</text>'

    def big(px, py, t, size=34):
        return f'<text x="{px}" y="{py}" font-size="{size}" font-weight="700">{esc(t)}</text>'

    parts += [lab(x, 91, l1), big(x, 134, b1, 44), lab(x + 152, 91, l2), big(x + 152, 134, b2, 44),
              f'<rect x="{x + 1}" y="159" width="{W - 16 - x - 2}" height="14" fill="none" stroke="#000" stroke-width="2"/>',
              f'<rect x="{x}" y="158" width="{int((W - 16 - x) * max(0.0, min(1.0, frac)))}" height="16" fill="#000"/>',
              f'<text x="{x}" y="196" font-size="17">{esc(off_txt)}</text>',
              f'<text x="{W - 16}" y="196" font-size="17" text-anchor="end">{esc(eta_txt)}</text>',
              lab(x, 225, "YER HIZI"), big(x, 260, f"{int(gs)} kt" if gs is not None else DASH),
              lab(x + 152, 225, "İRTİFA" + (" geo" if last.get("alt_geo") else "")),
              big(x + 152, 260, f"FL{int(round(alt / 100)):03d}" if alt is not None else DASH),
              f'<text x="{x}" y="292" font-size="17">{esc(vs_txt)}</text>',
              f'<text x="{x + 152}" y="292" font-size="17">{esc(alt_txt)}</text>',
              lab(x, 321, "UÇUŞ BAŞI"), big(x, 356, trk_txt),
              lab(x + 152, 321, "KALAN MESAFE"), big(x + 152, 354, rem_txt, 30),
              f'<text x="{x}" y="386" font-size="17">{esc(note)}</text>',
              f'<line x1="0" y1="402" x2="{W}" y2="402" stroke="#000" stroke-width="2"/>']

    # The provenance line went: it existed to show which source the board was
    # running on while that was still in doubt, and with only one line of text left
    # down here the band reads better centred than split across two rows.
    if est:
        if est.get("anchor") == "fr24":
            left = "tahmini konum \u00b7 fr24"
        else:
            src = est.get("from_s")
            gap = ("{:.0f} dk".format(src / 60) if src and src >= 60 else "")
            left = "tahmini konum" + (" \u00b7 son sabit " + gap + " \u00f6nce" if gap else "")
    elif lat is not None:
        ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
        deg = "\u00b0"
        left = "{:.2f}{}{}  {:.2f}{}{}".format(abs(lat), deg, ns, abs(lon), deg, ew)
    elif exp_off:
        tz = ZoneInfo(o.get("tz")) if o.get("tz") else LOCAL_TZ
        when = datetime.fromtimestamp(exp_off, timezone.utc).astimezone(tz).strftime("%d.%m %H:%M")
        left = ("kalkmış olmalı, " + when + " " + o_code) if status == "NO POSITION" \
            else ("tahmini kalkış " + when + " " + o_code)
    else:
        left = "Konum yok"
    parts.append('<text x="16" y="444" font-size="21">' + esc(left) + '</text>')
    parts.append(f'<text x="{W - 16}" y="444" font-size="17" text-anchor="end">'
                 f'güncel {now.astimezone(LOCAL_TZ).strftime("%d.%m %H:%M")}</text>')
    parts.append('</svg>')
    return "".join(parts)


def svg_idle(now):
    b64 = base64.b64encode(open("board.png", "rb").read()).decode()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            f'<image href="data:image/png;base64,{b64}" width="{W}" height="{H}" style="image-rendering:pixelated"/></svg>')


# GitHub Pages serves every file with Cache-Control: max-age=600 and offers no way
# to change it, so a panel refreshing on a 5-minute cycle can sit up to ten minutes
# behind. (The <meta http-equiv="Cache-Control"> this page used to carry never did
# anything: the HTML spec defines no such pragma, so browsers ignore it.)
#
# A query string is part of the cache key, so the board is also written as its own
# file and pulled with a fresh timestamp on every load. The page still carries the
# inline SVG, which is what a renderer without JavaScript keeps showing.
REFRESH_JS = """(function(){
function pull(){try{
var x=new XMLHttpRequest();
x.open("GET","board.svg?t="+(new Date()).getTime(),true);
x.onload=function(){var t=x.responseText;
if(x.status===200&&t&&t.indexOf("<svg")===0){document.getElementById("b").innerHTML=t;}};
x.onerror=function(){};x.send();}catch(e){}}
pull();setInterval(pull,120000);})();"""


def write_page(svg):
    open("board.svg", "w", encoding="utf-8").write(svg)
    open("index.html", "w", encoding="utf-8").write(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=800"><title>board</title>'
        '<style>html,body{margin:0;padding:0;width:100%;height:100%;background:#fff;overflow:hidden}'
        'svg{display:block;width:100vw;height:auto;max-height:100vh}</style></head>'
        f'<body><div id="b">{svg}</div><script>{REFRESH_JS}</script></body></html>')


def main():
    now = datetime.now(timezone.utc)
    dt = json.load(open("data.json")) if os.path.exists("data.json") else {"mode": "idle"}
    state = json.load(open("idle.json")) if os.path.exists("idle.json") else {}
    if dt.get("mode") == "leg":
        render_leg(dt, now).save("board.png")
        write_page(svg_leg(dt, now))
    else:
        img, state = render_idle(state, now)
        img.save("board.png")
        json.dump(state, open("idle.json", "w"))
        write_page(svg_idle(now))
    print("rendered", dt.get("mode"), dt.get("status") or "")


if __name__ == "__main__":
    main()
