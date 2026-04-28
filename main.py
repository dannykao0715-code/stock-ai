import os
import json
import pandas as pd
import yfinance as yf
from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime

app = Flask(__name__)

SCAN_LIMIT = 50
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

    except Exception:
        return {
            "2330.TW": "台積電",
            "2317.TW": "鴻海",
            "2454.TW": "聯發科",
            "2303.TW": "聯電",
            "2603.TW": "長榮"
        }


# ===== 單檔股票策略分析 =====
def analyze_stock(hist):
    close = hist["Close"]
    volume = hist["Volume"]

    if len(hist) < 60:
        return [], 0, 0

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    vma5 = volume.rolling(5).mean()
    vma20 = volume.rolling(20).mean()

    price = close.iloc[-1]

    change_5d = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100
    change_20d = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100

    signals = []
    score = 0

    # ===== 1. 趨勢判斷 =====
    if ma5.iloc[-1] > ma20.iloc[-1]:
        signals.append("短線多頭")
        score += 15

    if ma20.iloc[-1] > ma60.iloc[-1]:
        signals.append("中期多頭")
        score += 20

    if price > ma20.iloc[-1]:
        signals.append("站上月線")
        score += 15

    if price > ma60.iloc[-1]:
        signals.append("站上季線")
        score += 15

    # ===== 2. 動能判斷 =====
    if 2 <= change_5d <= 12:
        signals.append("動能健康")
        score += 15

    if change_20d > 5:
        signals.append("波段轉強")
        score += 15

    # ===== 3. 量能判斷 =====
    if vma5.iloc[-1] > vma20.iloc[-1] * 1.3:
        signals.append("量能放大")
        score += 20

    # ===== 4. 風險控管：避免追高 =====
    if change_5d > 15:
        signals.append("短線過熱")
        score -= 25

    if price < ma20.iloc[-1]:
        signals.append("跌破月線")
        score -= 20

    return signals, round(change_5d, 2), score


# ===== 大盤狀態 =====
def get_market_status():
    try:
        twii = yf.download("^TWII", period="3mo", progress=False)

        if len(twii) < 60:
            return "資料不足", 0

        close = twii["Close"]
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()

        if close.iloc[-1] > ma20.iloc[-1] and ma20.iloc[-1] > ma60.iloc[-1]:
            return "多頭市場", 15
        elif close.iloc[-1] < ma20.iloc[-1]:
            return "大盤偏弱", -20
        else:
            return "盤整市場", 0

    except Exception:
        return "無法判斷", 0


# ===== 掃描市場 =====
def scan_market():
    stocks = get_full_market_list()
    symbols = list(stocks.keys())[:SCAN_LIMIT]

    market_status, market_score = get_market_status()

    try:
        data = yf.download(
            symbols,
            period="6mo",
            group_by="ticker",
            threads=False,
            progress=False
        )
    except Exception:
        return []

    results = []

    for sym in symbols:
        try:
            if sym not in data:
                continue

            hist = data[sym].dropna()

            if len(hist) < 60:
                continue

            signals, change, score = analyze_stock(hist)

            total_score = score + market_score

            if total_score >= 60:
                results.append({
                    "symbol": sym,
                    "name": stocks[sym],
                    "price": round(hist["Close"].iloc[-1], 2),
                    "change": change,
                    "score": total_score,
                    "market_status": market_status,
                    "signals": signals
                })

        except Exception:
            continue

    return sorted(results, key=lambda x: x["score"], reverse=True)


# ===== 指數 =====
def get_index():
    try:
        twii = yf.Ticker("^TWII").fast_info["last_price"]
        otc = yf.Ticker("^TWOII").fast_info["last_price"]
        return round(twii, 0), round(otc, 2)
    except Exception:
        return "-", "-"


# ===== 追蹤資料 =====
def load_track():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_track(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===== 績效統計 =====
def calc_stats(tracks):
    win = 0
    total = 0
    total_return = 0

    for t in tracks:
        if t.get("pnl") != "-":
            total += 1
            total_return += t["pnl"]

            if t["pnl"] > 0:
                win += 1

    winrate = round(win / total * 100, 2) if total > 0 else 0
    avg = round(total_return / total, 2) if total > 0 else 0

    return winrate, avg


# ===== 首頁 =====
@app.route("/")
def index():
    recs = []

    if request.args.get("scan") == "true":
        recs = scan_market()

    twii, otc = get_index()
    market_status, market_score = get_market_status()

    tracks = load_track()

    for t in tracks:
        try:
            curr = yf.Ticker(t["symbol"]).fast_info["last_price"]
            pnl = (curr - t["price"]) / t["price"] * 100

            t["curr"] = round(curr, 2)
            t["pnl"] = round(pnl, 2)

            # ===== 進出場策略 =====
            if pnl <= -5:
                t["signal"] = "停損"
            elif pnl >= 20:
                t["signal"] = "強制停利"
            elif pnl >= 10:
                t["signal"] = "可分批停利"
            elif pnl <= -3:
                t["signal"] = "警戒"
            else:
                t["signal"] = "持有"

        except Exception:
            t["curr"] = "-"
            t["pnl"] = "-"
            t["signal"] = "-"

    winrate, avg = calc_stats(tracks)

    return render_template(
        "index.html",
        recs=recs,
        twii=twii,
        otc=otc,
        market_status=market_status,
        market_score=market_score,
        tracks=tracks,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        winrate=winrate,
        avg=avg
    )


# ===== 加入追蹤 =====
@app.route("/track/<symbol>/<name>/<price>")
def track(symbol, name, price):
    data = load_track()

    exists = any(x["symbol"] == symbol for x in data)

    if not exists:
        data.append({
            "symbol": symbol,
            "name": name,
            "price": float(price),
            "date": datetime.now().strftime("%Y-%m-%d")
        })

    save_track(data)
    return redirect(url_for("index"))


# ===== 刪除追蹤 =====
@app.route("/untrack/<symbol>")
def untrack(symbol):
    data = [x for x in load_track() if x["symbol"] != symbol]
    save_track(data)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
