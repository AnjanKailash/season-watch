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
import time
import urllib.error
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
FUNDS = {"QQQ", "VOO", "SPY", "VTI", "VT", "VXUS", "VUG", "VYM", "SCHD", "BND", "IWM", "XLE", "GLD", "CPER", "SLV", "SGOV", "ITB", "COPX"}
SHOW_MOMENTUM = 5
YEARS = 10

# Swing universe: liquid, fairly volatile US stocks by category (edit freely)
SWING_UNIVERSE = {
    "Tech": {"AAPL": "Apple", "MSFT": "Microsoft", "META": "Meta", "GOOGL": "Alphabet", "AMZN": "Amazon",
             "NFLX": "Netflix", "CRM": "Salesforce", "PLTR": "Palantir", "SHOP": "Shopify", "UBER": "Uber"},
    "Chips": {"NVDA": "Nvidia", "AMD": "AMD", "AVGO": "Broadcom", "MU": "Micron", "TSM": "Taiwan Semiconductor",
              "QCOM": "Qualcomm", "SMH": "Chip stocks fund"},
    "Consumer": {"TSLA": "Tesla", "NKE": "Nike", "LULU": "Lululemon", "SBUX": "Starbucks", "TGT": "Target",
                 "HD": "Home Depot", "CMG": "Chipotle"},
    "Finance": {"JPM": "JPMorgan", "GS": "Goldman Sachs", "BAC": "Bank of America", "V": "Visa", "PYPL": "PayPal",
                "COIN": "Coinbase"},
    "Energy": {"XOM": "Exxon Mobil", "OXY": "Occidental", "VLO": "Valero", "SLB": "Schlumberger", "EQT": "EQT"},
    "Metals": {"FCX": "Freeport (copper)", "NEM": "Newmont (gold)", "GDX": "Gold miners fund", "SLV": "Silver fund",
               "AA": "Alcoa", "NUE": "Nucor (steel)"},
    "Industrial": {"CAT": "Caterpillar", "DE": "Deere", "GE": "GE Aerospace", "BA": "Boeing", "ETN": "Eaton"},
    "Health": {"LLY": "Eli Lilly", "UNH": "UnitedHealth", "ABBV": "AbbVie", "ISRG": "Intuitive Surgical", "MRNA": "Moderna"},
    "Travel": {"DAL": "Delta Air Lines", "UAL": "United Airlines", "BKNG": "Booking.com", "RCL": "Royal Caribbean",
               "ABNB": "Airbnb"},
}
SWING_SETUPS = {
    "days": {"label": "Next few days", "h": 5, "min_vol": 1.3,
             "rule": "Sharp short dip (very oversold) while the stock is in a long-term uptrend"},
    "weeks": {"label": "1–3 weeks", "h": 10, "min_vol": 1.3,
              "rule": "7–20% pullback from its 3-month high, still above its 200-day average"},
    "months": {"label": "1–3 months", "h": 60, "min_vol": 1.0,
               "rule": "Back near its rising 200-day average after a 15–35% fall from the 1-year high"},
}
SWING_PER_TF = 8


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
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else None
    except urllib.error.HTTPError as exc:
        print(f"  Supabase {method} {path.split('?')[0]} failed: {exc.code} {exc.read().decode()[:300]}")
        raise


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
    h = h.dropna(subset=["Close"])
    dates = [d.date() for d in h.index]
    vals = [float(v) for v in h["Close"].values]
    lows = [float(v) for v in (h["Low"] if "Low" in h else h["Close"]).fillna(h["Close"]).values]
    vols = [float(v) for v in (h["Volume"] if "Volume" in h else h["Close"] * 0).fillna(0).values]
    return dates, vals, lows, vols


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
    moves = [abs(vals[i] / vals[i - 1] - 1) * 100 for i in range(max(1, len(vals) - 20), len(vals))] or [0.0]
    return {"price": round(price, 2), "day": dates[-1].isoformat(), "hi52": round(hi52, 2),
            "low3m": round(low3m, 2), "ma50": round(ma50, 2),
            "offHigh": round((hi52 - price) / hi52 * 100, 1),
            "fromLow": round((price - low3m) / low3m * 100, 1),
            "ret3m": round((price / vals[max(0, len(vals) - 63)] - 1) * 100, 1),
            "dailyMove": round(sum(moves) / len(moves), 2)}


