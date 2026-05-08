# main.py
# -*- coding: utf-8 -*-

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import statistics
import math
import time
import traceback

app = FastAPI(title="AI Stock Selection System", version="final-strategy-2026")

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
# 1. 股票池：優先抓全市場，失敗則用備援池
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
    ("Inventec", "英業達", "TW"),
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


def fetch_twse_listed_stocks():
    """
    抓上市股票清單。
    TWSE OpenAPI 欄位可能偶爾調整，因此用多欄位容錯。
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    stocks = []

    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()

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
    TPEx API 欄位可能調整，因此用容錯。
    """
    urls = [
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
        "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics",
    ]

    stocks = []

    for url in urls:
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()

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
    twse = fetch_twse_listed_stocks()
    tpex = fetch_tpex_stocks()

    pool = []
    seen = set()

    for code, name, market in twse + tpex:
        key = (code, market)
        if key not in seen:
            seen.add(key)
            pool.append((code, name, market))

    # 如果全市場 API 失敗，至少用備援池讓網站不會掛掉
    if len(pool) < 100:
        clean = []
        seen = set()
        for code, name, market in FALLBACK_STOCKS:
            if not str(code).isdigit():
                continue
            key = (code, market)
            if key not in seen:
                seen.add(key)
                clean.append((code, name, market))
        return clean

    return pool


# =========================================================
# 2. K線資料
# =========================================================

def yahoo_symbol(code, market):
    if market == "TWO":
        return f"{code}.TWO"
    return f"{code}.TW"


def fetch_candles_from_yahoo(code, market, days=420):
    """
    從 Yahoo Chart API 抓日K。
    """
    symbol = yahoo_symbol(code, market)
    now = int(time.time())
    past = now - days * 24 * 60 * 60

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={past}&period2={now}&interval=1d&events=history&includeAdjustedClose=true"
    )

    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

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
            o = opens[i]
            h = highs[i]
            l = lows[i]
            c = closes[i]
            v = volumes[i]

            if o is None or h is None or l is None or c is None:
                continue

            candles.append({
                "date": datetime.fromtimestamp(ts, TAIWAN_TZ).strftime("%Y-%m-%d"),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": int(v or 0),
            })
        except Exception:
            continue

    return candles


# =========================================================
# 3. 技術指標
# =========================================================

def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values, period):
    if len(values) < period:
        return None

    k = 2 / (period + 1)
    e = sum(values[:period]) / period

    for price in values[period:]:
        e = price * k + e * (1 - k)

    return e


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


def pct(a, b):
    if b == 0 or b is None:
        return 0
    return (a - b) / b * 100


def safe_round(x, digits=2):
    if x is None:
        return None
    try:
        return round(float(x), digits)
    except Exception:
        return None


def highest(candles, field, start, end):
    segment = candles[start:end]
    if not segment:
        return None
    return max(x[field] for x in segment)


def lowest(candles, field, start, end):
    segment = candles[start:end]
    if not segment:
        return None
    return min(x[field] for x in segment)


# =========================================================
# 4. 策略核心：突破不追高 + 回採 + 雞蛋位階 + K棒確認
# =========================================================

def find_breakout(candles):
    """
    找最近是否突破 20 / 60 日前高。
    重點：不是一突破就追，而是先標示突破位階，再看是否回採。
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
    突破後不追高，重點看：
    1. 是否回採前高支撐
    2. 是否跌破
    3. 是否有K棒確認
    """
    last = candles[-1]
    current = last["close"]
    support = breakout["level"]
    breakout_index = breakout["index"]

    after_breakout = candles[breakout_index:]
    if not after_breakout:
        return None

    lowest_after = min(x["low"] for x in after_breakout)

    distance_to_support = pct(current, support)
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


def evaluate_egg_position(candles):
    """
    雞蛋位階：
    不是越高越好。
    太低可能弱，太高容易追高。
    最佳位置：底部轉強後的中低位階至中段。
    """
    if len(candles) < 120:
        return {"score": 0, "position": None, "label": "資料不足"}

    close = candles[-1]["close"]
    low120 = min(x["low"] for x in candles[-120:])
    high120 = max(x["high"] for x in candles[-120:])

    if high120 == low120:
        return {"score": 0, "position": None, "label": "區間不足"}

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


