#!/usr/bin/env python3
"""Watch Cinema City Praha Flora (IMAX VOLVO) for Dune: Part Three seats.

Pushes a phone notification (ntfy.sh) when:
  * new IMAX screenings appear in the schedule (with the time sales open),
  * sales for a screening open,
  * free seats on a screening go UP by at least MIN_SEATS (someone cancelled /
    an abandoned basket expired).

It only reads the public schedule. It never locks seats or pays: the booking
site guards seat locking with reCAPTCHA, and paying is for you to do.
Pure Python 3.9+, no dependencies.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Prague")
API = "https://www.cinemacity.cz/cz/data-api-service/v1/quickbook/10101"
TICKETS = "https://tickets.cinemacity.cz/api"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

CINEMA_ID = os.environ.get("CINEMA_ID", "1052")            # Praha Flora
FILM = os.environ.get("FILM_PATTERN", "dun").lower()
HALL = os.environ.get("HALL_PATTERN", "imax").lower()
MIN_SEATS = int(os.environ.get("MIN_SEATS", "2"))          # how many seats you need together
FROM_DATE = os.environ.get("FROM_DATE", "2026-12-14")      # ignore screenings before this
TO_DATE = os.environ.get("TO_DATE", "")                    # optional upper bound, YYYY-MM-DD
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
DAYS_CZ = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def now():
    return datetime.now(TZ)


def get_json(url, method="GET"):
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"}, method=method)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed: {url}: {last}")


def in_window(day):
    return day >= FROM_DATE and (not TO_DATE or day <= TO_DATE)


def fetch_screenings():
    until = (date.today() + timedelta(days=200)).isoformat()
    days = get_json(f"{API}/dates/in-cinema/{CINEMA_ID}/until/{until}?attr=&lang=cs_CZ")["body"]["dates"]
    found = {}
    for day in days:
        if not in_window(day):
            continue
        body = get_json(f"{API}/film-events/in-cinema/{CINEMA_ID}/at-date/{day}?attr=&lang=cs_CZ")["body"]
        films = {f["id"]: f.get("name", "") for f in body.get("films", [])}
        for e in body.get("events", []):
            if FILM not in films.get(e["filmId"], "").lower():
                continue
            if HALL not in (e.get("auditorium") or "").lower():
                continue
            code = str(e.get("presentationCode") or e["id"])
            found[code] = {
                "code": code,
                "when": e["eventDateTime"],
                "hall": e.get("auditorium"),
                "ratio": e.get("availabilityRatio"),
                "soldOut": bool(e.get("soldOut")),
                "url": f"https://tickets.cinemacity.cz/order/{code}",
            }
        time.sleep(0.3)
    return found


_capacity = {}


def enrich(s, known):
    """Add capacity, free seat estimate and sale start (cached from state)."""
    for k in ("capacity", "saleStart"):
        if known and known.get(k):
            s[k] = known[k]
    if not s.get("capacity") or not s.get("saleStart"):
        p = get_json(f"{TICKETS}/presentations/{s['code']}?referralMiniSiteId=0")["presentation"]
        s["saleStart"] = p.get("ticketSaleStart")
        key = (p["venueId"], p["seatplanId"])
        if key not in _capacity:
            plan = get_json(f"{TICKETS}/seats/seatplanV2?venueId={key[0]}&seatplanId={key[1]}", method="POST")
            _capacity[key] = sum(len(r.get("S", {})) for sec in plan.get("S", {}).values()
                                 for g in sec.get("G", {}).values() for r in g.get("R", {}).values())
        s["capacity"] = _capacity[key]
    s["free"] = 0 if s["soldOut"] else round((s["ratio"] or 0) * s["capacity"])


def fmt(iso):
    d = datetime.fromisoformat(iso.replace(" ", "T"))
    return f"{DAYS_CZ[d.weekday()]} {d.day}.{d.month}. {d:%H:%M}"


def sale_open(s):
    st = s.get("saleStart")
    return not st or datetime.fromisoformat(st.replace(" ", "T")).replace(tzinfo=TZ) <= now()


def notify(title, lines, click=None, priority="high", tags="movie_camera"):
    msg = "\n".join(lines)
    print(f"\n### {title}\n{msg}\n", flush=True)
    if not NTFY_TOPIC:
        return
    headers = {"Title": title.encode("utf-8"), "Priority": priority, "Tags": tags}
    if click:
        headers["Click"] = click
        headers["Actions"] = f"view, Open booking, {click}, clear=true"
    req = urllib.request.Request(f"{NTFY_SERVER}/{NTFY_TOPIC}", data=msg.encode("utf-8"), headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as exc:  # never let a notification failure kill the watcher
        print(f"ntfy failed: {exc}", file=sys.stderr)


def check(state, first_run=False, report_all=False):
    current = fetch_screenings()
    known = state.setdefault("screenings", {})
    new, freed, opened = [], [], []
    for code, s in current.items():
        old = known.get(code)
        enrich(s, old)
        is_open = sale_open(s)
        # "floor" = fewest free seats ever seen once sales run = the unsellable
        # front-row leftovers. Only seats above the floor are worth a push.
        floor = (old or {}).get("floor", (old or {}).get("free", s["free"]))
        s["floor"] = min(floor, s["free"]) if is_open else s["free"]
        s["good"] = s["free"] - s["floor"]
        if old is None:
            new.append(s)
        else:
            old_good = old.get("good", 0)
            if is_open and s["good"] >= MIN_SEATS and s["good"] > old_good:
                freed.append((s, s["good"]))
            if is_open and not old.get("notifiedOpen"):
                opened.append(s)
        s["notifiedOpen"] = is_open
    state["screenings"] = current
    state["lastCheck"] = now().isoformat(timespec="seconds")

    if first_run and not report_all:
        print(f"Seeded state with {len(current)} screenings; alerts start from the next check.")
        return current
    if report_all:
        new = list(current.values())

    if new:
        new.sort(key=lambda s: s["when"])
        lines = [f"{fmt(s['when'])} · ~{s['free']} free · sale {'OPEN' if sale_open(s) else 'opens ' + fmt(s['saleStart'])}"
                 for s in new]
        pending = [s for s in new if not sale_open(s)]
        if pending:
            lines.append(f"\nBe at the booking page at {fmt(min(s['saleStart'] for s in pending))}.")
        notify(f"Dune IMAX: {len(new)} new screening(s)", lines, click=new[0]["url"], priority="urgent", tags="rotating_light")
    for s in opened:
        notify("Dune IMAX: sales just opened", [f"{fmt(s['when'])} · ~{s['free']} free"], click=s["url"], priority="urgent", tags="rotating_light")
    for s, good in sorted(freed, key=lambda x: x[0]["when"]):
        notify(f"Dune IMAX: {good} non-front-row seats free",
               [f"{fmt(s['when'])} · {good} seats above the usual ~{s['floor']} front-row leftovers",
                "Freed by a cancellation; usually gone within minutes."],
               click=s["url"], priority="high", tags="tickets")
    return current


def load(path):
    try:
        with open(path) as f:
            return json.load(f), False
    except FileNotFoundError:
        return {}, True


def save(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="state/state.json")
    ap.add_argument("--loop", type=int, default=0, help="seconds between checks (0 = run once)")
    ap.add_argument("--duration", type=int, default=0, help="stop looping after N seconds")
    ap.add_argument("--report-all", action="store_true", help="notify about every current screening")
    ap.add_argument("--test-notify", action="store_true", help="send one test push and exit")
    a = ap.parse_args()

    if a.test_notify:
        notify("Dune IMAX watcher is live", ["Test push. Tap to open the Flora booking site."],
               click="https://www.cinemacity.cz/cinemas/flora/1052", priority="default", tags="white_check_mark")
        return

    state, first = load(a.state)
    deadline = time.time() + a.duration if a.duration else None
    while True:
        try:
            cur = check(state, first_run=first, report_all=a.report_all)
            save(a.state, state)
            first, a.report_all = False, False
            print(f"{now():%Y-%m-%d %H:%M:%S} ok: {len(cur)} screenings, "
                  f"free seats: {sum(s['free'] for s in cur.values())}", flush=True)
        except Exception as exc:
            print(f"{now():%H:%M:%S} check failed: {exc}", file=sys.stderr, flush=True)
            if not a.loop:
                raise
        if not a.loop or (deadline and time.time() + a.loop > deadline):
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
