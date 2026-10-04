"""Season Watch daily refresh (runs on GitHub Actions).
Downloads prices (yfinance) and headlines (Google News RSS), works out cycle and
momentum status, and saves everything to Supabase.
Needs environment variables SUPABASE_URL and SUPABASE_SERVICE_KEY.
"""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]


def sb(method, path, body=None, prefer=None):
    """Tiny Supabase REST helper (service key bypasses row security)."""
    req = urllib.request.Request(f"{URL}/rest/v1/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("apikey", KEY)
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else None


def upsert(table, rows, on=None):
    if rows:
        sb("POST", table + (f"?on_conflict={on}" if on else ""), rows, "resolution=merge-duplicates,return=minimal")


# ---------------- Seasonal cycles (edit freely) ----------------
# start/end are month-day; the app works out the status from today's date.
CYCLES = [
    {"id": "holiday", "name": "Holiday shopping", "start": "10-15", "end": "12-24",
     "peak": "Usually late Nov to early Dec (around Black Friday)",
     "why": "The biggest shopping weeks of the year. Retail stocks often rise into Black Friday and Christmas.",
     "basket": [
         {"tk": "TGT", "name": "Target", "cyc": 5, "note": "Swings the most in this group."},
         {"tk": "AMZN", "name": "Amazon", "cyc": 4, "note": "Prime sales in October start its season."},
         {"tk": "WMT", "name": "Walmart", "cyc": 2, "note": "Steadiest, smaller moves either way."}]},
    {"id": "yearend", "name": "Year-end rally", "start": "12-15", "end": "01-05",
     "peak": "Usually the first days of January",
     "why": "The last two weeks of the year and first days of January have more often been up than down.",
     "basket": [
         {"tk": "QQQ", "name": "Top 100 US tech", "cyc": 4, "note": "Bigger moves than VOO."},
         {"tk": "VOO", "name": "Top 500 US companies", "cyc": 3, "note": "Steadier version."}]},
    {"id": "january", "name": "January small-company bounce", "start": "12-20", "end": "01-31",
     "peak": "Usually mid to late January",
     "why": "Small stocks sold for tax reasons in December often bounce in January.",
     "basket": [{"tk": "IWM", "name": "2,000 smaller US companies", "cyc": 4, "note": "Swings more than VOO."}]},
    {"id": "planting", "name": "Spring planting", "start": "02-01", "end": "04-30",
     "peak": "Usually April",
     "why": "US farmers buy fertilizer and machines before planting.",
     "basket": [
         {"tk": "CF", "name": "CF Industries (fertilizer)", "cyc": 5, "note": "Fertilizer prices swing hard."},
         {"tk": "DE", "name": "Deere (farm machines)", "cyc": 3, "note": "Steadier."}]},
    {"id": "driving", "name": "Summer driving (fuel)", "start": "02-15", "end": "05-31",
     "peak": "Usually May",
     "why": "Petrol demand rises into summer road trips; refiners often run up in spring.",
     "basket": [
         {"tk": "VLO", "name": "Valero (refiner)", "cyc": 5, "note": "Refiners move the most."},
         {"tk": "XLE", "name": "Energy fund", "cyc": 3, "note": "Steadier, follows oil."}]},
    {"id": "travel", "name": "Summer travel", "start": "03-01", "end": "06-15",
     "peak": "Usually May to early June",
     "why": "People book summer holidays in spring; travel companies give upbeat forecasts in April.",
     "basket": [
         {"tk": "DAL", "name": "Delta Air Lines", "cyc": 5, "note": "Airlines swing with fuel prices."},
         {"tk": "BKNG", "name": "Booking.com", "cyc": 3, "note": "Steadier."}]},
    {"id": "school", "name": "Back to school", "start": "07-01", "end": "08-31",
     "peak": "Usually mid August",
     "why": "Second-biggest US shopping season: clothes, supplies, laptops.",
     "basket": [
         {"tk": "BBY", "name": "Best Buy", "cyc": 4, "note": "Laptops and electronics."},
         {"tk": "TGT", "name": "Target", "cyc": 4, "note": "Clothes and supplies."}]},
    {"id": "gold", "name": "Gold festival season", "start": "09-15", "end": "11-15",
     "peak": "Usually just before Diwali",
     "why": "Indian and Chinese gold buying rises for Diwali and weddings.",
     "basket": [
         {"tk": "NEM", "name": "Newmont (gold miner)", "cyc": 4, "note": "Miners swing more than gold."},
         {"tk": "GLD", "name": "Gold fund", "cyc": 3, "note": "Follows the gold price."}]},
]

# Candidates checked every day for momentum (keep it short).
MOMENTUM_CANDIDATES = {
    "NVDA": "Nvidia", "QQQ": "Top 100 US tech", "FCX": "Freeport-McMoRan (copper)",
    "AMZN": "Amazon", "MSFT": "Microsoft", "META": "Meta", "AVGO": "Broadcom", "JPM": "JPMorgan",
}
METALS = {"GLD": "Gold", "CPER": "Copper"}
FUNDS = {"QQQ", "VOO", "SPY", "IWM", "XLE", "GLD", "CPER", "SGOV", "ITB", "COPX"}
SHOW_MOMENTUM = 3  # how many momentum stocks to show


# ---------------- Calendar logic ----------------
def window(cycle, today):
    """Return the current-or-next (start, end) dates for a month-day window."""
    sm, sd = map(int, cycle["start"].split("-"))
    em, ed = map(int, cycle["end"].split("-"))
    for y in (today.year - 1, today.year, today.year + 1):
        s = dt.date(y, sm, sd)
        e = dt.date(y if (em, ed) >= (sm, sd) else y + 1, em, ed)
        if e >= today:
            return s, e
    return None


def cycle_status(s, e, today):
    if today < s:
        return "coming" if (s - today).days <= 60 else "later"
    length = (e - s).days or 1
    done = (today - s).days / length
    if done < 0.35:
        return "early"
    if done < 0.75:
        return "mid"
    return "peak"


def fmt(d):
    return d.strftime("%d %b").lstrip("0")


# ---------------- Data fetching ----------------
def fetch_history(tickers):
    """Return {ticker: pandas.DataFrame} with ~1 year of daily data."""
    import yfinance as yf
    out = {}
    for tk in sorted(set(tickers)):
        try:
            h = yf.Ticker(tk).history(period="1y", auto_adjust=False)
            if h is not None and len(h) > 20:
                out[tk] = h
        except Exception as exc:  # network hiccup or bad ticker
            print(f"  price failed for {tk}: {exc}")
    return out


def next_earnings(tk):
    if tk in FUNDS:
        return None
    try:
        import yfinance as yf
        cal = yf.Ticker(tk).calendar
        d = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if d:
            d = d[0] if isinstance(d, (list, tuple)) else d
            return d.isoformat() if hasattr(d, "isoformat") else str(d)
    except Exception:
        pass
    return None


def fetch_news(tk, name, limit=2):
    q = urllib.parse.quote(f"{tk} {name} stock")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(urllib.request.urlopen(req, timeout=15).read())
    except Exception as exc:
        print(f"  news failed for {tk}: {exc}")
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        pub = it.findtext("pubDate") or ""
        try:
            day = dt.datetime.strptime(pub[:16], "%a, %d %b %Y").date().isoformat()
        except ValueError:
            day = dt.date.today().isoformat()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        items.append((tk, day, title, src))
        if len(items) >= limit:
            break
    return items


def stats_from(h):
    close = h["Close"]
    price = float(close.iloc[-1])
    hi52 = float(h["High"].max())
    low3m = float(close.iloc[-63:].min())
    ma50 = float(close.iloc[-50:].mean())
    daily = float((close.pct_change().abs().iloc[-20:].mean()) * 100)
    return {
        "price": round(price, 2), "day": h.index[-1].date().isoformat(),
        "hi52": round(hi52, 2), "low3m": round(low3m, 2), "ma50": round(ma50, 2),
        "offHigh": round((hi52 - price) / hi52 * 100, 1),
        "fromLow": round((price - low3m) / low3m * 100, 1),
        "ret3m": round((price / float(close.iloc[-63]) - 1) * 100, 1),
        "dailyMove": round(daily, 2),
    }


def momentum_view(tk, name, s):
    """Rules-based momentum stage and rough room estimate."""
    typical_leg = 12 if tk in FUNDS else 30  # % size of a typical rally leg
    done = max(5, min(95, round(s["fromLow"] / typical_leg * 100)))
    if s["offHigh"] > 8 and s["price"] < s["ma50"]:
        stage, warn = "cooling", "Momentum has stalled below its recent high. Don't buy now."
    elif done >= 70:
        stage, warn = "late", "Late in the move. Only buy on a dip, not at the high."
    else:
        stage, warn = "steady", ""
    room = max(0, typical_leg - s["fromLow"])
    return {
        "tk": tk, "name": name, "stage": stage, "done": done,
        "room": (f"About +{room:.0f}% more is possible if the move is typical" if room >= 2 and stage != "cooling"
                 else "Little room left based on a typical move"),
        "roomTo": f"about ${s['price'] * (1 + room / 100):,.0f}" if room >= 2 and stage != "cooling" else "",
        "frame": "next 3–6 weeks" if tk not in FUNDS else "next 1–3 months",
        "entry": None if stage == "cooling" else [round(s["price"] * 0.95), round(s["price"] * 0.96)],
        "target": "+6% from your buy",
        "why": (f"Up {s['fromLow']:.0f}% from its 3-month low, {s['offHigh']:.0f}% below its 1-year high, "
                f"{'above' if s['price'] > s['ma50'] else 'below'} its 50-day average."),
        "warn": warn,
    }


def refresh(log=print):
    today = dt.date.today()
    watch = [r["tk"] for r in sb("GET", "watch?select=tk") or []]
    held = [r["tk"] for r in sb("GET", "buys?select=tk&sold=eq.false") or []]
    basket = [b["tk"] for c in CYCLES for b in c["basket"]]
    tickers = set(basket) | set(MOMENTUM_CANDIDATES) | set(METALS) | set(watch) | set(held)

    log(f"Fetching prices for {len(tickers)} tickers...")
    hist = fetch_history(tickers)
    stats = {tk: stats_from(h) for tk, h in hist.items()}
    upsert("prices", [{"tk": tk, "price": s["price"], "day": s["day"], "stats": s} for tk, s in stats.items()], "tk")

    cycles = []
    for c in CYCLES:
        w = window(c, today)
        if not w:
            continue
        s, e = w
        st = cycle_status(s, e, today)
        if st == "later":
            continue
        buy = ("Now, only on a 4–5% dip" if st == "early" else
               "Only on a dip; it's already under way" if st == "mid" else
               "Too late this time; wait for next year" if st == "peak" else
               f"From about {fmt(s - dt.timedelta(days=20))}, on dips")
        items = []
        for b in c["basket"]:
            p = stats.get(b["tk"], {}).get("price")
            ed = next_earnings(b["tk"])
            items.append(dict(b, entry=[round(p * 0.95), round(p * 0.96)] if p and st in ("early", "mid", "coming") else None,
                              results=(f"Next results about {ed}. Price can move 5–10% that day." if ed else
                                       "Fund: no results day." if b["tk"] in FUNDS else "Check the results date before buying.")))
        cycles.append({"id": c["id"], "name": c["name"], "now": s <= today, "status": st,
                       "period": f"{fmt(s)} – {fmt(e)}", "peak": c["peak"], "buy": buy,
                       "sellBy": f"By about {fmt(e - dt.timedelta(days=7))}", "why": c["why"], "basket": items,
                       "startIso": s.isoformat()})
    cycles.sort(key=lambda c: (not c["now"], c["startIso"]))

    cands = [(tk, n) for tk, n in MOMENTUM_CANDIDATES.items() if tk in stats]
    momentum = []
    for tk, n in sorted(cands, key=lambda x: stats[x[0]]["ret3m"], reverse=True):
        s = stats[tk]
        if s["ret3m"] > 5 and s["price"] > s["ma50"] * 0.97:
            v = momentum_view(tk, n, s)
            ed = next_earnings(tk)
            v["results"] = f"Next results about {ed}." if ed else ("Fund: no results day." if tk in FUNDS else "Check the results date.")
            momentum.append(v)
        if len(momentum) >= SHOW_MOMENTUM:
            break

    metals = []
    for tk, n in METALS.items():
        s = stats.get(tk)
        if not s:
            continue
        if s["ret3m"] > 8 and s["offHigh"] < 5:
            status = "In momentum"
        elif s["ret3m"] > 0 and s["offHigh"] < 10:
            status = "Mild uptrend"
        elif s["offHigh"] > 8 and s["price"] < s["ma50"]:
            status = "Cooling"
        else:
            status = "No momentum now"
        metals.append({"name": n, "status": status,
                       "detail": f"{tk}: {s['ret3m']:+.0f}% in 3 months, {s['offHigh']:.0f}% below its 1-year high."})

    names = {b["tk"]: b["name"] for c in cycles if c["now"] for b in c["basket"]}
    names.update({m["tk"]: m["name"] for m in momentum})
    names.update({tk: tk for tk in watch + held})
    log(f"Fetching news for {len(names)} tickers...")
    rows = []
    for tk, n in names.items():
        rows += [{"tk": a, "day": b, "title": t, "source": src} for a, b, t, src in fetch_news(tk, n)]
    upsert("news", rows, "tk,title")
    sb("DELETE", f"news?day=lt.{(today - dt.timedelta(days=14)).isoformat()}")

    last_day = max((s["day"] for s in stats.values()), default=today.isoformat())
    now_ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
    snapshot = {"asOf": last_day, "asOfLabel": f"{last_day} close (updated {now_ist:%d %b %H:%M} IST)",
                "cycles": cycles, "momentum": momentum, "metals": metals}
    upsert("kv", [{"k": "snapshot", "v": snapshot, "updated_at": dt.datetime.utcnow().isoformat() + "Z"}], "k")
    log("Refresh done.")


if __name__ == "__main__":
    refresh()
