import os, json, pandas as pd, yfinance as yf
from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime

app = Flask(__name__)

SCAN_LIMIT = 30
DB_FILE = "track.json"


# ===== 市場清單 =====
def get_full_market_list():
    try:
        tse = pd.read_html("http://isin.twse.com.tw/isin/C_public.jsp?strMode=2")[0]
        df = tse[tse[0].str.contains(r'^\d{4}', na=False)]

        market_map = {}
        for item in df[0]:
            code, name = item.split()[:2]
            market_map[f"{code}.TW"] = name

        return market_map
    except:
        return {"2330.TW": "台積電"}


# ===== 策略 =====
def analyze_stock(hist):
    close = hist['Close']
    change = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()

    signals = []
    if change > 2:
        signals.append("短線動能")
    if ma5.iloc[-1] > ma20.iloc[-1]:
        signals.append("多頭趨勢")

    return signals, round(change, 2)


# ===== 掃描 =====
def scan_market():
    stocks = get_full_market_list()
    symbols = list(stocks.keys())[:SCAN_LIMIT]

    try:
        data = yf.download(symbols, period="1mo", group_by='ticker', threads=False)
    except:
        return []

    results = []

    for sym in symbols:
        try:
            if sym not in data:
                continue
            hist = data[sym].dropna()
            if len(hist) < 20:
                continue

            signals, change = analyze_stock(hist)

            if signals:
                results.append({
                    "symbol": sym,
                    "name": stocks[sym],
                    "price": round(hist['Close'].iloc[-1], 2),
                    "change": change,
                    "signals": signals
                })
        except:
            continue

    return sorted(results, key=lambda x: x['change'], reverse=True)


# ===== 指數 =====
def get_index():
    try:
        twii = yf.Ticker("^TWII").fast_info['last_price']
        otc = yf.Ticker("^TWOII").fast_info['last_price']
        return round(twii, 0), round(otc, 2)
    except:
        return "-", "-"


# ===== 追蹤 =====
def load_track():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return []


def save_track(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f)


def calc_stats(tracks):
    win = 0
    total = 0
    total_return = 0

    for t in tracks:
        if t['pnl'] != "-":
            total += 1
            total_return += t['pnl']
            if t['pnl'] > 0:
                win += 1

    winrate = round(win / total * 100, 2) if total > 0 else 0
    avg = round(total_return / total, 2) if total > 0 else 0

    return winrate, avg


@app.route("/")
def index():
    recs = []
    if request.args.get("scan") == "true":
        recs = scan_market()

    twii, otc = get_index()
    tracks = load_track()

    for t in tracks:
        try:
            curr = yf.Ticker(t['symbol']).fast_info['last_price']
            pnl = (curr - t['price']) / t['price'] * 100

            t['curr'] = round(curr, 2)
            t['pnl'] = round(pnl, 2)

            # 🎯 進出場策略
            if pnl <= -3:
                t['signal'] = "停損"
            elif pnl >= 15:
                t['signal'] = "停利"
            else:
                t['signal'] = "持有"

        except:
            t['curr'] = "-"
            t['pnl'] = "-"
            t['signal'] = "-"

    winrate, avg = calc_stats(tracks)

    return render_template("index.html",
                           recs=recs,
                           twii=twii,
                           otc=otc,
                           tracks=tracks,
                           now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           winrate=winrate,
                           avg=avg)


@app.route("/track/<symbol>/<name>/<price>")
def track(symbol, name, price):
    data = load_track()

    data.append({
        "symbol": symbol,
        "name": name,
        "price": float(price),
        "date": datetime.now().strftime("%Y-%m-%d")
    })

    save_track(data)
    return redirect(url_for("index"))


@app.route("/untrack/<symbol>")
def untrack(symbol):
    data = [x for x in load_track() if x['symbol'] != symbol]
    save_track(data)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
