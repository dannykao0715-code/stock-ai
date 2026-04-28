import os
import json
import time
import pandas as pd
import yfinance as yf

from flask import Flask, render_template, redirect, url_for
from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

TAIWAN_TZ = ZoneInfo("Asia/Taipei")

RESULT_FILE = "scan_results.json"
TRACK_FILE = "track.json"

MIN_SCORE = 70


# ======================
# 時間
# ======================
def taiwan_now():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ======================
# 股票池：上市 + 上櫃
# ======================
def get_stock_pool():
    market = {}

    try:
        # 上市
        tse = pd.read_html("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2")[0]
        tse = tse[tse[0].astype(str).str.contains(r"^\d{4}", na=False)]

        for item in tse[0]:
            try:
                parts = str(item).split()
                code = parts[0]
                name = parts[1]

                if len(code) == 4 and code.isdigit():
                    market[f"{code}.TW"] = name
            except Exception:
                continue

        # 上櫃
        otc = pd.read_html("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4")[0]
        otc = otc[otc[0].astype(str).str.contains(r"^\d{4}", na=False)]

        for item in otc[0]:
            try:
                parts = str(item).split()
                code = parts[0]
                name = parts[1]

                if len(code) == 4 and code.isdigit():
                    market[f"{code}.TWO"] = name
            except Exception:
                continue

        return market

    except Exception:
        return {
            "2330.TW": "台積電",
            "2317.TW": "鴻海",
            "2454.TW": "聯發科",
            "2308.TW": "台達電",
            "2382.TW": "廣達"
        }


# ======================
# 下載資料
# ======================
def download_stock(symbol, period="1y"):
    try:
        df = yf.download(
            symbol,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=False
        )

        if df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df.dropna()

    except Exception:
        return None


def safe_float(x):
    try:
        if hasattr(x, "iloc"):
            x = x.iloc[0]
        return float(x)
    except Exception:
        return None


# ======================
# 指數
# ======================
def get_index():
    def fetch(symbol):
        df = download_stock(symbol, "5d")
        if df is None or df.empty:
            return "-"
        price = safe_float(df["Close"].iloc[-1])
        return round(price, 2) if price else "-"

    return fetch("^TWII"), fetch("^TWOII")


# ======================
# 大盤濾網
# ======================
def get_market_status():
    df = download_stock("^TWII", "1y")

    if df is None or len(df) < 120:
        return "資料不足", 0, "防守"

    close = df["Close"]

    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()

    last = close.iloc[-1]

    if last > ma20.iloc[-1] > ma60.iloc[-1] > ma120.iloc[-1]:
        return "強多市場", 20, "積極"
    elif last > ma60.iloc[-1] and ma20.iloc[-1] > ma60.iloc[-1]:
        return "多頭市場", 10, "正常"
    elif last < ma20.iloc[-1] and ma20.iloc[-1] < ma60.iloc[-1]:
        return "空頭市場", -30, "防守"
    else:
        return "盤整市場", -5, "保守"


# ======================
# ATR
# ======================
def calc_atr(df, period=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        abs(high - prev_close),
        abs(low - prev_close)
    ], axis=1).max(axis=1)

    return tr.rolling(period).mean()


# ======================
# 核心策略
# ======================
def analyze_stock(df):
    if df is None or len(df) < 120:
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    price = safe_float(close.iloc[-1])
    if not price:
        return None

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()

    vma5 = volume.rolling(5).mean()
    vma20 = volume.rolling(20).mean()

    atr = calc_atr(df)
    atr_now = safe_float(atr.iloc[-1])

    change_5d = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100
    change_20d = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100
    change_60d = (close.iloc[-1] - close.iloc[-60]) / close.iloc[-60] * 100

    high_20 = high.rolling(20).max().iloc[-2]
    high_60 = high.rolling(60).max().iloc[-2]
    low_60 = low.rolling(60).min().iloc[-1]

    signals = []
    warnings = []
    score = 0

    # 趨勢
    if price > ma20.iloc[-1]:
        signals.append("站上月線")
        score += 10

    if price > ma60.iloc[-1]:
        signals.append("站上季線")
        score += 10

    if ma20.iloc[-1] > ma60.iloc[-1]:
        signals.append("月線大於季線")
        score += 10

    if ma20.iloc[-1] > ma60.iloc[-1] > ma120.iloc[-1]:
        signals.append("中長期多頭排列")
        score += 20

    # 主力吸籌型態
    spread = abs(ma20.iloc[-1] - ma60.iloc[-1]) / ma60.iloc[-1]

    if spread < 0.06 and price > ma20.iloc[-1]:
        signals.append("均線收斂後轉強")
        score += 15

    # 突破
    if price > high_20:
        signals.append("突破20日高點")
        score += 20

    if price > high_60:
        signals.append("突破60日高點")
        score += 25

    # 量能
    if vma5.iloc[-1] > vma20.iloc[-1] * 1.2:
        signals.append("量能增溫")
        score += 15

    if vma5.iloc[-1] > vma20.iloc[-1] * 1.6:
        signals.append("主力放量")
        score += 20

    # 動能
    if 1 <= change_5d <= 12:
        signals.append("短線動能健康")
        score += 15

    if change_20d > 5:
        signals.append("波段轉強")
        score += 15

    if change_60d > 10:
        signals.append("中期趨勢轉強")
        score += 10

    # 位置
    position_from_low = (price - low_60) / low_60 * 100

    if 5 <= position_from_low <= 35:
        signals.append("低位啟動區")
        score += 15

    # 避免追高
    if change_5d > 18:
        warnings.append("5日漲幅過熱")
        score -= 30

    if change_20d > 35:
        warnings.append("20日漲幅過熱")
        score -= 25

    if price < ma20.iloc[-1]:
        warnings.append("跌破月線")
        score -= 25

    atr_pct = 0

    if atr_now and price:
        atr_pct = atr_now / price * 100

    if atr_pct > 8:
        warnings.append("波動過大")
        score -= 15

    stop_loss = None
    take_profit_1 = None
    take_profit_2 = None

    if atr_now:
        stop_loss = round(price - atr_now * 2, 2)
        take_profit_1 = round(price + atr_now * 3, 2)
        take_profit_2 = round(price + atr_now * 5, 2)

    return {
        "price": round(price, 2),
        "score": round(score, 1),
        "change_5d": round(float(change_5d), 2),
        "change_20d": round(float(change_20d), 2),
        "signals": signals,
        "warnings": warnings,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "atr_pct": round(float(atr_pct), 2)
    }


