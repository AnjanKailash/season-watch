"""Season Watch refresh (runs on GitHub Actions).

MODE=full   (morning run): 10 years of prices -> cycle patterns, strength ranking,
                           stop-loss/targets, momentum, news, results dates.
MODE=prices (every 10 min in US hours): latest prices only.

Needs environment variables SUPABASE_URL and SUPABASE_SERVICE_KEY.
"""
import datetime as dt
import json
import os
import statistics
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

URL = os.environ["SUPABASE_URL"].strip().rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"].strip()
MODE = os.environ.get("MODE", "full").strip() or "full"
TODAY = dt.date.today()

# ----------------------------------------------------------------------------
# Cycles. start/end = month-day. group: calendar | industry
# ----------------------------------------------------------------------------
CYCLES = [
    # Calendar patterns (whole market)
    {"id": "best6", "group": "calendar", "name": "Best six months (\"Sell in May\")", "start": "11-01", "end": "04-30",
     "peak": "Usually March to April",
     "why": "November to April has historically been stronger than May to October.",
     "basket": [("QQQ", "Top 100 US tech"), ("VOO", "Top 500 US companies")]},
    {"id": "santa", "group": "calendar", "name": "Santa Claus rally", "start": "12-18", "end": "01-05",
     "peak": "Usually the first days of January",
     "why": "The last week of December and first days of January have more often been up than down.",
     "basket": [("QQQ", "Top 100 US tech"), ("VOO", "Top 500 US companies")]},
    {"id": "january", "group": "calendar", "name": "January small-company effect", "start": "12-20", "end": "01-31",
     "peak": "Usually mid to late January",
     "why": "Small stocks sold for tax reasons in December often bounce in January.",
     "basket": [("IWM", "2,000 smaller US companies")]},
    # Industry and commodity seasons
    {"id": "holiday", "group": "industry", "name": "Holiday shopping", "start": "10-15", "end": "12-24",
     "peak": "Usually late Nov to early Dec (Black Friday)",
     "why": "The biggest shopping weeks of the year; retail stocks often rise into it.",
     "basket": [("TGT", "Target"), ("AMZN", "Amazon"), ("WMT", "Walmart"), ("BBY", "Best Buy")]},
    {"id": "heating", "group": "industry", "name": "Winter heating (natural gas)", "start": "08-15", "end": "12-31",
     "peak": "Usually November to December",
     "why": "Gas demand rises before and during the US winter.",
     "basket": [("EQT", "EQT (gas producer)"), ("AR", "Antero Resources")]},
    {"id": "gold", "group": "industry", "name": "Festival gold demand", "start": "09-15", "end": "11-15",
     "peak": "Usually just before Diwali",
     "why": "Indian and Chinese gold buying rises for Diwali and weddings.",
     "basket": [("NEM", "Newmont (gold miner)"), ("GLD", "Gold fund")]},
    {"id": "hurricane", "group": "industry", "name": "Hurricane season", "start": "06-01", "end": "11-30",
     "peak": "Usually Aug to Oct, only when a big storm hits",
     "why": "Storm repairs lift home-improvement sales.",
     "basket": [("HD", "Home Depot"), ("LOW", "Lowe's")]},
    {"id": "tax", "group": "industry", "name": "US tax filing season", "start": "01-05", "end": "04-15",
     "peak": "Usually February to March",
     "why": "Americans file taxes by mid-April, when these companies earn most of their money.",
     "basket": [("HRB", "H&R Block"), ("INTU", "Intuit (TurboTax)")]},
    {"id": "planting", "group": "industry", "name": "Spring planting", "start": "02-01", "end": "04-30",
     "peak": "Usually April",
     "why": "US farmers buy fertilizer and machines before planting.",
     "basket": [("CF", "CF Industries (fertilizer)"), ("NTR", "Nutrien (fertilizer)"), ("DE", "Deere")]},
    {"id": "driving", "group": "industry", "name": "Summer driving (refiners)", "start": "02-15", "end": "05-31",
     "peak": "Usually May",
     "why": "Petrol demand rises into summer road trips; refiners often run up in spring.",
     "basket": [("VLO", "Valero"), ("MPC", "Marathon Petroleum"), ("XLE", "Energy fund")]},
    {"id": "travel", "group": "industry", "name": "Summer travel", "start": "03-01", "end": "06-15",
     "peak": "Usually May to early June",
     "why": "People book summer holidays in spring.",
     "basket": [("DAL", "Delta Air Lines"), ("BKNG", "Booking.com"), ("RCL", "Royal Caribbean")]},
    {"id": "ac", "group": "industry", "name": "Summer heat (air conditioning)", "start": "04-01", "end": "07-31",
     "peak": "Usually June to July",
     "why": "Air-conditioner sales and repairs peak in hot months.",
     "basket": [("CARR", "Carrier"), ("TT", "Trane Technologies")]},
    {"id": "school", "group": "industry", "name": "Back to school", "start": "07-01", "end": "08-31",
     "peak": "Usually mid August",
     "why": "Second-biggest US shopping season: clothes, supplies, laptops.",
     "basket": [("BBY", "Best Buy"), ("TGT", "Target")]},
]