def stop_pct(s):
    return int(min(10, max(4, round(s["dailyMove"] * 2.5))))


def stars(score):
    return 5 if score >= 6 else 4 if score >= 4 else 3 if score >= 2.5 else 2 if score >= 1 else 1


# ----------------------------------------------------------------------------
# Swing setups: pullbacks in strong, volatile stocks, checked against each
# stock's own last 10 years ("when it looked like this before, what happened?")
# ----------------------------------------------------------------------------
def sma_list(vals, n):
    out, acc = [None] * len(vals), 0.0
    for i, v in enumerate(vals):
        acc += v
        if i >= n:
            acc -= vals[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def rsi_list(vals, n):
    out = [None] * len(vals)
    if len(vals) <= n:
        return out
    gains = [max(0.0, vals[i] - vals[i - 1]) for i in range(1, n + 1)]
    losses = [max(0.0, vals[i - 1] - vals[i]) for i in range(1, n + 1)]
    ag, al = sum(gains) / n, sum(losses) / n
    for i in range(n, len(vals)):
        if i > n:
            ch = vals[i] - vals[i - 1]
            ag = (ag * (n - 1) + max(0.0, ch)) / n
            al = (al * (n - 1) + max(0.0, -ch)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def swing_signals(vals):
    s200, r2, r14 = sma_list(vals, 200), rsi_list(vals, 2), rsi_list(vals, 14)

    def sig(tf, i):
        if i < 260 or s200[i] is None:
            return False
        v = vals[i]
        if tf == "days":
            return r2[i] is not None and r2[i] < 10 and v > s200[i]
        if tf == "weeks":
            hi = max(vals[i - 63:i + 1])
            dd = (hi - v) / hi * 100
            return v > s200[i] and 7 <= dd <= 20 and r14[i] is not None and 30 <= r14[i] <= 45
        hi = max(vals[i - 252:i + 1])
        dd = (hi - v) / hi * 100
        return abs(v / s200[i] - 1) <= 0.04 and s200[i] > s200[i - 50] and 15 <= dd <= 35
    return sig, r14


def pctile(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (k - lo)


def pivots(xs, k=5):
    """Indexes of swing lows/highs: lowest/highest point within k days either side."""
    lo, hi = [], []
    for i in range(k, len(xs) - k):
        win = xs[i - k:i + k + 1]
        if xs[i] == min(win):
            lo.append(i)
        if xs[i] == max(win):
            hi.append(i)
    return lo, hi


def setup_odds(vals, sig, tf, last):
    h, outs, i = SWING_SETUPS[tf]["h"], [], 260
    while i < last - h:
        if sig(tf, i):
            outs.append(round((vals[i + h] / vals[i] - 1) * 100, 1))
            i += h
        else:
            i += 1
    return outs


def analyze(tk, name, cat, dates, vals, lows, vols, st):
    """Full swing check for one stock: dip setup + historical odds + breakdown rules."""
    last = len(vals) - 1
    if last < 300:
        p = vals[last]
        flags, good = [], []
        if last >= 30:
            sup = min(lows[:-10]) if last > 40 else min(lows)
            if min(vals[-5:]) < sup * 0.99:
                flags.append(f"Fell below its lowest level since listing (${sup:,.2f})")
            m20 = sum(vals[-20:]) / 20
            (good if p > m20 else flags).append(f"{'Above' if p > m20 else 'Below'} its 20-day average (${m20:,.2f})")
        return {"tk": tk, "name": name, "cat": cat, "verdict": "new", "label": "Too new to judge",
                "flags": flags, "good": good,
                "waitFor": [f"Only {last + 1} trading days of history since {dates[0]:%d %b %Y}. Past-pattern odds need about 300, "
                            "so treat it as higher-risk and keep any buy small."],
                "offHigh": st["offHigh"], "vol": st["dailyMove"], "price": round(p, 2), "day": dates[last].isoformat(),
                "entry": [round(p * 0.98, 2), round(p, 2)]}
    p = vals[last]
    s200 = sma_list(vals, 200)
    sig, r14 = swing_signals(vals)
    above200 = p > s200[last]
    rising200 = s200[last] > s200[last - 50]

    # --- breakdown rules ("don't catch a falling knife") ---
    flags, good, wait_for = [], [], []
    low52 = min(lows[-252:])
    recent_low_i = max(range(last - 9, last + 1), key=lambda i: -lows[i])
    if lows[recent_low_i] <= low52 * 1.001:
        flags.append(f"Made a new 1-year low on {dates[recent_low_i]:%d %b}")
    support = min(lows[-126:-10])
    broke_i = next((i for i in range(last - 9, last + 1) if vals[i] < support * 0.99), None)
    reclaimed = all(v > support for v in vals[-3:])
    if broke_i is not None and not reclaimed:
        flags.append(f"Broke below support ${support:,.2f} (its 6-month floor)")
        wait_for.append(f"Close back above ${support:,.2f} and stay there for 3+ days")
        avgv = sum(vols[broke_i - 50:broke_i]) / 50 if broke_i >= 50 else 0
        if avgv and vols[broke_i] > 1.5 * avgv:
            flags.append("The breakdown came on heavy volume (big sellers)")
    elif broke_i is not None and reclaimed:
        good.append(f"Dipped below ${support:,.2f} but has climbed back above it")
    plo, phi = pivots(lows[-130:]), None
    plo = [i + len(lows) - 130 for i in plo[0]]
    phi = [i + len(vals) - 130 for i in pivots(vals[-130:])[1]]
    if len(plo) >= 2 and len(phi) >= 2:
        if lows[plo[-1]] < lows[plo[-2]] and vals[phi[-1]] < vals[phi[-2]]:
            flags.append("Lower highs and lower lows: the trend has turned down")
            wait_for.append(f"A higher low: a dip that stays above ${lows[plo[-1]]:,.2f}")
        elif lows[plo[-1]] > lows[plo[-2]]:
            good.append("Making higher lows: buyers are stepping in earlier")
    if not above200 and not rising200:
        flags.append("Below a falling 200-day average")
        wait_for.append(f"Back above its 200-day average (about ${s200[last]:,.2f})")
    elif above200:
        good.append("Above its 200-day average (long-term uptrend)")

    # --- dip setup + this stock's own past odds ---
    best = None
    for tf, cfg in SWING_SETUPS.items():
        now = sig(tf, last)
        bounce = False
        if not now and tf == "days" and sig(tf, last - 1) and vals[last] > vals[last - 1]:
            now, bounce = True, True
        if not now:
            continue
        outs = setup_odds(vals, sig, tf, last)
        if len(outs) < 8:
            continue
        win = round(sum(o > 0 for o in outs) / len(outs) * 100)
        med = round(statistics.median(outs), 1)
        score = round(win / 100 * med, 2) if med > 0 else 0
        if best is None or score > best["score"]:
            h = cfg["h"]
            stop = int(min(12, max(3, round(st["dailyMove"] * (h ** 0.5) * 1.2))))
            p75 = round(pctile(outs, 0.75), 1)
            best = {"tf": tf, "tfLabel": cfg["label"], "h": h, "rule": cfg["rule"],
                    "bounce": bool(bounce or vals[last] > vals[last - 1]), "n": len(outs), "win": int(win),
                    "med": float(med), "p75": float(p75), "past": [float(o) for o in outs[-12:]],
                    "score": float(score), "stop": stop, "target": int(max(round(p75), round(stop * 1.5), 3)),
                    "volOk": st["dailyMove"] >= cfg["min_vol"]}

    serious = len(flags)
    if serious >= 2 and (not above200 or any("1-year low" in f for f in flags)):
        verdict, label = "avoid", "Avoid for now"
    elif serious:
        verdict, label = "wait", "Wait and watch"
    elif best and best["win"] >= 55 and best["med"] > 0:
        verdict, label = "buy", "Good dip to buy"
    elif best:
        verdict, label = "weak", "Dip, but weak past odds"
    else:
        verdict, label = "nodip", "No dip right now"
        if st["offHigh"] < 4:
            wait_for.append(f"A 4–5% pullback, to about ${p * 0.955:,.2f}")
    out = {"tk": tk, "name": name, "cat": cat, "verdict": verdict, "label": label, "flags": flags,
           "good": good, "waitFor": wait_for, "offHigh": st["offHigh"], "vol": st["dailyMove"],
           "rsi": int(round(r14[last] or 0)), "support": round(support, 2), "price": round(p, 2),
           "entry": [round(p * 0.98, 2), round(p, 2)], "day": dates[last].isoformat()}
    if best:
        out.update(best)
    return out


def analyze_all(data, stats, names):
    res = {}
    for tk, (name, cat) in names.items():
        if tk in data and tk in stats:
            try:
                a = analyze(tk, name, cat, *data[tk], stats[tk])
                if a:
                    res[tk] = a
            except Exception as exc:
                print(f"  analysis failed for {tk}: {exc}")
    return res


def swing_lists(analysis):
    swing_tks = {tk for g in SWING_UNIVERSE.values() for tk in g}
    picks, wait = [], []
    for tk, a in analysis.items():
        if tk not in swing_tks:
            continue
        if a["verdict"] == "buy" and a.get("volOk"):
            picks.append(a)
        elif a["verdict"] in ("wait", "avoid") and (a.get("tf") or a["offHigh"] >= 7):
            wait.append(a)
    out = []
    for tf in SWING_SETUPS:
        out += sorted([x for x in picks if x["tf"] == tf], key=lambda x: x["score"], reverse=True)[:SWING_PER_TF]
    wait.sort(key=lambda x: x["offHigh"], reverse=True)
    return out, wait[:12]


# ----------------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------------
def run_prices():
    tks = {r["tk"] for r in sb("GET", "prices?select=tk") or []}
    tks |= {r["tk"] for r in sb("GET", "watch?select=tk") or []}
    tks |= {r["tk"] for r in sb("GET", "buys?select=tk&sold=eq.false") or []}
    print(f"Latest prices for {len(tks)} tickers")
    rows, chg = [], {}
    for tk in sorted(tks):
        h = history(tk, "5d")
        if h is not None:
            c = h["Close"].dropna()
            rows.append({"tk": tk, "price": round(float(c.iloc[-1]), 2), "day": h.index[-1].date().isoformat()})
            if len(c) > 1:
                chg[tk] = round((float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100, 2)
    upsert("prices", rows, "tk")
    upsert("kv", [{"k": "pricesAt", "v": {"at": now_ist().strftime("%d %b %H:%M IST"),
                                           "iso": dt.datetime.now(dt.timezone.utc).isoformat()}},
                  {"k": "changes", "v": chg}], "k")
    analyse_new_watch()


def chart_payload(h, days=252):
    """1 year of daily candles + 50/200-day averages, for the in-app chart."""
    h = h.dropna(subset=["Close"])
    closes = [float(x) for x in h["Close"].values]
    m50, m200 = sma_list(closes, 50), sma_list(closes, 200)
    tail = h.iloc[-days:]
    k = len(h) - len(tail)
    col = lambda name: [round(float(x), 2) for x in (tail[name] if name in tail else tail["Close"]).fillna(tail["Close"]).values]
    return {"d": [d.date().isoformat() for d in tail.index], "o": col("Open"), "h": col("High"), "l": col("Low"),
            "c": col("Close"),
            "v": [int(x) for x in (tail["Volume"] if "Volume" in tail else tail["Close"] * 0).fillna(0).values],
            "m50": [None if x is None else round(x, 2) for x in m50[k:]],
            "m200": [None if x is None else round(x, 2) for x in m200[k:]]}


def save_charts(charts):
    rows = [{"k": f"h:{tk}", "v": v} for tk, v in charts.items()]
    for i in range(0, len(rows), 10):
        try:
            sb("POST", "kv?on_conflict=k", rows[i:i + 10], "resolution=merge-duplicates,return=minimal")
        except Exception as exc:
            print(f"  chart save skipped for a batch: {exc}")


# ----------------------------------------------------------------------------
# Long-term health: is the business still growing? (for 2+ year holdings)
# ----------------------------------------------------------------------------
FINANCIAL_SECTORS = ("Financial Services",)


def _row(df, *names):
    if df is None or getattr(df, "empty", True):
        return []
    for n in names:
        if n in df.index:
            vals = [float(v) for v in df.loc[n].values if v == v]  # newest first, drop NaN
            return vals
    return []


def fundamentals(tk):
    import yfinance as yf
    try:
        t = yf.Ticker(tk)
        info = t.info or {}
        try:
            inc = t.income_stmt
        except Exception:
            inc = None
        return info, inc
    except Exception as exc:
        print(f"  fundamentals failed for {tk}: {exc}")
        return {}, None


def longterm_view(tk, name, vals, spy_vals):
    """Plain-language long-term check: red flags that the business/stock has no near future."""
    flags, good = [], []
    is_fund = tk in FUNDS
    p = vals[-1]
    # --- price-based (works for funds too) ---
    if len(vals) > 756 and spy_vals and len(spy_vals) > 756:
        r3 = (p / vals[-757] - 1) * 100
        m3 = (spy_vals[-1] / spy_vals[-757] - 1) * 100
        if r3 < m3 - 40:
            flags.append(f"Far behind the market for 3 years ({r3:+.0f}% vs market {m3:+.0f}%)")
        elif r3 > m3:
            good.append(f"Beat the market over 3 years ({r3:+.0f}% vs {m3:+.0f}%)")
    s200 = sma_list(vals, 200)
    if len(vals) > 330 and s200[-1]:
        below = sum(1 for i in range(len(vals) - 126, len(vals)) if s200[i] and vals[i] < s200[i]) / 126
        if below > 0.8 and s200[-1] < s200[-127]:
            flags.append("In a long slide: below its falling 200-day average for most of 6 months")
        elif vals[-1] > s200[-1] and s200[-1] > s200[-127]:
            good.append("Long-term price trend is rising")
    fin = {}
    if not is_fund:
        info, inc = fundamentals(tk)
        if info.get("quoteType") in ("ETF", "MUTUALFUND"):
            is_fund, inc = True, None
    if not is_fund:
        rev = _row(inc, "Total Revenue", "Operating Revenue")
        ni = _row(inc, "Net Income", "Net Income Common Stockholders")
        op = _row(inc, "Operating Income")
        if len(rev) >= 3:
            if rev[0] < rev[1] < rev[2]:
                flags.append("Sales shrank 2 years in a row")
            elif rev[0] > rev[1]:
                good.append(f"Sales growing ({(rev[0] / rev[1] - 1) * 100:+.0f}% last year)")
        if len(ni) >= 2:
            if ni[0] < 0 and ni[0] < ni[1]:
                flags.append("Losing money, and losses got bigger")
            elif ni[0] > 0:
                good.append("Profitable")
        if len(op) >= 3 and len(rev) >= 3 and rev[0] and rev[2]:
            m0, m2 = op[0] / rev[0] * 100, op[2] / rev[2] * 100
            if m0 < m2 - 5:
                flags.append(f"Profit margin falling ({m2:.0f}% → {m0:.0f}% in 2 years)")
        de = info.get("debtToEquity")
        if de and de > 200 and info.get("sector") not in FINANCIAL_SECTORS:
            flags.append("Heavy debt compared with its size")
        fcf = info.get("freeCashflow")
        if fcf is not None:
            (good if fcf > 0 else flags).append("Generates spare cash" if fcf > 0 else "Burning cash (negative free cash flow)")
        rec, n_an = info.get("recommendationMean"), info.get("numberOfAnalystOpinions") or 0
        if rec and n_an >= 5:
            if rec >= 3.5:
                flags.append(f"Most of {n_an} analysts say sell")
            elif rec <= 2.2:
                good.append(f"Most of {n_an} analysts say buy")
        tgt = info.get("targetMeanPrice")
        if tgt and n_an >= 5:
            fin["target"] = round(float(tgt), 2)
            fin["upside"] = round((tgt / p - 1) * 100, 1)
        for k_src, k in (("trailingPE", "pe"), ("forwardPE", "fpe"), ("revenueGrowth", "revG"),
                         ("profitMargins", "margin"), ("sector", "sector")):
            v = info.get(k_src)
            if v is not None:
                fin[k] = round(float(v), 3) if isinstance(v, (int, float)) else v
        fin["years"] = min(len(rev), 4)
    n = len(flags)
    verdict = "sell" if n >= 3 else "watch" if n == 2 else "healthy"
    label = {"sell": "Consider selling: business weakening", "watch": "Watch closely",
             "healthy": "Healthy: fine to hold"}[verdict]
    if not is_fund and fin.get("years", 0) < 2 and n < 3:
        verdict, label = ("watch" if n else "unknown"), ("Watch closely" if n else "Not enough company data")
    return {"tk": tk, "name": name, "verdict": verdict, "label": label, "flags": flags, "good": good,
            "fund": is_fund, **fin}


def longterm_all(data, names):
    spy = data.get("SPY", (None, []))[1]
    out = {}
    for tk, name in names.items():
        if tk in data:
            try:
                out[tk] = longterm_view(tk, name, data[tk][1], spy)
                time.sleep(0.3)
            except Exception as exc:
                print(f"  long-term check failed for {tk}: {exc}")
    return out


def fetch_symbols():
    """All US-listed stocks/ETFs (code + name) for the app's search box."""
    out = {}
    for url, sym_col, name_col, etf_col in (
            ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", 0, 1, 6),
            ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", 0, 1, 4)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            lines = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore").splitlines()
        except Exception as exc:
            print(f"  symbol list failed ({url}): {exc}")
            continue
        for line in lines[1:]:
            parts = line.split("|")
            if len(parts) < 7 or line.startswith("File Creation"):
                continue
            sym, name = parts[sym_col].strip(), parts[name_col].strip()
            if not sym or any(ch in sym for ch in "$^") or parts[-2 if "otherlisted" in url else 3].strip() == "Y":
                continue  # skip test issues and odd share classes
            low = name.lower()
            if any(w in low for w in (" warrant", " right", " unit", "preferred", " notes ", "depositary share")):
                continue
            nice = name.split(" - ")[0]
            for junk in (" Common Stock", " Ordinary Shares", " Class A", " Class B", " Common Shares"):
                nice = nice.replace(junk, "")
            out[sym.replace(".", "-")] = nice.strip()[:60]
    return out


def analyse_new_watch(limit=5):
    """Stocks she just searched or added get a full swing check within one price run."""
    snap_rows = sb("GET", "kv?k=eq.snapshot&select=v") or []
    if not snap_rows:
        return
    snap = snap_rows[0]["v"]
    have = snap.setdefault("analysis", {})
    watch = [r["tk"] for r in sb("GET", "watch?select=tk") or []]
    todo = [tk for tk in watch if tk not in have][:limit]
    if not todo:
        return
    print(f"Analysing newly watched: {todo}")
    for tk in todo:
        h = history(tk, f"{YEARS + 1}y")
        if h is None or len(h) < 20:
            have[tk] = {"tk": tk, "name": tk, "cat": "Watchlist", "verdict": "na", "label": "Not enough price history",
                        "flags": [], "good": [], "waitFor": []}
            continue
        dates, vals, lows, vols = series(h)
        a = analyze(tk, tk, "Watchlist", dates, vals, lows, vols, basic_stats(dates, vals))
        save_charts({tk: chart_payload(h)})
        if a:
            have[tk] = a
        try:
            spy_h = history("SPY", f"{YEARS + 1}y")
            spy_v = series(spy_h)[1] if spy_h is not None else []
            snap.setdefault("longterm", {})[tk] = longterm_view(tk, tk, vals, spy_v)
        except Exception as exc:
            print(f"  long-term check failed for {tk}: {exc}")
    upsert("kv", [{"k": "snapshot", "v": snap}], "k")


def run_full():
    watch = [r["tk"] for r in sb("GET", "watch?select=tk") or []]
    held = [r["tk"] for r in sb("GET", "buys?select=tk&sold=eq.false") or []]
    swing_tks = {tk for g in SWING_UNIVERSE.values() for tk in g}
    tickers = {tk for c in CYCLES for tk, _ in c["basket"]} | set(MOMENTUM_CANDIDATES) | swing_tks | set(watch) | set(held) | {"SPY"}
    print(f"10-year history for {len(tickers)} tickers")
    data, stats, season, charts = {}, {}, {}, {}
    for tk in sorted(tickers):
        h = history(tk, f"{YEARS + 1}y")
        if h is None or len(h) < 20:
            continue
        dates, vals, lows, vols = series(h)
        charts[tk] = chart_payload(h)
        time.sleep(0.2)  # be gentle with Yahoo
        data[tk] = (dates, vals, lows, vols)
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
            hist = window_returns(data[tk][0], data[tk][1], c)
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
                       "sellBy": f"By about {(e - dt.timedelta(days=7)):%d %b}", "startIso": s.isoformat(), "endIso": e.isoformat(),
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
                         "low3m": s["low3m"], "leg": leg, "kind": "fund" if tk in FUNDS else "stock",
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
    who = {}
    for c in CYCLES:
        for tk, n in c["basket"]:
            who[tk] = (n, "Cycle")
    for tk, n in MOMENTUM_CANDIDATES.items():
        who[tk] = (n, "Momentum")
    for cat, g in SWING_UNIVERSE.items():
        for tk, n in g.items():
            who[tk] = (n, cat)
    for tk in watch + held:
        who.setdefault(tk, (tk, "Watchlist"))
    analysis = analyze_all(data, stats, who)
    print("Long-term checks...")
    longterm = longterm_all(data, {tk: v[0] for tk, v in who.items()})
    swing, swing_wait = swing_lists(analysis)
    names.update({x["tk"]: x["name"] for x in swing})
    for tk in watch + held:
        names.setdefault(tk, tk)
    print(f"News for {len(names)} tickers")
    seen, rows = set(), []
    for tk, n in names.items():
        for row in fetch_news(tk, n):
            key = (row["tk"], row["title"])
            if row["title"] and key not in seen:  # Google often repeats a headline; duplicates break the save
                seen.add(key)
                rows.append(row)
    try:
        upsert("news", rows, "tk,title")
        sb("DELETE", f"news?day=lt.{(TODAY - dt.timedelta(days=7)).isoformat()}")
    except Exception as exc:  # news is a nice-to-have: never let it stop the analysis from saving
        print(f"  news save skipped: {exc}")

    last_day = max((s["day"] for s in stats.values()), default=TODAY.isoformat())
    snapshot = {"asOf": last_day, "asOfLabel": f"{last_day} close (analysis updated {now_ist():%d %b %H:%M} IST)",
                "cycles": cycles, "momentum": momentum, "metals": metals, "swing": swing,
                "swingWait": swing_wait, "analysis": analysis, "longterm": longterm,
                "season": season, "years": YEARS}
    chg = {tk: round((data[tk][1][-1] / data[tk][1][-2] - 1) * 100, 2) for tk in data if len(data[tk][1]) > 1}
    snapshot["spark"] = {tk: {"d": data[tk][0][-1].isoformat(), "v": [round(x, 2) for x in data[tk][1][-30:]]} for tk in data}
    save_charts(charts)
    symbols = fetch_symbols()
    if len(symbols) > 1000:
        try:
            upsert("kv", [{"k": "symbols", "v": symbols}], "k")
        except Exception as exc:
            print(f"  symbol list save skipped: {exc}")
    upsert("kv", [{"k": "snapshot", "v": snapshot}, {"k": "changes", "v": chg},
                  {"k": "pricesAt", "v": {"at": now_ist().strftime("%d %b %H:%M IST"),
                                         "iso": dt.datetime.now(dt.timezone.utc).isoformat()}}], "k")


if __name__ == "__main__":
    print("Mode:", MODE)
    run_prices() if MODE == "prices" else run_full()
    print("Done.")
