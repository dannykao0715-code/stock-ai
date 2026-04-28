import os
import json
import time
import threading
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
STOCK_POOL_FILE = "stock_pool.json"
SCAN_STATUS_FILE = "scan_status.json"

MIN_SCORE = 70
is_scanning = False


# ======================
# 時間
# ======================
def taiwan_now():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ======================
# 掃描狀態
# ======================
def save_scan_status(status, message):
    data = {
        "status": status,
        "message": message,
        "updated_at": taiwan_now()
    }

    with open(SCAN_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_scan_status():
    if os.path.exists(SCAN_STATUS_FILE):
        try:
            with open(SCAN_STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "status": "idle",
        "message": "尚未掃描",
        "updated_at": "-"
    }


# ======================
# 保底股票池：產業龍頭 + 子公司 + 關係產業
# ======================
def get_fallback_stock_pool():
    return {
        "2330.TW": "台積電",
        "2303.TW": "聯電",
        "5347.TWO": "世界",
        "6770.TW": "力積電",
        "2454.TW": "聯發科",
        "3034.TW": "聯詠",
        "2379.TW": "瑞昱",
        "3661.TW": "世芯-KY",
        "3443.TW": "創意",
        "3529.TWO": "力旺",
        "4966.TWO": "譜瑞-KY",
        "5274.TWO": "信驊",

        "3711.TW": "日月光投控",
        "6147.TWO": "頎邦",
        "2449.TW": "京元電子",
        "2337.TW": "旺宏",
        "2408.TW": "南亞科",
        "6488.TWO": "環球晶",
        "5483.TWO": "中美晶",

        "2317.TW": "鴻海",
        "2382.TW": "廣達",
        "3231.TW": "緯創",
        "2356.TW": "英業達",
        "6669.TW": "緯穎",
        "2308.TW": "台達電",
        "2357.TW": "華碩",
        "2376.TW": "技嘉",
        "3017.TW": "奇鋐",
        "3324.TWO": "雙鴻",
        "3653.TW": "健策",
        "8996.TWO": "高力",
        "2345.TW": "智邦",

        "3008.TW": "大立光",
        "3406.TW": "玉晶光",
        "2383.TW": "台光電",
        "3037.TW": "欣興",
        "8046.TW": "南電",
        "3189.TWO": "景碩",

        "2409.TW": "友達",
        "3481.TW": "群創",
        "8069.TWO": "元太",

        "2881.TW": "富邦金",
        "2882.TW": "國泰金",
        "2886.TW": "兆豐金",
        "2891.TW": "中信金",
        "2884.TW": "玉山金",
        "2885.TW": "元大金",
        "5880.TW": "合庫金",
        "5871.TW": "中租-KY",

        "1301.TW": "台塑",
        "1303.TW": "南亞",
        "1326.TW": "台化",
        "6505.TW": "台塑化",
        "2002.TW": "中鋼",
        "2027.TW": "大成鋼",
        "1605.TW": "華新",

        "2603.TW": "長榮",
        "2609.TW": "陽明",
        "2615.TW": "萬海",
        "2618.TW": "長榮航",
        "2610.TW": "華航",
        "2606.TW": "裕民",

        "6446.TW": "藥華藥",
        "1760.TW": "寶齡富錦",
        "4743.TWO": "合一",
        "4105.TWO": "東洋",
        "6472.TW": "保瑞",

        "2412.TW": "中華電",
        "3045.TW": "台灣大",
        "4904.TW": "遠傳",
        "1216.TW": "統一",
        "2912.TW": "統一超",
        "2207.TW": "和泰車",

        "1504.TW": "東元",
        "1513.TW": "中興電",
        "1519.TW": "華城",
        "1609.TW": "大亞",

        "8299.TWO": "群聯",
        "3105.TWO": "穩懋",
        "6187.TWO": "萬潤",
        "3260.TWO": "威剛",
        "8086.TWO": "宏捷科"
    }


# ======================
# 股票池快取
# ======================
def save_stock_pool(market):
    data = {
        "updated_at": taiwan_now(),
        "count": len(market),
        "stocks": market
    }

    with open(STOCK_POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_stock_pool_cache():
    if os.path.exists(STOCK_POOL_FILE):
        try:
            with open(STOCK_POOL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            stocks = data.get("stocks", {})

            if stocks and len(stocks) > 100:
                print("使用快取股票池，共", len(stocks), "檔")
                return stocks

        except Exception as e:
            print("讀取股票池快取失敗：", e)

    return None


def get_stock_pool():
    market = {}

    try:
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

        if len(market) > 1000:
            save_stock_pool(market)
            print("全市場股票池更新成功，共", len(market), "檔")
            return market

        cache = load_stock_pool_cache()
        if cache:
            return cache

    except Exception as e:
        print("股票池更新失敗：", e)

        cache = load_stock_pool_cache()
        if cache:
            return cache

    print("使用產業龍頭保底股票池")
    return get_fallback_stock_pool()


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

    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()

    vma5 = volume.rolling(5).mean()
    vma20 = volume.rolling(20).mean()

    atr = calc_atr(df)
    atr_now = safe_float(atr.iloc[-1])

    change_5d = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100
    change_20d = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100

    high_20 = high.rolling(20).max().iloc[-2]
    high_60 = high.rolling(60).max().iloc[-2]
    low_60 = low.rolling(60).min().iloc[-1]

    signals = []
    warnings = []
    score = 0

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

    spread = abs(ma20.iloc[-1] - ma60.iloc[-1]) / ma60.iloc[-1]

    if spread < 0.06 and price > ma20.iloc[-1]:
        signals.append("均線收斂後轉強")
        score += 15

    if price > high_20:
        signals.append("突破20日高點")
        score += 20

    if price > high_60:
        signals.append("突破60日高點")
        score += 25

    if vma5.iloc[-1] > vma20.iloc[-1] * 1.2:
        signals.append("量能增溫")
        score += 15

    if vma5.iloc[-1] > vma20.iloc[-1] * 1.6:
        signals.append("主力放量")
        score += 20

    if 1 <= change_5d <= 12:
        signals.append("短線動能健康")
        score += 15

    if change_20d > 5:
        signals.append("波段轉強")
        score += 15

    position_from_low = (price - low_60) / low_60 * 100

    if 5 <= position_from_low <= 35:
        signals.append("低位啟動區")
        score += 15

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
# 掃描結果
# ======================
def save_scan_results(data):
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_scan_results():
    if os.path.exists(RESULT_FILE):
        try:
            with open(RESULT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "updated_at": "尚未掃描",
        "market_status": "尚未掃描",
        "market_score": 0,
        "risk_mode": "-",
        "stock_pool_count": 0,
        "count": 0,
        "results": []
    }


# ======================
# 全市場掃描
# ======================
def scan_market():
    save_scan_status("running", "正在背景掃描全市場，請稍後重新整理。")
    print("開始掃描：", taiwan_now())

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
                save_scan_status("running", f"正在掃描全市場：{i}/{total}")
                print(f"已掃描 {i}/{total}")

            time.sleep(0.05)

        except Exception as e:
            print("單檔掃描失敗：", symbol, e)
            continue

    results = sorted(results, key=lambda x: x["score"], reverse=True)

    save_scan_results({
        "updated_at": taiwan_now(),
        "market_status": market_status,
        "market_score": market_score,
        "risk_mode": risk_mode,
        "stock_pool_count": total,
        "count": len(results),
        "results": results
    })

    save_scan_status("done", f"掃描完成：股票池 {total} 檔，符合條件 {len(results)} 檔。")
    print("掃描完成：", taiwan_now(), "股票池", total, "檔，符合", len(results), "檔")


# ======================
# 追蹤
# ======================
def load_track():
    if os.path.exists(TRACK_FILE):
        try:
            with open(TRACK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
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
# 首頁
# ======================
@app.route("/")
def index():
    scan_data = load_scan_results()
    scan_status_data = load_scan_status()

    recs = scan_data.get("results", [])

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
        market_status=scan_data.get("market_status", "尚未掃描"),
        market_score=scan_data.get("market_score", 0),
        risk_mode=scan_data.get("risk_mode", "-"),
        scan_updated_at=scan_data.get("updated_at", "尚未掃描"),
        scan_count=scan_data.get("count", 0),
        stock_pool_count=scan_data.get("stock_pool_count", 0),
        scan_status=scan_status_data.get("status", "idle"),
        scan_message=scan_status_data.get("message", "尚未掃描"),
        scan_status_time=scan_status_data.get("updated_at", "-"),
        tracks=tracks,
        winrate=winrate,
        avg=avg,
        now=taiwan_now()
    )


# ======================
# 手動背景掃描
# ======================
@app.route("/scan-now")
def scan_now():
    global is_scanning

    if is_scanning:
        return redirect(url_for("index"))

    def run_scan():
        global is_scanning
        try:
            is_scanning = True
            scan_market()
        except Exception as e:
            save_scan_status("error", f"掃描失敗：{e}")
            print("掃描失敗：", e)
        finally:
            is_scanning = False

    threading.Thread(target=run_scan, daemon=True).start()

    return redirect(url_for("index"))


# ======================
# 加入追蹤
# ======================
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
# 每天 16:00 自動背景掃描
# ======================
def scheduled_scan():
    global is_scanning

    if is_scanning:
        return

    is_scanning = True

    try:
        scan_market()
    except Exception as e:
        save_scan_status("error", f"排程掃描失敗：{e}")
        print("排程掃描失敗：", e)
    finally:
        is_scanning = False


scheduler = BackgroundScheduler(timezone=TAIWAN_TZ)

scheduler.add_job(
    scheduled_scan,
    trigger="cron",
    hour=16,
    minute=0,
    id="daily_market_scan",
    replace_existing=True
)

scheduler.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