MOMENTUM_CANDIDATES = {
    "NVDA": "Nvidia", "AVGO": "Broadcom", "MSFT": "Microsoft", "META": "Meta", "AMZN": "Amazon",
    "GOOGL": "Alphabet (Google)", "AAPL": "Apple", "AMD": "AMD", "MU": "Micron", "TSLA": "Tesla",
    "JPM": "JPMorgan", "QQQ": "Top 100 US tech",
    "GLD": "Gold fund", "CPER": "Copper fund", "FCX": "Freeport-McMoRan (copper)", "SLV": "Silver fund",
}
METALS = {"GLD": "Gold", "CPER": "Copper", "SLV": "Silver"}
FUNDS = {"QQQ", "VOO", "SPY", "IWM", "XLE", "GLD", "CPER", "SLV", "SGOV", "ITB", "COPX"}
SHOW_MOMENTUM = 5
YEARS = 10


# ----------------------------------------------------------------------------
# Supabase
# ----------------------------------------------------------------------------
def sb(method, path, body=None, prefer=None):
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


def upsert(table, rows, on):
    for i in range(0, len(rows), 200):
        sb("POST", f"{table}?on_conflict={on}", rows[i:i + 200], "resolution=merge-duplicates,return=minimal")


def now_ist():
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)


# ----------------------------------------------------------------------------
# Market data
# ----------------------------------------------------------------------------
def history(tk, period):
    import yfinance as yf
    try:
        h = yf.Ticker(tk).history(period=period, auto_adjust=True)
        return h if h is not None and len(h) > 3 else None
    except Exception as exc:
        print(f"  price failed for {tk}: {exc}")
        return None


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


def fetch_news(tk, name, limit=3, days=3):
    q = urllib.parse.quote(f"{tk} {name} stock")
    url = f"https://news.google.com/rss/search?q={q}+when:{days}d&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(urllib.request.urlopen(req, timeout=15).read())
    except Exception as exc:
        print(f"  news failed for {tk}: {exc}")
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        try:
            day = dt.datetime.strptime((it.findtext("pubDate") or "")[:16], "%a, %d %b %Y").date()
        except ValueError:
            day = TODAY
        if (TODAY - day).days > days:
            continue
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        out.append({"tk": tk, "day": day.isoformat(), "title": title, "source": src})
        if len(out) >= limit:
            break
    return out


# ----------------------------------------------------------------------------
# Calculations
# ----------------------------------------------------------------------------
def md(s):
    m, d = map(int, s.split("-"))
    return m, d


def occurrence(c, year):
    sm, sd = md(c["start"])
    em, ed = md(c["end"])
    s = dt.date(year, sm, sd)
    e = dt.date(year if (em, ed) >= (sm, sd) else year + 1, em, ed)
    return s, e


def cycle_status(c):
    """Return status and the (start, end) dates it refers to."""
    for y in (TODAY.year - 1, TODAY.year, TODAY.year + 1):
        s, e = occurrence(c, y)
        if e >= TODAY:
            break
    if s <= TODAY:
        frac = (TODAY - s).days / max(1, (e - s).days)
        return ("early" if frac < 0.35 else "mid" if frac < 0.75 else "peak"), s, e
    ps, pe = occurrence(c, s.year - 1)
    if 0 < (TODAY - pe).days <= 21:
        return "over", ps, pe
    return ("coming" if (s - TODAY).days <= 60 else "later"), s, e


def series(h):
    close = h["Close"].dropna()
    dates = [d.date() for d in close.index]
    return dates, [float(v) for v in close.values]


def window_returns(dates, vals, c):
    out = []
    for y in range(TODAY.year - YEARS - 1, TODAY.year + 1):
        s, e = occurrence(c, y)
        if e >= TODAY or s < dates[0]:
            continue
        before = [v for d, v in zip(dates, vals) if d < s]
        seg = [v for d, v in zip(dates, vals) if s <= d <= e]
        if len(seg) < 5 or not before:
            continue
        label = str(y) if s.year == e.year else f"{y}-{str(e.year)[2:]}"
        out.append({"y": label, "r": round((seg[-1] / before[-1] - 1) * 100, 1)})
    return out[-YEARS:]


