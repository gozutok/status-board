#!/usr/bin/env python3
"""Query the Flightradar24 API from this machine, to work things out in seconds.

Everything about these endpoints was being settled by pushing a change and reading
a workflow log six minutes later, because the token lives in a repository secret.
This reads it locally instead.

The token is taken from the macOS keychain, or from ~/.config/fr24/token, or from
FR24_TOKEN. It is never printed, never written anywhere, and goes nowhere but
fr24api.flightradar24.com.

    python3 fr24.py live registrations=TC-JJO
    python3 fr24.py live flights=TK25 data_sources=ESTIMATED
    python3 fr24.py summary flights=TK25 --days 2
    python3 fr24.py tracks flight_id=419880e3
    python3 fr24.py sources TC-JJO          # one aircraft asked three ways
    python3 fr24.py record registrations=TC-JJO   # save a real row as a fixture
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://fr24api.flightradar24.com/api"
PATHS = {"live": "/live/flight-positions/full", "light": "/live/flight-positions/light",
         "summary": "/flight-summary/light", "tracks": "/flight-tracks"}


def token(sandbox=False):
    name = "fr24-sandbox-token" if sandbox else "fr24-api-token"
    env = os.environ.get("FR24_SANDBOX_TOKEN" if sandbox else "FR24_TOKEN")
    if env:
        return env.strip()
    try:
        got = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER", ""),
                              "-s", name, "-w"], capture_output=True, text=True, timeout=10)
        if got.returncode == 0 and got.stdout.strip():
            return got.stdout.strip()
    except Exception:
        pass
    path = os.path.expanduser("~/.config/fr24/token")
    if os.path.exists(path):
        return open(path, encoding="utf-8").read().strip()
    sys.exit("no token: keychain item 'fr24-api-token', ~/.config/fr24/token, or $FR24_TOKEN")


def call(path, params, sandbox=False):
    r = requests.get(BASE + path, params=params, timeout=30, headers={
        "Accept": "application/json", "Accept-Version": "v1",
        "Authorization": "Bearer " + token(sandbox)})
    body = r.text
    try:
        body = json.loads(body)
    except Exception:
        pass
    return r.status_code, body


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd, rest = argv[1], argv[2:]
    sandbox = "--sandbox" in rest
    rest = [a for a in rest if a != "--sandbox"]

    if cmd == "sources":
        who = rest[0] if rest else sys.exit("usage: fr24.py sources <registration|flight>")
        key = "registrations" if "-" in who else "flights"
        for label, srcs in (("default", None), ("ADSB,MLAT", "ADSB,MLAT"), ("ESTIMATED", "ESTIMATED")):
            p = {key: who, "limit": 1}
            if srcs:
                p["data_sources"] = srcs
            code, body = call(PATHS["live"], p, sandbox)
            rows = body.get("data", []) if isinstance(body, dict) else []
            src = rows[0].get("source") if rows else None
            print(f"  {label:12} HTTP {code}  rows={len(rows)}  source={src}")
        return 0

    if cmd == "record":
        params = dict(a.split("=", 1) for a in rest if "=" in a)
        params.setdefault("limit", "1")
        code, body = call(PATHS["live"], params, sandbox)
        rows = body.get("data", []) if isinstance(body, dict) else []
        if code != 200 or not rows:
            print(f"HTTP {code}: {body}")
            return 1
        os.makedirs("fixtures", exist_ok=True)
        with open("fixtures/live_position.json", "w", encoding="utf-8") as fh:
            json.dump(rows[0], fh, indent=1, sort_keys=True)
        print("wrote fixtures/live_position.json —", ", ".join(sorted(rows[0])))
        return 0

    if cmd not in PATHS:
        sys.exit(f"unknown command {cmd!r}; one of: {', '.join(PATHS)}, sources, record")
    params = dict(a.split("=", 1) for a in rest if "=" in a)
    if cmd == "summary" and "flight_datetime_from" not in params:
        days = 2
        if "--days" in argv:
            days = int(argv[argv.index("--days") + 1])
        now = datetime.now(timezone.utc)
        params["flight_datetime_from"] = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        params["flight_datetime_to"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    params.setdefault("limit", "5")
    code, body = call(PATHS[cmd], params, sandbox)
    print(f"HTTP {code}")
    print(json.dumps(body, indent=1, sort_keys=True)[:4000])
    return 0 if code == 200 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