def evaluate_candle_confirmation(candles):
    """
    K棒確認：
    以實戰簡化條件判斷：
    - 紅K
    - 收盤接近高點
    - 下影線支撐
    - 量能溫和放大
    """
    if len(candles) < 25:
        return {"score": 0, "label": "資料不足"}

    last = candles[-1]
    prev = candles[-2]

    o = last["open"]
    h = last["high"]
    l = last["low"]
    c = last["close"]

    body = abs(c - o)
    full = max(h - l, 0.0001)
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l

    close_position = (c - l) / full
    red_k = c > o
    close_near_high = close_position >= 0.65
    lower_support = lower_shadow > upper_shadow and lower_shadow >= body * 0.45

    vols = [x["volume"] for x in candles]
    avg_vol20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 0
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


def evaluate_wave_position(candles):
    """
    波段位置：
    避免買在波段末端，偏好：
    - MA20 > MA60
    - 價格站上MA20
    - 但不要離MA20太遠
    """
    if len(candles) < 80:
        return {"score": 0, "label": "資料不足"}

    closes = [x["close"] for x in candles]
    close = closes[-1]

    ma5 = sma(closes, 5)
    ma20 = sma(closes, 20)
    ma60 = sma(closes, 60)

    if not ma20 or not ma60:
        return {"score": 0, "label": "均線不足"}

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


def evaluate_market_strength(candles):
    """
    個股趨勢強度，近似代表資金是否願意推。
    """
    if len(candles) < 65:
        return {"score": 0, "label": "資料不足"}

    closes = [x["close"] for x in candles]
    vols = [x["volume"] for x in candles]

    close = closes[-1]
    close5 = closes[-6]
    close20 = closes[-21]
    close60 = closes[-61]

    ret5 = pct(close, close5)
    ret20 = pct(close, close20)
    ret60 = pct(close, close60)

    avg_vol5 = sum(vols[-5:]) / 5
    avg_vol20 = sum(vols[-25:-5]) / 20 if len(vols) >= 25 else avg_vol5
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


def evaluate_overheat(candles):
    """
    過熱排除條件：
    符合任一過熱特徵，直接不列入 S/A。
    """
    if len(candles) < 65:
        return {"overheated": False, "reasons": []}

    closes = [x["close"] for x in candles]
    vols = [x["volume"] for x in candles]

    close = closes[-1]
    ma20 = sma(closes, 20)
    rsi14 = rsi(closes, 14)

    ret5 = pct(close, closes[-6])
    ret20 = pct(close, closes[-21])

    avg_vol20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 0
    vol_ratio = vols[-1] / avg_vol20 if avg_vol20 > 0 else 1

    dist_ma20 = pct(close, ma20) if ma20 else 0

    last = candles[-1]
    full = max(last["high"] - last["low"], 0.0001)
    upper_shadow = last["high"] - max(last["open"], last["close"])
    upper_ratio = upper_shadow / full

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
# 5. 單檔股票分析
# =========================================================

def analyze_stock(code, name, market):
    try:
        candles = fetch_candles_from_yahoo(code, market)

        if len(candles) < 90:
            return None

        last = candles[-1]
        closes = [x["close"] for x in candles]

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

        # 風險控制：雖然沒達過熱排除，但如果位置太差也扣掉
        risk_notes = []

        if egg["score"] < 0:
            risk_notes.append(egg["label"])

        if wave.get("dist_ma20") is not None and wave["dist_ma20"] > 12:
            risk_notes.append("短線偏離月線")

        if pullback["status"] == "等回採":
            # 等回採股不給 S，避免使用者誤會是立即進場
            total_score = min(total_score, 76)

        if total_score >= 82 and pullback["status"] != "等回採":
            grade = "S"
        elif total_score >= 68:
            grade = "A"
        else:
            # B級以下完全排除
            return None

        # 最終防線：只留 S/A，不輸出 B、不輸出過熱
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
# 6. 全市場掃描與快取
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

    # 排序邏輯：分數優先，其次靠近支撐者優先
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
# 7. HTML UI
# =========================================================

def render_table(items):
    if not items:
        return """
        <div class="empty">
            目前沒有符合條件的股票。<br>
            這不代表沒有機會，而是代表策略沒有硬選，避免追高或選到 B 級雜訊股。
        </div>
        """

    rows = ""

    for x in items:
        grade_class = "grade-s" if x["grade"] == "S" else "grade-a"

        rows += f"""
        <tr>
            <td>
                <div class="stock-code">{x['code']}</div>
                <div class="stock-name">{x['name']}</div>
            </td>
            <td><span class="{grade_class}">{x['grade']}</span></td>
            <td>{x['score']}</td>
            <td>{x['price']}</td>
            <td>{x['action']}</td>
            <td>{x['breakout_type']}<br><small>{x['breakout_date']}</small></td>
            <td>{x['support']}</td>
            <td>{x['distance_to_support']}%</td>
            <td>{x['egg_label']}<br><small>{x['egg_position']}%</small></td>
            <td>{x['candle_label']}</td>
            <td>{x['wave_label']}</td>
            <td>{x['risk_notes']}</td>
        </tr>
        """

    return f"""
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>股票</th>
                    <th>級別</th>
                    <th>分數</th>
                    <th>現價</th>
                    <th>操作狀態</th>
                    <th>突破型態</th>
                    <th>支撐</th>
                    <th>距支撐</th>
                    <th>雞蛋位階</th>
                    <th>K棒確認</th>
                    <th>波段位置</th>
                    <th>風險備註</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </div>
    """