def monthly_pattern(h):
    close = h["Close"].dropna()
    try:
        m = close.resample("ME").last()
    except (ValueError, KeyError):
        m = close.resample("M").last()
    r = (m.pct_change().dropna() * 100)
    months, wins = [], []
    for i in range(1, 13):
        x = r[r.index.month == i]
        months.append(round(float(x.mean()), 1) if len(x) else 0)
        wins.append(round(float((x > 0).mean() * 100)) if len(x) else 0)
    return {"months": months, "win": wins, "n": int(len(r) / 12)}


def basic_stats(dates, vals):
    price = vals[-1]
    last = vals[-252:]
    hi52 = max(last)
    low3m = min(vals[-63:])
    ma50 = sum(vals[-50:]) / min(50, len(vals))
    moves = [abs(vals[i] / vals[i - 1] - 1) * 100 for i in range(len(vals) - 20, len(vals))]
    return {"price": round(price, 2), "day": dates[-1].isoformat(), "hi52": round(hi52, 2),
            "low3m": round(low3m, 2), "ma50": round(ma50, 2),
            "offHigh": round((hi52 - price) / hi52 * 100, 1),
            "fromLow": round((price - low3m) / low3m * 100, 1),
            "ret3m": round((price / vals[-63] - 1) * 100, 1),
            "dailyMove": round(sum(moves) / len(moves), 2)}


def stop_pct(s):
    return int(min(10, max(4, round(s["dailyMove"] * 2.5))))


def stars(score):
    return 5 if score >= 6 else 4 if score >= 4 else 3 if score >= 2.5 else 2 if score >= 1 else 1


# ----------------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------------
def run_prices():
    tks = {r["tk"] for r in sb("GET", "prices?select=tk") or []}
    tks |= {r["tk"] for r in sb("GET", "watch?select=tk") or []}
    tks |= {r["tk"] for r in sb("GET", "buys?select=tk&sold=eq.false") or []}
    print(f"Latest prices for {len(tks)} tickers")
    rows = []
    for tk in sorted(tks):
        h = history(tk, "5d")
        if h is not None:
            rows.append({"tk": tk, "price": round(float(h["Close"].iloc[-1]), 2), "day": h.index[-1].date().isoformat()})
    upsert("prices", rows, "tk")
    upsert("kv", [{"k": "pricesAt", "v": {"at": now_ist().strftime("%d %b %H:%M IST")}}], "k")


