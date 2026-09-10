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
WORLD_GEOJSON = "https://raw.githubusercontent.com/johan/world.geo.json/master/countries.geo.json"
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


def world():
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "world.geo.json")
    if not os.path.exists(p):
        r = requests.get(WORLD_GEOJSON, timeout=60)
        r.raise_for_status()
        open(p, "wb").write(r.content)
    return json.load(open(p))


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
    def __init__(self, box, lats, lons):
        self.x0, self.y0, self.x1, self.y1 = box
        self.pw, self.ph = self.x1 - self.x0, self.y1 - self.y0
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


def draw_world(d, view, geo):
    for feat in geo["features"]:
        g = feat["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            for ring in poly:
                pts = [view.xy(lat, lon) for lon, lat in ring]
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
    togo = (datetime.fromisoformat(eta) - now).total_seconds() if eta and status != "ARRIVED" else None
    d.text((x, 78), "SINCE OFF", font=font(16), fill=0)
    d.text((x, 94), hm(since), font=font(44, True), fill=0)
    d.text((x + 152, 78), "TO GO est", font=font(16), fill=0)
    d.text((x + 152, 94), hm(togo), font=font(44, True), fill=0)
    total = (since or 0) + (togo or 0)
    frac = 1.0 if status == "ARRIVED" else (since / total if since and total else 0.0)
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
        age = dt.get("pos_age_s")
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


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def svg_leg(dt, now):
    route = dt.get("route") or {}
    o, dest = route.get("origin") or {}, route.get("destination") or {}
    last = dt.get("last_pos") or {}
    path = dt.get("path") or []
    status = dt.get("status") or ""
    o_code, d_code = o.get("iata") or o.get("icao") or "???", dest.get("iata") or dest.get("icao") or "???"
    lat, lon = last.get("lat"), last.get("lon")
    box = (0, 72, 470, 400)
    F = 'font-family="Helvetica Neue,Helvetica,Arial,sans-serif"'
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" {F} style="background:#fff">',
             f'<rect width="{W}" height="{H}" fill="#fff"/>',
             f'<rect width="{W}" height="70" fill="#000"/>',
             f'<text x="16" y="35" font-size="44" font-weight="700" fill="#fff" dominant-baseline="central">{esc(dt.get("ident") or dt.get("callsign"))}</text>',
             f'<text x="215" y="35" font-size="40" font-weight="700" fill="#fff" dominant-baseline="central">{esc(o_code)} \u2192 {esc(d_code)}</text>',
             f'<text x="{W - 16}" y="22" font-size="28" font-weight="700" fill="#fff" text-anchor="end" dominant-baseline="central">{esc(status)}</text>']
    sub = " \u00b7 ".join(x for x in [dt.get("type"), dt.get("reg"), dt.get("callsign")] if x)
    parts.append(f'<text x="{W - 16}" y="52" font-size="18" fill="#fff" text-anchor="end" dominant-baseline="central">{esc(sub)}</text>')

    have_dest = dest.get("lat") is not None
    pts_path = [(p[1], p[2]) for p in path if p[1] is not None]
    if lat is not None or pts_path or (o.get("lat") is not None and have_dest):
        lats = [p[0] for p in pts_path] + ([lat] if lat is not None else []) + [x["lat"] for x in (o, dest) if x.get("lat") is not None]
        lons = [p[1] for p in pts_path] + ([lon] if lon is not None else []) + [x["lon"] for x in (o, dest) if x.get("lon") is not None]
        rem = gc_line((lat, lon), (dest["lat"], dest["lon"])) if lat is not None and have_dest else []
        lats += [p[0] for p in rem]
        lons += [p[1] for p in rem]
        view = MapView(box, lats, lons)
        parts.append(f'<clipPath id="m"><rect x="{box[0]}" y="{box[1]}" width="{view.pw}" height="{view.ph}"/></clipPath><g clip-path="url(#m)">')
        d = []
        for feat in world()["features"]:
            g = feat["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            for poly in polys:
                for ring in poly:
                    pts = [view.xy(la, lo) for lo, la in ring]
                    if len(pts) > 2:
                        d.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
        parts.append(f'<path d="{" ".join(d)}" fill="#fff" stroke="#000" stroke-width="1"/>')
        if rem:
            parts.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in (view.xy(*p) for p in rem)) + '" fill="none" stroke="#000" stroke-width="2" stroke-dasharray="6 6"/>')
        if len(pts_path) > 1:
            parts.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in (view.xy(*p) for p in pts_path)) + '" fill="none" stroke="#000" stroke-width="4" stroke-linejoin="round"/>')
        for ap, code in ((o, o_code), (dest, d_code)):
            if ap.get("lat") is None:
                continue
            x, y = view.xy(ap["lat"], ap["lon"])
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="#000"/>')
            if x > view.pw - 60:
                parts.append(f'<text x="{x - 10:.1f}" y="{y + 24:.1f}" font-size="20" font-weight="700" text-anchor="end">{esc(code)}</text>')
            else:
                parts.append(f'<text x="{x + 10:.1f}" y="{y + 24:.1f}" font-size="20" font-weight="700">{esc(code)}</text>')
        if lat is not None:
            x, y = view.xy(lat, lon)
            s = 15
            parts.append(f'<g transform="translate({x:.1f},{y:.1f}) rotate({float(last.get("track") or 0):.0f})">'
                         f'<polygon points="0,{-s} {s * 0.6:.1f},{s * 0.7:.1f} 0,{s * 0.3:.1f} {-s * 0.6:.1f},{s * 0.7:.1f}" fill="#000"/>'
                         f'<circle r="{s + 4}" fill="none" stroke="#000" stroke-width="2"/></g>')
        parts.append('</g>')
    else:
        parts.append(f'<text x="{(box[0] + box[2]) // 2}" y="{(box[1] + box[3]) // 2}" font-size="24" text-anchor="middle" dominant-baseline="central">Waiting for first position</text>')
    parts.append(f'<rect x="{box[0] + 0.5}" y="{box[1] + 0.5}" width="{box[2] - box[0] - 1}" height="{box[3] - box[1] - 1}" fill="none" stroke="#000"/>')

    x = 486
    off = dt.get("off_time")
    since = now.timestamp() - off if off else None
    eta = dt.get("eta")
    togo = (datetime.fromisoformat(eta) - now).total_seconds() if eta and status != "ARRIVED" else None
    total = (since or 0) + (togo or 0)
    frac = 1.0 if status == "ARRIVED" else (since / total if since and total else 0.0)
    gs, alt, vs, trk = last.get("gs_kt"), last.get("alt_ft"), last.get("vs_fpm"), last.get("track")
    age = dt.get("pos_age_s")
    age_txt = (f"{int(age // 60)} min" if age >= 60 else f"{int(age)} s") if age is not None else ""

    def lab(px, py, t):
        return f'<text x="{px}" y="{py}" font-size="16" fill="#000">{esc(t)}</text>'

    def big(px, py, t, size=34):
        return f'<text x="{px}" y="{py}" font-size="{size}" font-weight="700">{esc(t)}</text>'

    parts += [lab(x, 91, "SINCE OFF"), big(x, 134, hm(since), 44), lab(x + 152, 91, "TO GO est"), big(x + 152, 134, hm(togo), 44),
              f'<rect x="{x + 1}" y="159" width="{W - 16 - x - 2}" height="14" fill="none" stroke="#000" stroke-width="2"/>',
              f'<rect x="{x}" y="158" width="{int((W - 16 - x) * frac)}" height="16" fill="#000"/>',
              f'<text x="{x}" y="196" font-size="17">OFF {esc(local(off, o.get("tz")))} {esc(o_code)}</text>',
              f'<text x="{W - 16}" y="196" font-size="17" text-anchor="end">ETA {esc(local(eta, dest.get("tz")))} {esc(d_code)}</text>',
              lab(x, 225, "GS"), big(x, 260, f"{int(gs)} kt" if gs is not None else "\u2014"),
              lab(x + 152, 225, "ALT" + (" geo" if last.get("alt_geo") else "")), big(x + 152, 260, f"FL{int(round(alt / 100)):03d}" if alt is not None else "\u2014"),
              f'<text x="{x}" y="292" font-size="17">{esc(f"V/S {int(vs):+d} fpm" if vs is not None else "V/S \u2014")}</text>',
              f'<text x="{x + 152}" y="292" font-size="17">{esc(f"{int(alt):,} ft".replace(",", " ") if alt is not None else "")}</text>',
              lab(x, 321, "TRK"), big(x, 356, f"{int(trk):03d}\u00b0" if trk is not None else "\u2014"),
              lab(x + 152, 321, "REMAINING"), big(x + 152, 354, f"{dt['remaining_nm']} NM" if dt.get("remaining_nm") is not None else "\u2014", 30),
              f'<text x="{x}" y="386" font-size="17">{esc("pos age " + age_txt if age_txt else "")}</text>',
              f'<line x1="0" y1="402" x2="{W}" y2="402" stroke="#000" stroke-width="2"/>']
    if lat is not None:
        src = (dt.get("providers") or {}).get("position") or ""
        ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
        parts.append(f'<text x="16" y="430" font-size="21">{esc(f"{abs(lat):.2f}\u00b0{ns} {abs(lon):.2f}\u00b0{ew}   {src} {age_txt}")}</text>')
    else:
        parts.append('<text x="16" y="430" font-size="21">No position yet</text>')
    pv = dt.get("providers") or {}
    foot = f"path: {pv.get('path', '\u2014')} {len(path)} pts \u00b7 route: {pv.get('route', '\u2014')} \u00b7 id: {pv.get('identify', '\u2014')}"
    parts.append(f'<text x="16" y="461" font-size="16">{esc(foot)}</text>')
    parts.append(f'<text x="{W - 16}" y="461" font-size="16" text-anchor="end">upd {now.astimezone(LOCAL_TZ).strftime("%d.%m %H:%M")}</text>')
    parts.append('</svg>')
    return "".join(parts)


def svg_idle(now):
    b64 = base64.b64encode(open("board.png", "rb").read()).decode()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            f'<image href="data:image/png;base64,{b64}" width="{W}" height="{H}" style="image-rendering:pixelated"/></svg>')


def write_page(svg):
    open("index.html", "w").write(
        '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Cache-Control" content="no-store">'
        '<meta name="viewport" content="width=800"><title>board</title>'
        '<style>html,body{margin:0;padding:0;width:100%;height:100%;background:#fff;overflow:hidden}'
        'svg{display:block;width:100vw;height:auto;max-height:100vh}</style></head>'
        f'<body>{svg}</body></html>')


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


if __name__ == "__main__":
    main()