def html_page(data):
    now = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

    s_table = render_table(data["s_list"])
    a_table = render_table(data["a_list"])
    watch_table = render_table(data["watch_list"])

    return f"""
    <!DOCTYPE html>
    <html lang="zh-Hant">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AI 選股系統｜實戰策略版</title>
        <style>
            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft JhengHei", Arial, sans-serif;
                background: #0f172a;
                color: #e5e7eb;
            }}

            .container {{
                max-width: 1500px;
                margin: 0 auto;
                padding: 22px;
            }}

            .header {{
                background: linear-gradient(135deg, #1e293b, #111827);
                border: 1px solid #334155;
                border-radius: 22px;
                padding: 24px;
                margin-bottom: 20px;
                box-shadow: 0 12px 30px rgba(0,0,0,0.25);
            }}

            h1 {{
                margin: 0 0 10px;
                font-size: 30px;
                letter-spacing: 0.5px;
            }}

            .subtitle {{
                color: #cbd5e1;
                line-height: 1.7;
                font-size: 15px;
            }}

            .badges {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-top: 18px;
            }}

            .badge {{
                padding: 8px 12px;
                border-radius: 999px;
                background: #1e293b;
                border: 1px solid #475569;
                color: #dbeafe;
                font-size: 13px;
            }}

            .stats {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 14px;
                margin-bottom: 20px;
            }}

            .stat {{
                background: #111827;
                border: 1px solid #334155;
                border-radius: 18px;
                padding: 18px;
            }}

            .stat-title {{
                color: #94a3b8;
                font-size: 13px;
                margin-bottom: 8px;
            }}

            .stat-value {{
                font-size: 24px;
                font-weight: 800;
            }}

            .section {{
                background: #111827;
                border: 1px solid #334155;
                border-radius: 22px;
                padding: 20px;
                margin-bottom: 22px;
                box-shadow: 0 10px 24px rgba(0,0,0,0.22);
            }}

            .section-title {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 14px;
                gap: 10px;
            }}

            h2 {{
                margin: 0;
                font-size: 22px;
            }}

            .hint {{
                color: #94a3b8;
                font-size: 13px;
            }}

            .table-wrap {{
                overflow-x: auto;
                border-radius: 16px;
                border: 1px solid #334155;
            }}

            table {{
                width: 100%;
                min-width: 1200px;
                border-collapse: collapse;
                background: #020617;
            }}

            th {{
                background: #1e293b;
                color: #cbd5e1;
                text-align: left;
                padding: 12px;
                font-size: 13px;
                white-space: nowrap;
                border-bottom: 1px solid #334155;
            }}

            td {{
                padding: 12px;
                border-bottom: 1px solid #1f2937;
                vertical-align: top;
                font-size: 14px;
                line-height: 1.5;
            }}

            tr:hover {{
                background: #0f172a;
            }}

            small {{
                color: #94a3b8;
            }}

            .stock-code {{
                font-size: 16px;
                font-weight: 800;
                color: #f8fafc;
            }}

            .stock-name {{
                color: #94a3b8;
                font-size: 13px;
            }}

            .grade-s {{
                display: inline-block;
                padding: 5px 10px;
                border-radius: 999px;
                background: #f59e0b;
                color: #111827;
                font-weight: 900;
            }}

            .grade-a {{
                display: inline-block;
                padding: 5px 10px;
                border-radius: 999px;
                background: #38bdf8;
                color: #082f49;
                font-weight: 900;
            }}

            .empty {{
                color: #94a3b8;
                background: #020617;
                border: 1px dashed #334155;
                border-radius: 16px;
                padding: 22px;
                line-height: 1.7;
            }}

            .actions {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-top: 18px;
            }}

            .btn {{
                display: inline-block;
                text-decoration: none;
                padding: 11px 15px;
                border-radius: 14px;
                font-weight: 700;
                font-size: 14px;
                border: 1px solid #475569;
                color: #f8fafc;
                background: #1e293b;
            }}

            .btn-primary {{
                background: #2563eb;
                border-color: #3b82f6;
            }}

            .warning {{
                margin-top: 14px;
                color: #fcd34d;
                font-size: 13px;
                line-height: 1.7;
            }}

            @media (max-width: 900px) {{
                .container {{
                    padding: 14px;
                }}

                h1 {{
                    font-size: 24px;
                }}

                .stats {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}

                .section-title {{
                    align-items: flex-start;
                    flex-direction: column;
                }}
            }}
        </style>
    </head>

    <body>
        <div class="container">

            <div class="header">
                <h1>AI 選股系統｜實戰策略最終整合版</h1>
                <div class="subtitle">
                    核心邏輯：突破不追高、等回採、回採前高不破、雞蛋位階、K棒確認、波段位置。<br>
                    本版已排除 B級股、過熱股、乖離過大股，避免系統為了硬選而選出雜訊。
                </div>

                <div class="badges">
                    <div class="badge">只顯示 S / A</div>
                    <div class="badge">B級完全排除</div>
                    <div class="badge">過熱股排除</div>
                    <div class="badge">突破後不追高</div>
                    <div class="badge">支撐回採確認</div>
                    <div class="badge">快取20分鐘</div>
                </div>

                <div class="actions">
                    <a class="btn btn-primary" href="/rescan">重新掃描</a>
                    <a class="btn" href="/">重新整理</a>
                    <a class="btn" href="/api/recommendations">JSON資料</a>
                </div>

                <div class="warning">
                    提醒：這是策略篩選系統，不是保證獲利訊號。實際進場仍需搭配大盤環境、成交量、停損位置與個人資金控管。
                </div>
            </div>

            <div class="stats">
                <div class="stat">
                    <div class="stat-title">目前時間</div>
                    <div class="stat-value">{now}</div>
                </div>
                <div class="stat">
                    <div class="stat-title">資料更新</div>
                    <div class="stat-value">{data["updated_at"]}</div>
                </div>
                <div class="stat">
                    <div class="stat-title">掃描股票數</div>
                    <div class="stat-value">{data["raw_count"]}</div>
                </div>
                <div class="stat">
                    <div class="stat-title">符合條件數</div>
                    <div class="stat-value">{data["result_count"]}</div>
                </div>
            </div>

            <div class="section">
                <div class="section-title">
                    <h2>S級前10｜最接近實戰進場條件</h2>
                    <div class="hint">條件：突破後回採、位階健康、K棒確認、波段位置佳、未過熱</div>
                </div>
                {s_table}
            </div>

            <div class="section">
                <div class="section-title">
                    <h2>A級前10｜可追蹤觀察</h2>
                    <div class="hint">條件：結構轉強，但可能仍在等回採或確認度略低</div>
                </div>
                {a_table}
            </div>

            <div class="section">
                <div class="section-title">
                    <h2>觀察名單｜等回採 / 可觀察進場</h2>
                    <div class="hint">突破後不急追，等待支撐、K棒、量能再次確認</div>
                </div>
                {watch_table}
            </div>

        </div>
    </body>
    </html>
    """