# ======================
# 全市場掃描
# ======================
def scan_market():
    print("開始全市場掃描：", taiwan_now())

    stocks = get_stock_pool()
    market_status, market_score, risk_mode = get_market_status()

    results = []
    total = len(stocks)

    for i, (symbol, name) in enumerate(stocks.items(), start=1):
        try:
            df = download_stock(symbol, "1y")
            result = analyze_stock(df)

            if not result:
                continue

            total_score = result["score"] + market_score

            if total_score >= MIN_SCORE:
                results.append({
                    "symbol": symbol,
                    "name": name,
                    "price": result["price"],
                    "score": round(total_score, 1),
                    "raw_score": result["score"],
                    "change_5d": result["change_5d"],
                    "change_20d": result["change_20d"],
                    "signals": result["signals"],
                    "warnings": result["warnings"],
                    "stop_loss": result["stop_loss"],
                    "take_profit_1": result["take_profit_1"],
                    "take_profit_2": result["take_profit_2"],
                    "atr_pct": result["atr_pct"],
                    "market_status": market_status,
                    "risk_mode": risk_mode
                })

            if i % 100 == 0:
                print(f"已掃描 {i}/{total}")

            time.sleep(0.05)

        except Exception:
            continue

    results = sorted(results, key=lambda x: x["score"], reverse=True)

    save_scan_results({
        "updated_at": taiwan_now(),
        "market_status": market_status,
        "market_score": market_score,
        "risk_mode": risk_mode,
        "count": len(results),
        "results": results
    })

    print("掃描完成：", taiwan_now(), "共", len(results), "檔")

    return results


# ======================
# 掃描結果存取
# ======================
def save_scan_results(data):
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_scan_results():
    if os.path.exists(RESULT_FILE):
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    return {
        "updated_at": "尚未掃描",
        "market_status": "尚未掃描",
        "market_score": 0,
        "risk_mode": "-",
        "count": 0,
        "results": []
    }


# ======================
# 追蹤
# ======================
def load_track():
    if os.path.exists(TRACK_FILE):
        with open(TRACK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_track(data):
    with open(TRACK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calc_stats(tracks):
    valid = [t for t in tracks if t.get("pnl") != "-"]

    if not valid:
        return 0, 0

    wins = [t for t in valid if t["pnl"] > 0]
    avg = sum(t["pnl"] for t in valid) / len(valid)

    return round(len(wins) / len(valid) * 100, 2), round(avg, 2)


# ======================
# 首頁：不用按掃描，直接讀結果
# ======================
@app.route("/")
def index():
    scan_data = load_scan_results()
    recs = scan_data["results"]

    twii, otc = get_index()
    tracks = load_track()

    for t in tracks:
        df = download_stock(t["symbol"], "5d")

        try:
            curr = safe_float(df["Close"].iloc[-1])
            pnl = (curr - t["price"]) / t["price"] * 100

            t["curr"] = round(curr, 2)
            t["pnl"] = round(pnl, 2)

            if t.get("stop_loss") and curr <= t["stop_loss"]:
                t["signal"] = "停損"
            elif t.get("take_profit_2") and curr >= t["take_profit_2"]:
                t["signal"] = "第二階段停利"
            elif t.get("take_profit_1") and curr >= t["take_profit_1"]:
                t["signal"] = "第一階段停利"
            elif pnl <= -5:
                t["signal"] = "風險警戒"
            elif pnl >= 10:
                t["signal"] = "可分批獲利"
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
        market_status=scan_data["market_status"],
        market_score=scan_data["market_score"],
        risk_mode=scan_data["risk_mode"],
        scan_updated_at=scan_data["updated_at"],
        scan_count=scan_data["count"],
        tracks=tracks,
        winrate=winrate,
        avg=avg,
        now=taiwan_now()
    )


# 手動立即掃描
@app.route("/scan-now")
def scan_now():
    scan_market()
    return redirect(url_for("index"))


@app.route("/track/<symbol>/<name>/<price>/<stop_loss>/<take1>/<take2>")
def track(symbol, name, price, stop_loss, take1, take2):
    data = load_track()

    exists = any(x["symbol"] == symbol for x in data)

    if not exists:
        data.append({
            "symbol": symbol,
            "name": name,
            "price": float(price),
            "stop_loss": float(stop_loss),
            "take_profit_1": float(take1),
            "take_profit_2": float(take2),
            "date": datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
        })

    save_track(data)
    return redirect(url_for("index"))


@app.route("/untrack/<symbol>")
def untrack(symbol):
    data = [x for x in load_track() if x["symbol"] != symbol]
    save_track(data)
    return redirect(url_for("index"))


# ======================
# 背景任務：每天 16:00 自動掃描
# ======================
scheduler = BackgroundScheduler(timezone=TAIWAN_TZ)

scheduler.add_job(
    scan_market,
    trigger="cron",
    hour=16,
    minute=0,
    id="daily_market_scan",
    replace_existing=True
)

scheduler.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
