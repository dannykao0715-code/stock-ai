# main.py
# -*- coding: utf-8 -*-

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import time
import traceback


app = FastAPI(
    title="AI Stock Selection System",
    version="final-template-strategy-2026"
)

templates = Jinja2Templates(directory="templates")

TAIWAN_TZ = ZoneInfo("Asia/Taipei")

CACHE = {
    "updated_at": None,
    "data": None,
    "raw_count": 0,
    "scan_seconds": 0,
}

CACHE_TTL_SECONDS = 60 * 20
MAX_WORKERS = 10
REQUEST_TIMEOUT = 8


# =========================================================
# 1. 備援股票池
# =========================================================

FALLBACK_STOCKS = [
    # 權值 / 半導體
    ("2330", "台積電", "TW"),
    ("2317", "鴻海", "TW"),
    ("2454", "聯發科", "TW"),
    ("2303", "聯電", "TW"),
    ("2308", "台達電", "TW"),
    ("2382", "廣達", "TW"),
    ("2357", "華碩", "TW"),
    ("2327", "國巨", "TW"),
    ("3711", "日月光投控", "TW"),
    ("3034", "聯詠", "TW"),
    ("2379", "瑞昱", "TW"),
    ("3443", "創意", "TW"),
    ("3661", "世芯-KY", "TW"),
    ("2360", "致茂", "TW"),
    ("3533", "嘉澤", "TW"),
    ("4966", "譜瑞-KY", "TW"),

    # AI / 伺服器 / 散熱 / 電源
    ("6669", "緯穎", "TW"),
    ("3231", "緯創", "TW"),
    ("3017", "奇鋐", "TW"),
    ("3324", "雙鴻", "TWO"),
    ("3653", "健策", "TW"),
    ("2356", "英業達", "TW"),
    ("4938", "和碩", "TW"),
    ("2383", "台光電", "TW"),
    ("2368", "金像電", "TW"),
    ("6274", "台燿", "TWO"),
    ("8299", "群聯", "TWO"),
    ("6415", "矽力*-KY", "TW"),
    ("6121", "新普", "TW"),

    # 金融
    ("2881", "富邦金", "TW"),
    ("2882", "國泰金", "TW"),
    ("2884", "玉山金", "TW"),
    ("2885", "元大金", "TW"),
    ("2886", "兆豐金", "TW"),
    ("2891", "中信金", "TW"),
    ("2892", "第一金", "TW"),
    ("5880", "合庫金", "TW"),

    # 傳產 / 航運 / 營建
    ("2603", "長榮", "TW"),
    ("2609", "陽明", "TW"),
    ("2615", "萬海", "TW"),
    ("2618", "長榮航", "TW"),
    ("1605", "華新", "TW"),
    ("2002", "中鋼", "TW"),
    ("1301", "台塑", "TW"),
    ("1303", "南亞", "TW"),
    ("1402", "遠東新", "TW"),
    ("2542", "興富發", "TW"),
    ("2515", "中工", "TW"),

    # 生技 / 其他
    ("6446", "藥華藥", "TW"),
    ("1795", "美時", "TW"),
    ("4743", "合一", "TWO"),
    ("6547", "高端疫苗", "TWO"),
]


# =========================================================
# 2. 股票池
# =========================================================