# =========================================================
# 8. Routes
# =========================================================

@app.get("/", response_class=HTMLResponse)
def home():
    try:
        data = scan_market(force=False)
        return HTMLResponse(html_page(data))
    except Exception as e:
        err = traceback.format_exc()
        return HTMLResponse(f"""
        <html>
        <body style="font-family:Arial; background:#111827; color:#fff; padding:24px;">
            <h1>系統發生錯誤</h1>
            <p>{str(e)}</p>
            <pre style="white-space:pre-wrap; background:#020617; padding:16px; border-radius:12px;">{err}</pre>
        </body>
        </html>
        """, status_code=500)


@app.get("/recommendations", response_class=HTMLResponse)
def recommendations_page():
    return home()


@app.get("/rescan", response_class=HTMLResponse)
def rescan():
    try:
        data = scan_market(force=True)
        return HTMLResponse(html_page(data))
    except Exception as e:
        err = traceback.format_exc()
        return HTMLResponse(f"""
        <html>
        <body style="font-family:Arial; background:#111827; color:#fff; padding:24px;">
            <h1>重新掃描失敗</h1>
            <p>{str(e)}</p>
            <pre style="white-space:pre-wrap; background:#020617; padding:16px; border-radius:12px;">{err}</pre>
        </body>
        </html>
        """, status_code=500)


@app.get("/api/recommendations")
def api_recommendations(force: bool = Query(False)):
    try:
        data = scan_market(force=force)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({
            "error": str(e),
            "trace": traceback.format_exc(),
        }, status_code=500)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "cache_updated_at": CACHE["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if CACHE["updated_at"] else None,
        "cache_has_data": CACHE["data"] is not None,
    }


# Railway / local 啟動用
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