def run_full():
    watch = [r["tk"] for r in sb("GET", "watch?select=tk") or []]
    held = [r["tk"] for r in sb("GET", "buys?select=tk&sold=eq.false") or []]
    tickers = {tk for c in CYCLES for tk, _ in c["basket"]} | set(MOMENTUM_CANDIDATES) | set(watch) | set(held)
    print(f"10-year history for {len(tickers)} tickers")
    data, stats, season = {}, {}, {}
    for tk in sorted(tickers):
        h = history(tk, f"{YEARS + 1}y")
        if h is None or len(h) < 70:
            continue
        dates, vals = series(h)
        data[tk] = (dates, vals)
        stats[tk] = basic_stats(dates, vals)
        season[tk] = monthly_pattern(h)
    upsert("prices", [{"tk": tk, "price": s["price"], "day": s["day"], "stats": s} for tk, s in stats.items()], "tk")

    earnings = {}

    def results_text(tk):
        if tk in FUNDS:
            return "Fund: no results day."
        if tk not in earnings:
            earnings[tk] = next_earnings(tk)
        ed = earnings[tk]
        return f"Next results about {ed}. Price can move 5–10% that day." if ed else "Check the results date before buying."

    cycles = []
    for c in CYCLES:
        st, s, e = cycle_status(c)
        if st == "later":
            continue
        items = []
        for tk, name in c["basket"]:
            if tk not in data:
                continue
            hist = window_returns(*data[tk], c)
            rs = [x["r"] for x in hist]
            win = round(sum(r > 0 for r in rs) / len(rs) * 100) if rs else 0
            med = round(statistics.median(rs), 1) if rs else 0
            avg = round(sum(rs) / len(rs), 1) if rs else 0
            sp = stop_pct(stats[tk])
            tp = int(min(25, max(6, round(med) if med > 0 else 6, round(sp * 1.5))))
            p = stats[tk]["price"]
            items.append({"tk": tk, "name": name, "hist": hist, "win": win, "med": med, "avg": avg,
                          "score": round(med * win / 100, 2) if med > 0 else 0, "stop": sp, "target": tp,
                          "entry": [round(p * 0.95, 2), round(p * 0.96, 2)] if st in ("early", "mid", "coming") else None,
                          "results": results_text(tk)})
        if not items:
            continue
        items.sort(key=lambda x: x["score"], reverse=True)
        strength = items[0]["score"]
        buy = {"early": "Now, on a 4–5% dip",
               "mid": "Only on a dip; it's already under way",
               "peak": "Too late. Sell on strength",
               "over": "Cycle ended. Sell if still holding",
               "coming": f"From about {(s - dt.timedelta(days=20)).strftime('%d %b')}, on dips"}[st]
        cycles.append({"id": c["id"], "group": c["group"], "name": c["name"], "status": st,
                       "now": st in ("early", "mid"), "start": c["start"], "end": c["end"],
                       "period": f"{s:%d %b} – {e:%d %b}", "peak": c["peak"], "why": c["why"], "buy": buy,
                       "sellBy": f"By about {(e - dt.timedelta(days=7)):%d %b}", "startIso": s.isoformat(),
                       "strength": strength, "stars": stars(strength), "basket": items})
    live = sorted([c for c in cycles if c["status"] in ("early", "mid", "coming")], key=lambda c: c["strength"], reverse=True)
    for i, c in enumerate(live[:3]):
        c["rank"] = i + 1

    momentum = []
    for tk, name in MOMENTUM_CANDIDATES.items():
        s = stats.get(tk)
        if not s or s["ret3m"] <= 5 or s["price"] < s["ma50"] * 0.97:
            continue
        leg = 12 if tk in FUNDS else 30
        done = max(5, min(95, round(s["fromLow"] / leg * 100)))
        room = round(max(0, leg - s["fromLow"]), 1)
        stage = "cooling" if (s["offHigh"] > 8 and s["price"] < s["ma50"]) else "late" if done >= 70 else "steady"
        sp = stop_pct(s)
        momentum.append({"tk": tk, "name": name, "stage": stage, "done": done, "potential": room if stage != "cooling" else 0,
                         "potentialTo": round(s["price"] * (1 + room / 100), 2) if room >= 2 else None,
                         "frame": "next 1–3 months" if tk in FUNDS else "next 3–6 weeks",
                         "stop": sp, "target": int(min(25, max(6, round(room)))) if room >= 6 else 6,
                         "entry": None if stage == "cooling" else [round(s["price"] * 0.95, 2), round(s["price"] * 0.96, 2)],
                         "why": (f"Up {s['fromLow']:.0f}% from its 3-month low, {s['offHigh']:.0f}% below its 1-year high, "
                                 f"{'above' if s['price'] > s['ma50'] else 'below'} its 50-day average."),
                         "warn": "Momentum has stalled. Don't buy now." if stage == "cooling" else
                                 "Late in the move. Only buy on a dip." if stage == "late" else "",
                         "results": results_text(tk)})
    momentum.sort(key=lambda m: m["potential"], reverse=True)
    momentum = momentum[:SHOW_MOMENTUM]

    metals = []
    for tk, n in METALS.items():
        s = stats.get(tk)
        if not s:
            continue
        status = ("In momentum" if s["ret3m"] > 8 and s["offHigh"] < 5 else
                  "Mild uptrend" if s["ret3m"] > 0 and s["offHigh"] < 10 else
                  "Cooling" if s["offHigh"] > 8 and s["price"] < s["ma50"] else "No momentum now")
        metals.append({"tk": tk, "name": n, "status": status,
                       "detail": f"{tk}: {s['ret3m']:+.0f}% in 3 months, {s['offHigh']:.0f}% below its 1-year high."})

    # News (last 3 days) for every recommended stock + her watchlist and holdings
    names = {b["tk"]: b["name"] for c in cycles for b in c["basket"]}
    names.update({m["tk"]: m["name"] for m in momentum})
    for tk in watch + held:
        names.setdefault(tk, tk)
    print(f"News for {len(names)} tickers")
    rows = []
    for tk, n in names.items():
        rows += fetch_news(tk, n)
    upsert("news", rows, "tk,title")
    sb("DELETE", f"news?day=lt.{(TODAY - dt.timedelta(days=7)).isoformat()}")

    last_day = max((s["day"] for s in stats.values()), default=TODAY.isoformat())
    snapshot = {"asOf": last_day, "asOfLabel": f"{last_day} close (analysis updated {now_ist():%d %b %H:%M} IST)",
                "cycles": cycles, "momentum": momentum, "metals": metals, "season": season, "years": YEARS}
    upsert("kv", [{"k": "snapshot", "v": snapshot}], "k")


if __name__ == "__main__":
    print("Mode:", MODE)
    run_prices() if MODE == "prices" else run_full()
    print("Done.")