def fetch_twse_listed_stocks():
    """
    抓上市股票清單。
    失敗時不讓系統掛掉，直接回傳空陣列。
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    stocks = []

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        for item in data:
            code = str(
                item.get("Code")
                or item.get("證券代號")
                or item.get("STOCK_CODE")
                or ""
            ).strip()

            name = str(
                item.get("Name")
                or item.get("證券名稱")
                or item.get("STOCK_NAME")
                or ""
            ).strip()

            if code.isdigit() and len(code) == 4:
                stocks.append((code, name or code, "TW"))

    except Exception:
        pass

    return stocks


def fetch_tpex_stocks():
    """
    抓上櫃股票清單。
    API 若失敗，直接回傳空陣列。
    """
    urls = [
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
        "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics",
    ]

    stocks = []

    for url in urls:
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()

            for item in data:
                code = str(
                    item.get("SecuritiesCompanyCode")
                    or item.get("代號")
                    or item.get("Code")
                    or item.get("股票代號")
                    or ""
                ).strip()

                name = str(
                    item.get("CompanyName")
                    or item.get("名稱")
                    or item.get("Name")
                    or item.get("股票名稱")
                    or ""
                ).strip()

                if code.isdigit() and len(code) == 4:
                    stocks.append((code, name or code, "TWO"))

        except Exception:
            continue

    return stocks


def get_stock_pool():
    """
    優先抓全市場。
    若全市場資料失敗，使用備援股票池，避免網站整個不能用。
    """
    twse = fetch_twse_listed_stocks()
    tpex = fetch_tpex_stocks()

    pool = []
    seen = set()

    for code, name, market in twse + tpex:
        key = (code, market)
        if key not in seen:
            seen.add(key)
            pool.append((code, name, market))

    if len(pool) < 100:
        fallback = []
        seen = set()

        for code, name, market in FALLBACK_STOCKS:
            if not str(code).isdigit():
                continue

            key = (code, market)
            if key not in seen:
                seen.add(key)
                fallback.append((code, name, market))

        return fallback

    return pool


# =========================================================
# 3. K 線資料
# =========================================================

def yahoo_symbol(code, market):
    if market == "TWO":
        return f"{code}.TWO"
    return f"{code}.TW"


def fetch_candles_from_yahoo(code, market, days=420):
    """
    從 Yahoo Finance 抓日 K。
    """
    symbol = yahoo_symbol(code, market)
    now = int(time.time())
    past = now - days * 24 * 60 * 60

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={past}&period2={now}&interval=1d&events=history&includeAdjustedClose=true"
    )

    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    result = data.get("chart", {}).get("result", [])
    if not result:
        return []

    result = result[0]
    timestamps = result.get("timestamp", [])
    quote = result.get("indicators", {}).get("quote", [{}])[0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    candles = []

    for i, ts in enumerate(timestamps):
        try:
            open_price = opens[i]
            high_price = highs[i]
            low_price = lows[i]
            close_price = closes[i]
            volume = volumes[i]

            if open_price is None or high_price is None or low_price is None or close_price is None:
                continue

            candles.append({
                "date": datetime.fromtimestamp(ts, TAIWAN_TZ).strftime("%Y-%m-%d"),
                "open": float(open_price),
                "high": float(high_price),
                "low": float(low_price),
                "close": float(close_price),
                "volume": int(volume or 0),
            })

        except Exception:
            continue

    return candles


# =========================================================
# 4. 技術指標
# =========================================================

def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(-period, 0):
        diff = values[i] - values[i - 1]

        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def pct(current, base):
    if base is None or base == 0:
        return 0
    return (current - base) / base * 100


def safe_round(value, digits=2):
    if value is None:
        return None

    try:
        return round(float(value), digits)
    except Exception:
        return None


# =========================================================
# 5. 策略：突破不追高
# =========================================================

def find_breakout(candles):
    """
    找最近是否突破 20 日或 60 日前高。
    注意：突破不是直接買，而是標示後等待回採。
    """
    if len(candles) < 90:
        return None

    closes = [x["close"] for x in candles]
    highs = [x["high"] for x in candles]

    search_start = max(61, len(candles) - 25)
    breakout_events = []

    for i in range(search_start, len(candles)):
        prev20_high = max(highs[i - 20:i])
        prev60_high = max(highs[i - 60:i])
        close = closes[i]

        if close > prev60_high:
            breakout_events.append({
                "index": i,
                "date": candles[i]["date"],
                "type": "突破60日前高",
                "level": prev60_high,
                "strength": 60,
            })

        elif close > prev20_high:
            breakout_events.append({
                "index": i,
                "date": candles[i]["date"],
                "type": "突破20日前高",
                "level": prev20_high,
                "strength": 20,
            })

    if not breakout_events:
        return None

    return breakout_events[-1]


def evaluate_pullback(candles, breakout):
    """
    突破後評估是否回採前高支撐。
    """
    last = candles[-1]
    current_price = last["close"]
    support = breakout["level"]
    breakout_index = breakout["index"]

    after_breakout = candles[breakout_index:]
    if not after_breakout:
        return None

    lowest_after = min(x["low"] for x in after_breakout)

    distance_to_support = pct(current_price, support)
    low_break_pct = pct(lowest_after, support)

    near_support = -2.5 <= distance_to_support <= 6.0
    not_broken = low_break_pct >= -3.0
    too_far = distance_to_support > 10.0

    if too_far:
        status = "等回採"
        score = 8

    elif near_support and not_broken:
        status = "可觀察進場"
        score = 22

    elif not_broken:
        status = "回採未破"
        score = 16

    else:
        status = "跌破支撐排除"
        score = -30

    return {
        "status": status,
        "support": support,
        "distance_to_support": distance_to_support,
        "lowest_after_breakout": lowest_after,
        "low_break_pct": low_break_pct,
        "score": score,
        "near_support": near_support,
        "not_broken": not_broken,
        "too_far": too_far,
    }


# =========================================================
# 6. 雞蛋位階
# =========================================================

def evaluate_egg_position(candles):
    """
    雞蛋位階：
    偏好低檔轉強、中段健康，不追高位階。
    """
    if len(candles) < 120:
        return {
            "score": 0,
            "position": None,
            "label": "資料不足"
        }

    close = candles[-1]["close"]
    low120 = min(x["low"] for x in candles[-120:])
    high120 = max(x["high"] for x in candles[-120:])

    if high120 == low120:
        return {
            "score": 0,
            "position": None,
            "label": "區間不足"
        }

    position = (close - low120) / (high120 - low120)

    if 0.25 <= position <= 0.55:
        score = 18
        label = "雞蛋甜蜜位階"

    elif 0.55 < position <= 0.72:
        score = 12
        label = "中段偏強"

    elif 0.12 <= position < 0.25:
        score = 8
        label = "低檔剛轉強"

    elif position > 0.82:
        score = -22
        label = "高位階風險"

    else:
        score = -8
        label = "位階不佳"

    return {
        "score": score,
        "position": position,
        "label": label,
    }


# =========================================================
# 7. K 棒確認
# =========================================================

def evaluate_candle_confirmation(candles):
    """
    K 棒確認：
    紅 K、收近高點、下影線支撐、量能溫和放大。
    """
    if len(candles) < 25:
        return {
            "score": 0,
            "label": "資料不足"
        }

    last = candles[-1]

    open_price = last["open"]
    high_price = last["high"]
    low_price = last["low"]
    close_price = last["close"]

    body = abs(close_price - open_price)
    full_range = max(high_price - low_price, 0.0001)

    upper_shadow = high_price - max(close_price, open_price)
    lower_shadow = min(close_price, open_price) - low_price

    close_position = (close_price - low_price) / full_range

    red_k = close_price > open_price
    close_near_high = close_position >= 0.65
    lower_support = lower_shadow > upper_shadow and lower_shadow >= body * 0.45

    volumes = [x["volume"] for x in candles]
    avg_vol20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else 0
    vol_ratio = last["volume"] / avg_vol20 if avg_vol20 > 0 else 1

    score = 0
    labels = []

    if red_k:
        score += 7
        labels.append("紅K")

    if close_near_high:
        score += 6
        labels.append("收近高點")

    if lower_support:
        score += 5
        labels.append("下影線支撐")

    if 1.05 <= vol_ratio <= 2.5:
        score += 6
        labels.append("溫和放量")

    elif vol_ratio > 3.5:
        score -= 8
        labels.append("爆量風險")

    if not labels:
        labels.append("K棒尚未確認")

    return {
        "score": score,
        "label": "、".join(labels),
        "vol_ratio": vol_ratio,
    }


# =========================================================
# 8. 波段位置
# =========================================================

def evaluate_wave_position(candles):
    """
    波段位置：
    避免買在波段末端。
    偏好 MA20 > MA60、股價站上 MA20，但不要離 MA20 太遠。
    """
    if len(candles) < 80:
        return {
            "score": 0,
            "label": "資料不足"
        }

    closes = [x["close"] for x in candles]
    close = closes[-1]

    ma5 = sma(closes, 5)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)

    if not ma20 or not ma60:
        return {
            "score": 0,
            "label": "均線不足"
        }

    dist_ma20 = pct(close, ma20)

    score = 0
    labels = []

    if ma20 > ma60:
        score += 12
        labels.append("中期多方")
    else:
        score -= 10
        labels.append("中期未轉多")

    if close > ma20:
        score += 8
        labels.append("站上月線")
    else:
        score -= 6
        labels.append("跌破月線")

    if ma5 and ma5 > ma20:
        score += 5
        labels.append("短線轉強")

    if dist_ma20 > 15:
        score -= 18
        labels.append("離月線過遠")

    elif 0 <= dist_ma20 <= 8:
        score += 8
        labels.append("波段位置健康")

    return {
        "score": score,
        "label": "、".join(labels),
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "dist_ma20": dist_ma20,
    }


# =========================================================
# 9. 強度評分
# =========================================================

def evaluate_market_strength(candles):
    """
    近似評估個股資金推動力。
    """
    if len(candles) < 65:
        return {
            "score": 0,
            "label": "資料不足"
        }

    closes = [x["close"] for x in candles]
    volumes = [x["volume"] for x in candles]

    close = closes[-1]

    ret5 = pct(close, closes[-6])
    ret20 = pct(close, closes[-21])
    ret60 = pct(close, closes[-61])

    avg_vol5 = sum(volumes[-5:]) / 5
    avg_vol20 = sum(volumes[-25:-5]) / 20 if len(volumes) >= 25 else avg_vol5
    vol_trend = avg_vol5 / avg_vol20 if avg_vol20 > 0 else 1

    score = 0
    labels = []

    if ret20 > 5:
        score += 8
        labels.append("20日轉強")

    if ret60 > 8:
        score += 8
        labels.append("60日多方")

    if 1.0 <= vol_trend <= 2.4:
        score += 8
        labels.append("量能健康")

    elif vol_trend > 3.2:
        score -= 10
        labels.append("量能過熱")

    if ret5 < -8:
        score -= 8
        labels.append("短線轉弱")

    return {
        "score": score,
        "label": "、".join(labels) if labels else "中性",
        "ret5": ret5,
        "ret20": ret20,
        "ret60": ret60,
        "vol_trend": vol_trend,
    }


# =========================================================
# 10. 過熱排除
# =========================================================

def evaluate_overheat(candles):
    """
    過熱排除條件：
    只要符合任一高風險過熱條件，就不列入結果。
    """
    if len(candles) < 65:
        return {
            "overheated": False,
            "reasons": []
        }

    closes = [x["close"] for x in candles]
    volumes = [x["volume"] for x in candles]

    close = closes[-1]
    ma20 = sma(closes, 20)
    rsi14 = rsi(closes, 14)

    ret5 = pct(close, closes[-6])
    ret20 = pct(close, closes[-21])
    dist_ma20 = pct(close, ma20) if ma20 else 0

    avg_vol20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else 0
    vol_ratio = volumes[-1] / avg_vol20 if avg_vol20 > 0 else 1

    last = candles[-1]
    full_range = max(last["high"] - last["low"], 0.0001)
    upper_shadow = last["high"] - max(last["open"], last["close"])
    upper_ratio = upper_shadow / full_range

    reasons = []

    if ret5 >= 18:
        reasons.append("5日漲幅過大")

    if ret20 >= 35:
        reasons.append("20日漲幅過大")

    if dist_ma20 >= 18:
        reasons.append("乖離月線過大")

    if rsi14 and rsi14 >= 78:
        reasons.append("RSI過熱")

    if vol_ratio >= 4.0 and upper_ratio >= 0.35:
        reasons.append("爆量長上影")

    return {
        "overheated": len(reasons) > 0,
        "reasons": reasons,
        "ret5": ret5,
        "ret20": ret20,
        "dist_ma20": dist_ma20,
        "rsi14": rsi14,
        "vol_ratio": vol_ratio,
    }


# =========================================================
# 11. 單檔股票分析
# =========================================================

def analyze_stock(code, name, market):
    try:
        candles = fetch_candles_from_yahoo(code, market)

        if len(candles) < 90:
            return None

        last = candles[-1]

        overheat = evaluate_overheat(candles)
        if overheat["overheated"]:
            return None

        breakout = find_breakout(candles)
        if not breakout:
            return None

        pullback = evaluate_pullback(candles, breakout)
        if not pullback:
            return None

        if pullback["status"] == "跌破支撐排除":
            return None

        egg = evaluate_egg_position(candles)
        candle = evaluate_candle_confirmation(candles)
        wave = evaluate_wave_position(candles)
        strength = evaluate_market_strength(candles)

        breakout_score = 14 if breakout["strength"] == 60 else 10

        total_score = (
            breakout_score
            + pullback["score"]
            + egg["score"]
            + candle["score"]
            + wave["score"]
            + strength["score"]
        )

        risk_notes = []

        if egg["score"] < 0:
            risk_notes.append(egg["label"])

        if wave.get("dist_ma20") is not None and wave["dist_ma20"] > 12:
            risk_notes.append("短線偏離月線")

        if pullback["status"] == "等回採":
            total_score = min(total_score, 76)

        if total_score >= 82 and pullback["status"] != "等回採":
            grade = "S"

        elif total_score >= 68:
            grade = "A"

        else:
            return None

        if grade not in ["S", "A"]:
            return None

        if pullback["status"] == "等回採":
            action = "等回採，不追高"

        elif pullback["status"] == "可觀察進場":
            action = "可觀察進場"

        elif pullback["status"] == "回採未破":
            action = "觀察回採支撐"

        else:
            action = "觀察"

        return {
            "code": code,
            "name": name,
            "market": market,
            "date": last["date"],
            "price": safe_round(last["close"], 2),
            "grade": grade,
            "score": safe_round(total_score, 1),
            "action": action,
            "breakout_type": breakout["type"],
            "breakout_date": breakout["date"],
            "support": safe_round(pullback["support"], 2),
            "distance_to_support": safe_round(pullback["distance_to_support"], 2),
            "egg_position": safe_round(egg["position"] * 100, 1) if egg["position"] is not None else None,
            "egg_label": egg["label"],
            "candle_label": candle["label"],
            "wave_label": wave["label"],
            "strength_label": strength["label"],
            "ret5": safe_round(strength.get("ret5"), 2),
            "ret20": safe_round(strength.get("ret20"), 2),
            "ret60": safe_round(strength.get("ret60"), 2),
            "rsi14": safe_round(overheat.get("rsi14"), 2),
            "risk_notes": "、".join(risk_notes) if risk_notes else "無明顯過熱",
        }

    except Exception:
        return None


# =========================================================
# 12. 全市場掃描與快取
# =========================================================

def scan_market(force=False):
    now = datetime.now(TAIWAN_TZ)

    if not force and CACHE["data"] is not None and CACHE["updated_at"] is not None:
        age = (now - CACHE["updated_at"]).total_seconds()

        if age < CACHE_TTL_SECONDS:
            return CACHE["data"]

    start_time = time.time()

    pool = get_stock_pool()
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(analyze_stock, code, name, market): (code, name, market)
            for code, name, market in pool
        }

        for future in as_completed(futures):
            try:
                item = future.result()

                if item:
                    results.append(item)

            except Exception:
                continue

    results.sort(
        key=lambda x: (
            x["score"],
            -abs(x.get("distance_to_support") or 999)
        ),
        reverse=True
    )

    s_list = [x for x in results if x["grade"] == "S"][:10]
    a_list = [x for x in results if x["grade"] == "A"][:10]

    watch_list = [
        x for x in results
        if x["action"] in ["等回採，不追高", "觀察回採支撐", "可觀察進場"]
    ][:20]

    final_data = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "raw_count": len(pool),
        "result_count": len(results),
        "scan_seconds": round(time.time() - start_time, 2),
        "s_list": s_list,
        "a_list": a_list,
        "watch_list": watch_list,
        "all": results,
        "strategy": {
            "核心": "突破不追高 + 回採前高不破 + 雞蛋位階 + K棒確認 + 波段位置",
            "排除": "B級以下、短線過熱、乖離過大、RSI過熱、爆量長上影",
            "顯示": "S級前10、A級前10、觀察名單",
        }
    }

    CACHE["updated_at"] = now
    CACHE["data"] = final_data
    CACHE["raw_count"] = len(pool)
    CACHE["scan_seconds"] = final_data["scan_seconds"]

    return final_data


# =========================================================
# 13. Routes
# =========================================================

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request
        }
    )


@app.get("/recommendations")
def recommendations(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request
        }
    )


@app.get("/rescan")
def rescan():
    return RedirectResponse(url="/?force=true")


@app.get("/api/recommendations")
def api_recommendations(force: bool = Query(False)):
    try:
        data = scan_market(force=force)
        return JSONResponse(data)

    except Exception as e:
        return JSONResponse(
            {
                "error": str(e),
                "trace": traceback.format_exc(),
            },
            status_code=500
        )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "cache_updated_at": CACHE["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if CACHE["updated_at"] else None,
        "cache_has_data": CACHE["data"] is not None,
    }


# =========================================================
# 14. Local / Railway 啟動
# =========================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000
    )
