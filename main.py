import os
import json
import time
import math
import threading
from io import StringIO
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import yfinance as yf
from flask import Flask, render_template, redirect, url_for, request, Response
from apscheduler.schedulers.background import BackgroundScheduler


app = Flask(__name__)


# =====================================================
# 登入保護
# =====================================================
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456")


def check_auth(username, password):
    return username == ADMIN_USER and password == ADMIN_PASSWORD


def require_auth():
    return Response(
        "需要登入才能使用此網站",
        401,
        {"WWW-Authenticate": 'Basic realm="Stock AI Login"'}
    )


@app.before_request
def protect_site():
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return require_auth()


# =====================================================
# 基本設定
# =====================================================
TAIWAN_TZ = ZoneInfo("Asia/Taipei")

RESULT_FILE = "scan_results.json"
TRACK_FILE = "track.json"
TRADE_LOG_FILE = "trade_log.json"
STOCK_POOL_FILE = "stock_pool.json"
SCAN_STATUS_FILE = "scan_status.json"
CANDIDATE_FILE = "candidate_pool.json"

FULL_MARKET_MIN_COUNT = 1700
PARTIAL_MARKET_MIN_COUNT = 1000

MAX_ELITE_RESULTS = 3
MAX_S_RESULTS = 10
MAX_CANDIDATE_DISPLAY = 30
MAX_ENTRY_ALERTS = 10

ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "1000000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))

MIN_AVG_VOLUME_20 = 500_000
MIN_AVG_AMOUNT_20 = 5_000_000

is_scanning = False


# =====================================================
# 工具
# =====================================================
def taiwan_now():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")


def read_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def write_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_float(x, default=0):
    try:
        if hasattr(x, "iloc"):
            x = x.iloc[0]
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def pct(a, b):
    try:
        if not b:
            return 0
        return (a - b) / b * 100
    except Exception:
        return 0


# =====================================================
# 掃描狀態
# =====================================================
def save_scan_status(status, message):
    write_json_file(SCAN_STATUS_FILE, {
        "status": status,
        "message": message,
        "updated_at": taiwan_now()
    })


def load_scan_status():
    return read_json_file(SCAN_STATUS_FILE, {
        "status": "idle",
        "message": "尚未掃描",
        "updated_at": "-"
    })


# =====================================================
# 保底股票池
# =====================================================
def get_fallback_stock_pool():
    base = {
        "2330.TW": ("台積電", "半導體"),
        "2303.TW": ("聯電", "半導體"),
        "2454.TW": ("聯發科", "IC設計"),
        "3034.TW": ("聯詠", "IC設計"),
        "2379.TW": ("瑞昱", "IC設計"),
        "3661.TW": ("世芯-KY", "IC設計"),
        "3443.TW": ("創意", "IC設計"),
        "5274.TWO": ("信驊", "IC設計"),
        "2317.TW": ("鴻海", "AI伺服器"),
        "2382.TW": ("廣達", "AI伺服器"),
        "3231.TW": ("緯創", "AI伺服器"),
        "6669.TW": ("緯穎", "AI伺服器"),
        "2308.TW": ("台達電", "電源"),
        "3017.TW": ("奇鋐", "散熱"),
        "3324.TWO": ("雙鴻", "散熱"),
        "3653.TW": ("健策", "散熱"),
        "8996.TWO": ("高力", "散熱"),
        "2345.TW": ("智邦", "網通"),
        "2383.TW": ("台光電", "PCB"),
        "3037.TW": ("欣興", "PCB"),
        "8046.TW": ("南電", "PCB"),
        "3189.TWO": ("景碩", "PCB"),
        "2881.TW": ("富邦金", "金融"),
        "2882.TW": ("國泰金", "金融"),
        "2886.TW": ("兆豐金", "金融"),
        "2891.TW": ("中信金", "金融"),
        "2603.TW": ("長榮", "航運"),
        "2609.TW": ("陽明", "航運"),
        "2615.TW": ("萬海", "航運"),
        "2618.TW": ("長榮航", "航空"),
        "2610.TW": ("華航", "航空"),
        "1513.TW": ("中興電", "重電"),
        "1519.TW": ("華城", "重電"),
        "1609.TW": ("大亞", "電線電纜"),
        "1618.TW": ("合機", "電線電纜"),
        "6446.TW": ("藥華藥", "生技"),
        "1760.TW": ("寶齡富錦", "生技"),
        "4743.TWO": ("合一", "生技"),
        "4105.TWO": ("東洋", "生技"),
        "6472.TW": ("保瑞", "生技"),
    }

    return {
        symbol: {
            "name": name,
            "industry": industry
        }
        for symbol, (name, industry) in base.items()
    }


# =====================================================
# 股票池
# =====================================================
def normalize_stock_item(code, name, industry="其他", suffix=".TW"):
    code = str(code).strip()
    name = str(name).strip()
    industry = str(industry).strip() if industry else "其他"

    if len(code) == 4 and code.isdigit() and name:
        return f"{code}{suffix}", {
            "name": name,
            "industry": industry
        }

    return None, None


def fetch_json_url(url, timeout=20):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*"
    }

    res = requests.get(url, headers=headers, timeout=timeout)
    res.raise_for_status()
    return res.json()


def fetch_twse_openapi_stock_pool():
    market = {}

    try:
        data = fetch_json_url("https://openapi.twse.com.tw/v1/opendata/t187ap03_L")

        for item in data:
            code = item.get("公司代號", "")
            name = item.get("公司簡稱", "") or item.get("公司名稱", "")
            industry = item.get("產業別", "上市")

            symbol, info = normalize_stock_item(code, name, industry, ".TW")
            if symbol:
                market[symbol] = info

    except Exception as e:
        print("TWSE OpenAPI 失敗：", e)

    return market


def parse_tpex_item(item):
    code_keys = [
        "公司代號", "股票代號", "有價證券代號", "證券代號",
        "SecuritiesCompanyCode", "CompanyCode", "Code", "stock_id", "stk_code"
    ]

    name_keys = [
        "公司簡稱", "公司名稱", "股票名稱", "有價證券名稱", "證券簡稱",
        "CompanyName", "Name", "stock_name", "stk_name"
    ]

    industry_keys = ["產業別", "產業類別", "IndustryCode", "Industry", "industry"]

    code = ""
    name = ""
    industry = "上櫃"

    for k in code_keys:
        if k in item and item.get(k):
            code = item.get(k)
            break

    for k in name_keys:
        if k in item and item.get(k):
            name = item.get(k)
            break

    for k in industry_keys:
        if k in item and item.get(k):
            industry = item.get(k)
            break

    return normalize_stock_item(code, name, industry, ".TWO")


def fetch_tpex_openapi_stock_pool():
    market = {}

    urls = [
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_company",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_company_basic",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_listed_companies",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_security_info"
    ]

    for url in urls:
        try:
            data = fetch_json_url(url)
            temp = {}

            if isinstance(data, dict):
                rows = data.get("data", [])
            elif isinstance(data, list):
                rows = data
            else:
                rows = []

            for item in rows:
                if not isinstance(item, dict):
                    continue

                symbol, info = parse_tpex_item(item)
                if symbol:
                    temp[symbol] = info

            if len(temp) > len(market):
                market = temp

            if len(market) >= 700:
                return market

        except Exception as e:
            print("TPEx OpenAPI 嘗試失敗：", url, e)

    return market


def fetch_isin_by_mode(mode, suffix, industry_label):
    market = {}

    try:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        headers = {"User-Agent": "Mozilla/5.0"}

        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()

        try:
            text = res.content.decode("big5", errors="ignore")
        except Exception:
            text = res.text

        tables = pd.read_html(StringIO(text))
        df = tables[0]
        df = df[df[0].astype(str).str.contains(r"^\d{4}", na=False)]

        for item in df[0]:
            try:
                parts = str(item).split()
                code = parts[0]
                name = parts[1]

                symbol, info = normalize_stock_item(code, name, industry_label, suffix)
                if symbol:
                    market[symbol] = info

            except Exception:
                continue

    except Exception as e:
        print(f"ISIN mode={mode} 失敗：", e)

    return market


def fetch_isin_all_stock_pool():
    market = {}
    market.update(fetch_isin_by_mode(2, ".TW", "上市"))
    market.update(fetch_isin_by_mode(4, ".TWO", "上櫃"))
    return market


def save_stock_pool(market, source_note=""):
    write_json_file(STOCK_POOL_FILE, {
        "updated_at": taiwan_now(),
        "count": len(market),
        "source_note": source_note,
        "stocks": market
    })


def load_stock_pool_cache():
    data = read_json_file(STOCK_POOL_FILE, None)
    if not data:
        return None, None

    stocks = data.get("stocks", {})
    if stocks and len(stocks) > 100:
        return stocks, data

    return None, None


def get_stock_pool():
    source_log = []

    cache, cache_meta = load_stock_pool_cache()
    cache_count = len(cache) if cache else 0

    if cache_count:
        source_log.append(f"快取：{cache_count}檔")

    market = {}

    twse = fetch_twse_openapi_stock_pool()
    market.update(twse)
    source_log.append(f"TWSE上市：{len(twse)}檔")

    tpex = fetch_tpex_openapi_stock_pool()
    market.update(tpex)
    source_log.append(f"TPEx上櫃：{len(tpex)}檔")

    if len(market) < FULL_MARKET_MIN_COUNT:
        isin_all = fetch_isin_all_stock_pool()
        source_log.append(f"ISIN全市場：{len(isin_all)}檔")

        if len(isin_all) > len(market):
            market = isin_all

    current_count = len(market)

    if current_count >= FULL_MARKET_MIN_COUNT:
        note = "；".join(source_log) + f"；採用完整股票池 {current_count} 檔"
        save_stock_pool(market, note)
        save_scan_status("running", note)
        return market

    if cache and cache_count > current_count:
        note = "；".join(source_log) + f"；來源不足，改用快取 {cache_count} 檔"
        save_scan_status("running", note)
        return cache

    if current_count >= PARTIAL_MARKET_MIN_COUNT:
        note = "；".join(source_log) + f"；警告：目前僅部分股票池 {current_count} 檔"
        save_scan_status("running", note)
        return market

    if cache:
        note = "；".join(source_log) + f"；來源失敗，改用快取 {cache_count} 檔"
        save_scan_status("running", note)
        return cache

    fallback = get_fallback_stock_pool()
    note = "；".join(source_log) + f"；所有來源失敗，使用保底股票池 {len(fallback)} 檔"
    save_scan_status("running", note)
    return fallback


# =====================================================
# 股價資料
# =====================================================
def download_stock(symbol, period="1y"):
    try:
        df = yf.download(
            symbol,
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=False,
            threads=False
        )

        if df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df.dropna()

    except Exception as e:
        print("下載失敗：", symbol, e)
        return None


# =====================================================
# 大盤風險
# =====================================================
def get_index_price(symbol):
    df = download_stock(symbol, "5d")
    if df is None or df.empty:
        return "-"
    price = safe_float(df["Close"].iloc[-1], None)
    return round(price, 2) if price else "-"


def analyze_index(symbol):
    df = download_stock(symbol, "1y")

    if df is None or len(df) < 120:
        return {
            "ok": False,
            "above_ma20": False,
            "above_ma60": False,
            "ma20_gt_ma60": False
        }

    close = df["Close"]
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    last = close.iloc[-1]

    return {
        "ok": True,
        "above_ma20": bool(last > ma20.iloc[-1]),
        "above_ma60": bool(last > ma60.iloc[-1]),
        "ma20_gt_ma60": bool(ma20.iloc[-1] > ma60.iloc[-1])
    }


def get_market_status():
    twii = analyze_index("^TWII")
    otc = analyze_index("^TWOII")

    if not twii["ok"]:
        return {
            "market_status": "資料不足",
            "market_score": 0,
            "risk_mode": "防守",
            "risk_switch": "保守觀察",
            "allow_new_positions": False,
            "risk_multiplier": 0,
            "risk_note": "大盤資料不足，暫不建議建立新倉。"
        }

    if twii["above_ma20"] and twii["above_ma60"] and twii["ma20_gt_ma60"] and otc.get("above_ma20"):
        return {
            "market_status": "強多市場",
            "market_score": 25,
            "risk_mode": "積極",
            "risk_switch": "允許新倉",
            "allow_new_positions": True,
            "risk_multiplier": 1.0,
            "risk_note": "大盤結構偏多，允許進場與正常部位。"
        }

    if twii["above_ma60"] and twii["ma20_gt_ma60"]:
        return {
            "market_status": "多頭市場",
            "market_score": 15,
            "risk_mode": "正常",
            "risk_switch": "允許新倉",
            "allow_new_positions": True,
            "risk_multiplier": 0.8,
            "risk_note": "大盤中期偏多，部位略降，仍可依策略進場。"
        }

    if not twii["above_ma20"] and not otc.get("above_ma20", False):
        return {
            "market_status": "轉弱市場",
            "market_score": -25,
            "risk_mode": "防守",
            "risk_switch": "禁止新倉",
            "allow_new_positions": False,
            "risk_multiplier": 0,
            "risk_note": "加權與櫃買皆弱於月線，禁止新倉，只保留觀察與持股風控。"
        }

    if not twii["above_ma20"]:
        return {
            "market_status": "盤整偏弱",
            "market_score": -10,
            "risk_mode": "保守",
            "risk_switch": "只允許S級",
            "allow_new_positions": True,
            "risk_multiplier": 0.3,
            "risk_note": "大盤低於月線，只允許高品質標的且降低部位。"
        }

    return {
        "market_status": "盤整市場",
        "market_score": -5,
        "risk_mode": "保守",
        "risk_switch": "減碼觀察",
        "allow_new_positions": True,
        "risk_multiplier": 0.5,
        "risk_note": "大盤盤整，採取半部位與嚴格停損。"
    }


# =====================================================
# 指標計算
# =====================================================
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


def calc_liquidity(df):
    close = df["Close"]
    volume = df["Volume"]

    avg_volume_5 = safe_float(volume.rolling(5).mean().iloc[-1])
    avg_volume_20 = safe_float(volume.rolling(20).mean().iloc[-1])
    avg_amount_20 = safe_float((close * volume).rolling(20).mean().iloc[-1])

    score = 0
    level = "普通"
    warnings = []

    if avg_volume_20 >= 500_000:
        score += 10

    if avg_volume_20 >= 1_000_000:
        score += 10
        level = "佳"

    if avg_volume_20 >= 3_000_000:
        score += 10
        level = "優"

    if avg_amount_20 >= 50_000_000:
        score += 10

    if avg_volume_20 < MIN_AVG_VOLUME_20:
        score -= 30
        level = "不足"
        warnings.append("20日均量不足")

    if avg_amount_20 < MIN_AVG_AMOUNT_20:
        score -= 20
        level = "不足"
        warnings.append("成交金額不足")

    return {
        "avg_volume_5": round(avg_volume_5, 0),
        "avg_volume_20": round(avg_volume_20, 0),
        "avg_volume_20_lots": round(avg_volume_20 / 1000, 1),
        "avg_amount_20": round(avg_amount_20, 0),
        "liquidity_score": score,
        "liquidity_level": level,
        "liquidity_warnings": warnings,
        "is_liquid_enough": avg_volume_20 >= MIN_AVG_VOLUME_20 and avg_amount_20 >= MIN_AVG_AMOUNT_20
    }


def calc_main_force(df):
    close = df["Close"]
    open_ = df["Open"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    money = close * volume

    ma_money_5 = money.rolling(5).mean()
    ma_money_20 = money.rolling(20).mean()

    money_ratio = safe_float(ma_money_5.iloc[-1] / ma_money_20.iloc[-1]) if safe_float(ma_money_20.iloc[-1]) else 0

    up_day = close > open_
    strong_up = (close > open_) & ((close - open_) / open_ * 100 > 2)
    near_high = ((high - close) / (high - low + 0.0001)) < 0.25

    main_buy_days = int(((up_day) & (money > ma_money_20 * 1.3)).tail(10).sum())
    strong_buy_days = int(((strong_up) & (near_high) & (money > ma_money_20 * 1.5)).tail(10).sum())

    score = 0
    signals = []

    if money_ratio > 1.1:
        score += 10
        signals.append("資金微幅增溫")

    if money_ratio > 1.2:
        score += 15
        signals.append("資金增溫")

    if money_ratio > 1.6:
        score += 25
        signals.append("資金明顯放大")

    if main_buy_days >= 2:
        score += 15
        signals.append("疑似主力承接")

    if main_buy_days >= 3:
        score += 20
        signals.append("疑似主力連續承接")

    if strong_buy_days >= 1:
        score += 15
        signals.append("強勢買盤出現")

    if strong_buy_days >= 2:
        score += 25
        signals.append("強勢買盤進場")

    if close.iloc[-1] < close.iloc[-2] and volume.iloc[-1] > volume.rolling(20).mean().iloc[-1] * 1.5:
        score -= 20
        signals.append("高量下跌警訊")

    return {
        "main_score": round(score, 1),
        "main_signals": signals,
        "money_ratio": round(money_ratio, 2),
        "main_buy_days": main_buy_days,
        "strong_buy_days": strong_buy_days
    }


def analyze_egg_position(price, low_value, high_value):
    if not high_value or not low_value or high_value <= low_value:
        return {
            "egg_zone": "無法判斷",
            "egg_score": 0,
            "egg_position_pct": 0,
            "egg_note": "區間不足。"
        }

    pos = (price - low_value) / (high_value - low_value) * 100

    if pos <= 35:
        return {
            "egg_zone": "蛋黃區",
            "egg_score": 25,
            "egg_position_pct": round(pos, 2),
            "egg_note": "低位階，屬較安全的啟動區。"
        }

    if pos <= 70:
        return {
            "egg_zone": "蛋白區",
            "egg_score": 15,
            "egg_position_pct": round(pos, 2),
            "egg_note": "中位階，若趨勢明確可續抱。"
        }

    if pos <= 90:
        return {
            "egg_zone": "蛋殼區",
            "egg_score": -5,
            "egg_position_pct": round(pos, 2),
            "egg_note": "偏高位階，接近滿足區需提高停利警戒。"
        }

    return {
        "egg_zone": "蛋殼過熱區",
        "egg_score": -25,
        "egg_position_pct": round(pos, 2),
        "egg_note": "高位過熱，容易震盪或拉回。"
    }


def analyze_candle_pattern(df):
    if df is None or len(df) < 5:
        return {
            "candle_signal": "資料不足",
            "candle_score": 0,
            "candle_note": "K棒不足。"
        }

    o = safe_float(df["Open"].iloc[-1])
    h = safe_float(df["High"].iloc[-1])
    l = safe_float(df["Low"].iloc[-1])
    c = safe_float(df["Close"].iloc[-1])

    po = safe_float(df["Open"].iloc[-2])
    pc = safe_float(df["Close"].iloc[-2])

    rng = max(h - l, 0.0001)
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l

    is_red = c > o
    is_black = c < o

    if is_red and pc < po and c > po and o < pc:
        return {
            "candle_signal": "紅K吞噬",
            "candle_score": 25,
            "candle_note": "買盤反攻，轉強訊號。"
        }

    if is_red and body / rng >= 0.55 and (c - o) / o * 100 >= 2 and c >= h - rng * 0.25:
        return {
            "candle_signal": "帶量長紅K",
            "candle_score": 20,
            "candle_note": "買盤積極，收高強勢。"
        }

    if lower / rng >= 0.45 and is_red:
        return {
            "candle_signal": "下影支撐紅K",
            "candle_score": 18,
            "candle_note": "支撐區有承接。"
        }

    if is_black and pc > po and c < po and o > pc:
        return {
            "candle_signal": "黑K吞噬",
            "candle_score": -25,
            "candle_note": "轉弱K棒，須小心。"
        }

    if is_black and body / rng >= 0.55 and (o - c) / o * 100 >= 2:
        return {
            "candle_signal": "長黑K",
            "candle_score": -25,
            "candle_note": "賣壓明顯。"
        }

    if upper / rng >= 0.45:
        return {
            "candle_signal": "長上影K",
            "candle_score": -15,
            "candle_note": "上方壓力大，需防假突破。"
        }

    return {
        "candle_signal": "中性K",
        "candle_score": 0,
        "candle_note": "K棒中性。"
    }


def analyze_breakout_pullback(df):
    if df is None or len(df) < 80:
        return {
            "prev_high_20": 0,
            "prev_high_60": 0,
            "breakout_level": 0,
            "pullback_low": 0,
            "breakout_state": "資料不足",
            "breakout_score": 0,
            "breakout_note": "資料不足。"
        }

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    price = safe_float(close.iloc[-1])
    prev_high_20 = safe_float(high.rolling(20).max().iloc[-2])
    prev_high_60 = safe_float(high.rolling(60).max().iloc[-2])

    breakout_level = max(prev_high_20, prev_high_60)

    recent = df.tail(8)
    recent_close = recent["Close"]
    recent_low = recent["Low"]

    broke = len(recent_close[recent_close > breakout_level * 1.003]) > 0
    pullback = bool((recent_low <= breakout_level * 1.015).any())
    stand_back = bool((recent_close >= breakout_level * 0.995).tail(3).any())
    fake_break = broke and price < breakout_level * 0.995

    pullback_low = safe_float(recent_low.min())

    if fake_break:
        return {
            "prev_high_20": round(prev_high_20, 2),
            "prev_high_60": round(prev_high_60, 2),
            "breakout_level": round(breakout_level, 2),
            "pullback_low": round(pullback_low, 2),
            "breakout_state": "假突破取消",
            "breakout_score": -35,
            "breakout_note": "突破後跌回支撐下方，暫時取消。"
        }

    if broke and pullback and stand_back and price > breakout_level:
        return {
            "prev_high_20": round(prev_high_20, 2),
            "prev_high_60": round(prev_high_60, 2),
            "breakout_level": round(breakout_level, 2),
            "pullback_low": round(pullback_low, 2),
            "breakout_state": "突破回採不破",
            "breakout_score": 35,
            "breakout_note": "突破前高後回採不破，支撐有效。"
        }

    if broke:
        return {
            "prev_high_20": round(prev_high_20, 2),
            "prev_high_60": round(prev_high_60, 2),
            "breakout_level": round(breakout_level, 2),
            "pullback_low": round(pullback_low, 2),
            "breakout_state": "突破完成等回採",
            "breakout_score": 12,
            "breakout_note": "已突破但尚未回採確認，不追高。"
        }

    if price >= breakout_level * 0.97:
        return {
            "prev_high_20": round(prev_high_20, 2),
            "prev_high_60": round(prev_high_60, 2),
            "breakout_level": round(breakout_level, 2),
            "pullback_low": round(pullback_low, 2),
            "breakout_state": "接近前高壓力",
            "breakout_score": 5,
            "breakout_note": "接近前高，等待突破後回採。"
        }

    return {
        "prev_high_20": round(prev_high_20, 2),
        "prev_high_60": round(prev_high_60, 2),
        "breakout_level": round(breakout_level, 2),
        "pullback_low": round(pullback_low, 2),
        "breakout_state": "尚未突破",
        "breakout_score": 0,
        "breakout_note": "尚未突破前高。"
    }


def infer_sector(symbol, name, industry):
    if industry and industry not in ["上市", "上櫃", "其他"]:
        return industry

    name = name or ""

    groups = {
        "AI伺服器": ["廣達", "緯創", "緯穎", "鴻海", "英業達", "技嘉", "華碩"],
        "散熱": ["奇鋐", "雙鴻", "健策", "高力"],
        "PCB": ["台光電", "欣興", "南電", "景碩"],
        "半導體": ["台積電", "聯電", "世界", "力積電"],
        "IC設計": ["聯發科", "聯詠", "瑞昱", "創意", "世芯", "力旺", "譜瑞", "信驊"],
        "金融": ["金", "中租"],
        "航運": ["長榮", "陽明", "萬海", "裕民"],
        "航空": ["華航", "長榮航"],
        "生技": ["藥", "生", "醫", "保瑞", "合一", "東洋"],
        "重電": ["華城", "中興電", "東元", "大亞", "合機"],
        "塑化": ["台塑", "南亞", "台化", "台塑化"],
        "鋼鐵": ["中鋼", "大成鋼"]
    }

    for sector, keys in groups.items():
        for k in keys:
            if k in name:
                return sector

    return "其他"


# =====================================================
# 個股分析
# =====================================================
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

    atr_series = calc_atr(df)
    atr = safe_float(atr_series.iloc[-1])

    change_5d = pct(close.iloc[-1], close.iloc[-5])
    change_20d = pct(close.iloc[-1], close.iloc[-20])
    change_60d = pct(close.iloc[-1], close.iloc[-60])

    high_20 = safe_float(high.rolling(20).max().iloc[-2])
    high_60 = safe_float(high.rolling(60).max().iloc[-2])
    low_60 = safe_float(low.rolling(60).min().iloc[-1])
    high_120 = safe_float(high.rolling(120).max().iloc[-1])
    low_120 = safe_float(low.rolling(120).min().iloc[-1])

    ma20_now = safe_float(ma20.iloc[-1])
    ma60_now = safe_float(ma60.iloc[-1])
    ma120_now = safe_float(ma120.iloc[-1])

    ma20_distance = pct(price, ma20_now)
    ma60_distance = pct(price, ma60_now)

    signals = []
    warnings = []
    score = 0

    if price > ma20_now:
        signals.append("站上月線")
        score += 10

    if price > ma60_now:
        signals.append("站上季線")
        score += 10

    if ma20_now > ma60_now:
        signals.append("月線大於季線")
        score += 10

    if ma20_now > ma60_now > ma120_now:
        signals.append("多頭排列")
        score += 25

    if price > high_20:
        signals.append("突破20日前高")
        score += 15

    if price > high_60:
        signals.append("突破60日前高")
        score += 20

    if 1 <= change_5d <= 15:
        signals.append("短線動能健康")
        score += 15

    if change_20d > 5:
        signals.append("波段轉強")
        score += 15

    if change_60d > 10:
        signals.append("中期趨勢轉強")
        score += 10

    if 5 <= pct(price, low_60) <= 45:
        signals.append("低中位啟動")
        score += 15

    if change_5d > 22:
        warnings.append("5日漲幅過熱")
        score -= 30

    if change_20d > 45:
        warnings.append("20日漲幅過熱")
        score -= 25

    if ma20_distance > 12:
        warnings.append("距離月線過遠")
        score -= 20

    if price < ma20_now:
        warnings.append("跌破月線")
        score -= 25

    atr_pct = atr / price * 100 if atr else 0
    if atr_pct > 10:
        warnings.append("波動過大")
        score -= 15

    liquidity = calc_liquidity(df)
    main_force = calc_main_force(df)
    egg = analyze_egg_position(price, low_120, high_120)
    candle = analyze_candle_pattern(df)
    breakout = analyze_breakout_pullback(df)

    score += liquidity["liquidity_score"]
    score += main_force["main_score"]
    score += egg["egg_score"]
    score += candle["candle_score"]
    score += breakout["breakout_score"]

    if liquidity["liquidity_warnings"]:
        warnings.extend(liquidity["liquidity_warnings"])

    result = {
        "price": round(price, 2),
        "technical_score": round(score, 1),
        "change_5d": round(change_5d, 2),
        "change_20d": round(change_20d, 2),
        "change_60d": round(change_60d, 2),
        "ma5": round(safe_float(ma5.iloc[-1]), 2),
        "ma20": round(ma20_now, 2),
        "ma60": round(ma60_now, 2),
        "ma20_distance": round(ma20_distance, 2),
        "ma60_distance": round(ma60_distance, 2),
        "atr": round(atr, 2),
        "atr_pct": round(atr_pct, 2),
        "latest_high": round(safe_float(high.iloc[-1]), 2),
        "latest_low": round(safe_float(low.iloc[-1]), 2),
        "low_60": round(low_60, 2),
        "high_60": round(high_60, 2),
        "low_120": round(low_120, 2),
        "high_120": round(high_120, 2),
        "signals": signals,
        "warnings": warnings,
    }

    result.update(liquidity)
    result.update(main_force)
    result.update(egg)
    result.update(candle)
    result.update(breakout)

    return result


# =====================================================
# 族群強度
# =====================================================
def calc_sector_scores(items):
    sector_map = {}

    for item in items:
        sector_map.setdefault(item["sector"], []).append(item)

    scores = {}

    for sector, arr in sector_map.items():
        if not arr:
            continue

        avg_5d = sum(x["change_5d"] for x in arr) / len(arr)
        avg_20d = sum(x["change_20d"] for x in arr) / len(arr)
        avg_main = sum(x["main_score"] for x in arr) / len(arr)
        strong = len([x for x in arr if x["technical_score"] >= 60])
        strong_ratio = strong / len(arr)

        score = 0

        if avg_5d > 2:
            score += 10

        if avg_5d > 5:
            score += 10

        if avg_20d > 5:
            score += 10

        if avg_20d > 12:
            score += 10

        if avg_main >= 35:
            score += 10

        if strong_ratio >= 0.25:
            score += 10

        if strong_ratio >= 0.4:
            score += 15

        scores[sector] = {
            "sector": sector,
            "sector_score": score,
            "sector_avg_5d": round(avg_5d, 2),
            "sector_avg_20d": round(avg_20d, 2),
            "sector_avg_main": round(avg_main, 2),
            "sector_strong_ratio": round(strong_ratio * 100, 1),
            "sector_stock_count": len(arr)
        }

    return scores


def build_sector_rankings(sector_scores):
    rows = sorted(sector_scores.values(), key=lambda x: x["sector_score"], reverse=True)
    ranked = []

    for i, row in enumerate(rows[:10], start=1):
        x = dict(row)
        x["rank"] = i
        ranked.append(x)

    return ranked


# =====================================================
# AI交易計畫
# =====================================================
def determine_entry_status(item):
    warnings = item.get("warnings", [])
    breakout_state = item.get("breakout_state", "")
    candle_score = item.get("candle_score", 0)
    main_score = item.get("main_score", 0)

    if not item.get("is_liquid_enough"):
        return "流動性不足", "不列入", "成交量或成交金額不足。"

    if "跌破月線" in warnings:
        return "弱勢取消型", "跌破取消", "跌破月線，結構轉弱。"

    if breakout_state == "假突破取消":
        return "假突破型", "跌破取消", "突破失敗，暫時取消候選。"

    if (
        "距離月線過遠" in warnings or
        "5日漲幅過熱" in warnings or
        "20日漲幅過熱" in warnings or
        item.get("egg_zone") == "蛋殼過熱區" or
        candle_score <= -20
    ):
        return "過熱觀察型", "過熱不追", "位階或漲幅偏高，不建議追高。"

    if breakout_state == "突破回採不破":
        if candle_score > 0 or main_score >= 40:
            return "突破回採型", "可觀察進場", "突破前高後回採不破，且K棒或資金轉強。"
        return "突破回採型", "等轉強K", "回採不破，但仍需轉強K確認。"

    if breakout_state == "突破完成等回採":
        return "突破型", "等回採", "突破後不追高，等待回採前高不破。"

    if item.get("technical_score", 0) >= 150 and main_score >= 35:
        return "低位啟動型", "等突破", "量價轉強，等待突破或回採確認。"

    if item.get("technical_score", 0) >= 120:
        return "趨勢觀察型", "僅列觀察", "條件尚可，但尚未出現明確進場點。"

    return "觀察型", "僅列觀察", "尚未達到明確進場條件。"


def build_trade_plan(item):
    price = item.get("price", 0)
    atr = item.get("atr", 0)
    support = item.get("breakout_level") or item.get("ma20") or price
    pullback_low = item.get("pullback_low") or item.get("latest_low") or support

    if not price or not atr or not support:
        return {
            "support_price": 0,
            "next_entry_low": 0,
            "next_entry_high": 0,
            "no_entry_price": 0,
            "invalid_price": 0,
            "practical_stop": 0,
            "initial_stop": 0,
            "standard_trailing_stop": 0,
            "risk_reward": 0,
            "ai_next_action": "資料不足",
            "trade_plan_note": "價格或ATR資料不足。"
        }

    next_entry_low = round(support * 1.003, 2)
    max_by_atr = support + atr * 0.6
    max_by_pct = support * 1.025
    next_entry_high = round(min(max_by_atr, max_by_pct), 2)

    if next_entry_high < next_entry_low:
        next_entry_high = round(next_entry_low + atr * 0.3, 2)

    no_entry_price = round(support * 0.995, 2)
    invalid_price = round(min(support * 0.99, pullback_low * 0.99), 2)

    practical_stop = round(max(support * 0.985, next_entry_low - atr * 1.5), 2)
    initial_stop = practical_stop

    standard_trailing_stop = round(price - atr * 2.5, 2)
    target_price = round(next_entry_low + atr * 3, 2)

    risk = max(next_entry_low - practical_stop, 0.01)
    reward = max(target_price - next_entry_low, 0.01)
    risk_reward = round(reward / risk, 2)

    status = item.get("entry_status", "")

    if status == "可觀察進場":
        ai_next_action = "明日開盤若未跌破支撐且未開太高，可第一筆試單"
    elif status in ["等回採", "等突破", "等轉強K"]:
        ai_next_action = "持續觀察，等待支撐確認或轉強K"
    elif status in ["跌破取消", "過熱不追", "流動性不足"]:
        ai_next_action = "不進場"
    else:
        ai_next_action = "觀察"

    if risk_reward < 1.5:
        ai_next_action = "風報比不足，等待更低進場價或放棄"

    return {
        "support_price": round(support, 2),
        "next_entry_low": next_entry_low,
        "next_entry_high": next_entry_high,
        "no_entry_price": no_entry_price,
        "invalid_price": invalid_price,
        "practical_stop": practical_stop,
        "initial_stop": initial_stop,
        "standard_trailing_stop": standard_trailing_stop,
        "target_price": target_price,
        "risk_reward": risk_reward,
        "ai_next_action": ai_next_action,
        "trade_plan_note": "明日開盤若站在進場區間內可試單；跌破不進場點位則不進場；跌破實戰停損價需停損；假突破失效價只作為候選取消參考。"
    }


def calc_position_sizing(item, market_info):
    entry = item.get("next_entry_low") or item.get("price")
    stop = item.get("practical_stop") or item.get("initial_stop")

    risk_multiplier = market_info.get("risk_multiplier", 0)
    adjusted_risk_amount = ACCOUNT_SIZE * RISK_PER_TRADE * risk_multiplier

    if not entry or not stop or entry <= stop or adjusted_risk_amount <= 0:
        return {
            "suggest_shares": 0,
            "suggest_lots": 0,
            "position_value": 0,
            "risk_per_share": 0,
            "first_entry_pct": 30,
            "first_entry_shares": 0
        }

    risk_per_share = entry - stop
    shares = math.floor(adjusted_risk_amount / risk_per_share)
    lots = math.floor(shares / 1000)
    first_entry_shares = math.floor(shares * 0.3)

    return {
        "suggest_shares": shares,
        "suggest_lots": lots,
        "position_value": round(shares * entry, 0),
        "risk_per_share": round(risk_per_share, 2),
        "first_entry_pct": 30,
        "first_entry_shares": first_entry_shares
    }


def classify_stock(item):
    score = item.get("score", 0)

    invalid = (
        not item.get("is_liquid_enough", False) or
        item.get("entry_status") in ["跌破取消", "過熱不追", "不列入"] or
        item.get("candle_score", 0) <= -20 or
        "跌破月線" in item.get("warnings", [])
    )

    if invalid:
        return None

    if score >= 225 and item.get("main_score", 0) >= 60 and item.get("sector_score", 0) >= 20:
        return "S"

    if score >= 175 and item.get("main_score", 0) >= 30 and item.get("sector_score", 0) >= 5:
        return "A"

    return None


def build_elite_results(s_results, a_results, market_info):
    if not market_info.get("allow_new_positions"):
        return []

    pool = s_results + a_results

    def rank_score(x):
        bonus = 0

        if x.get("entry_status") == "可觀察進場":
            bonus += 50

        if x.get("breakout_state") == "突破回採不破":
            bonus += 30

        if x.get("egg_zone") in ["蛋黃區", "蛋白區"]:
            bonus += 15

        if x.get("candle_score", 0) > 0:
            bonus += 15

        if x.get("risk_reward", 0) >= 2:
            bonus += 10

        return x.get("score", 0) + bonus

    filtered = []
    sector_count = {}

    for x in sorted(pool, key=rank_score, reverse=True):
        if x.get("entry_status") in ["跌破取消", "過熱不追", "不列入"]:
            continue

        sector = x.get("sector", "其他")
        if sector_count.get(sector, 0) >= 2:
            continue

        copied = dict(x)
        copied["elite_reason"] = "今日精選：通過AI分數、量價、位階、流動性與風控條件。"
        filtered.append(copied)
        sector_count[sector] = sector_count.get(sector, 0) + 1

        if len(filtered) >= MAX_ELITE_RESULTS:
            break

    return filtered


# =====================================================
# 候選觀察池
# =====================================================
def load_candidate_pool():
    return read_json_file(CANDIDATE_FILE, {
        "updated_at": "-",
        "candidates": {},
        "entry_alerts": []
    })


def save_candidate_pool(data):
    write_json_file(CANDIDATE_FILE, data)


def update_candidate_pool(items):
    old_data = load_candidate_pool()
    old_candidates = old_data.get("candidates", {})

    now = taiwan_now()
    today = today_str()

    new_candidates = {}
    entry_alerts = []

    for item in items:
        if item.get("level") not in ["S", "A"]:
            continue

        if item.get("entry_status") in ["跌破取消", "過熱不追", "不列入", "流動性不足"]:
            continue

        symbol = item["symbol"]
        old = old_candidates.get(symbol, {})

        previous_status = old.get("current_status", "-")
        current_status = item.get("entry_status", "-")

        candidate = {
            "symbol": symbol,
            "name": item.get("name", symbol),
            "level": item.get("level", "-"),
            "sector": item.get("sector", "-"),
            "previous_status": previous_status,
            "current_status": current_status,
            "buy_type": item.get("buy_type", "-"),
            "score": item.get("score", 0),
            "support_price": item.get("support_price", 0),
            "next_entry_low": item.get("next_entry_low", 0),
            "next_entry_high": item.get("next_entry_high", 0),
            "no_entry_price": item.get("no_entry_price", 0),
            "invalid_price": item.get("invalid_price", 0),
            "practical_stop": item.get("practical_stop", 0),
            "initial_stop": item.get("initial_stop", 0),
            "risk_reward": item.get("risk_reward", 0),
            "egg_zone": item.get("egg_zone", "-"),
            "candle_signal": item.get("candle_signal", "-"),
            "liquidity_level": item.get("liquidity_level", "-"),
            "avg_volume_20_lots": item.get("avg_volume_20_lots", 0),
            "ai_next_action": item.get("ai_next_action", "-"),
            "trade_plan_note": item.get("trade_plan_note", "-"),
            "updated_at": now,
            "first_seen": old.get("first_seen", today),
            "last_seen": today
        }

        new_candidates[symbol] = candidate

        if current_status == "可觀察進場":
            alert = dict(candidate)
            alert["alert_type"] = "今日進場提醒"
            entry_alerts.append(alert)

    sorted_candidates = dict(
        sorted(
            new_candidates.items(),
            key=lambda kv: (
                1 if kv[1].get("current_status") == "可觀察進場" else 0,
                kv[1].get("score", 0)
            ),
            reverse=True
        )
    )

    entry_alerts = sorted(entry_alerts, key=lambda x: x.get("score", 0), reverse=True)[:MAX_ENTRY_ALERTS]

    data = {
        "updated_at": now,
        "candidates": sorted_candidates,
        "entry_alerts": entry_alerts
    }

    save_candidate_pool(data)
    return data


# =====================================================
# 持股管理 AI
# =====================================================
def calc_holding_management(track, df):
    if df is None or df.empty:
        return track

    curr = safe_float(df["Close"].iloc[-1])
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    atr = safe_float(calc_atr(df).iloc[-1])
    entry = safe_float(track.get("price"))
    support = safe_float(track.get("support_price")) or safe_float(track.get("entry_price")) or entry

    old_invalid_price = safe_float(track.get("invalid_price"))
    if not old_invalid_price:
        old_invalid_price = safe_float(track.get("initial_stop"))

    if not old_invalid_price:
        old_invalid_price = support * 0.99

    practical_stop = safe_float(track.get("practical_stop"))
    if not practical_stop and atr:
        practical_stop = max(support * 0.985, entry - atr * 1.5)
    elif not practical_stop:
        practical_stop = support * 0.985

    practical_stop = round(practical_stop, 2)
    invalid_price = round(old_invalid_price, 2)

    entry_date = track.get("date", today_str())
    entry_dt = pd.to_datetime(entry_date, errors="coerce")

    if pd.notna(entry_dt):
        after_entry = df[df.index >= entry_dt]
    else:
        after_entry = df.tail(60)

    if after_entry.empty:
        after_entry = df.tail(60)

    highest = max(safe_float(after_entry["High"].max()), entry)

    conservative_trail = round(highest - atr * 2.0, 2) if atr else 0
    standard_trail = round(highest - atr * 2.5, 2) if atr else 0
    loose_trail = round(highest - atr * 3.0, 2) if atr else 0

    if standard_trail > entry and curr >= entry + atr * 1.5:
        trail_zone_name = "移動停利區"
        standard_label = "標準移動停利"
        standard_break_action = "跌破標準移動停利，建議停利出場。"
    else:
        trail_zone_name = "移動停損區"
        standard_label = "標準移動停損"
        standard_break_action = "跌破標準移動停損，建議停損出場。"

    if conservative_trail > entry:
        conservative_action = "跌破保守移動停利，建議先減碼或鎖利。"
    else:
        conservative_action = "跌破保守移動停損，代表接近成本防守，建議先減碼或提高警戒。"

    if loose_trail > entry:
        loose_action = "跌破寬鬆移動停利，趨勢轉弱，建議全出。"
    else:
        loose_action = "跌破寬鬆移動停損，趨勢轉弱，建議全出。"

    low_120 = safe_float(low.rolling(120).min().iloc[-1])
    high_120 = safe_float(high.rolling(120).max().iloc[-1])

    egg = analyze_egg_position(curr, low_120, high_120)

    start_low = min(safe_float(after_entry["Low"].min()), support)
    first_wave_target = round(support + (support - start_low), 2)
    second_wave_target = round(support + (support - start_low) * 1.618, 2)
    strong_wave_target = round(support + (support - start_low) * 2.0, 2)

    atr_target_1 = round(entry + atr * 3, 2) if atr else 0
    atr_target_2 = round(entry + atr * 5, 2) if atr else 0
    atr_target_3 = round(entry + atr * 8, 2) if atr else 0

    target_1 = max(first_wave_target, atr_target_1)
    target_2 = max(second_wave_target, atr_target_2)
    target_3 = max(strong_wave_target, atr_target_3)

    progress_to_t1 = round(curr / target_1 * 100, 2) if target_1 else 0
    pnl = round(pct(curr, entry), 2) if entry else 0

    candle = analyze_candle_pattern(df)

    support_broken = curr < support * 0.995
    support_stand_back = (
        safe_float(close.iloc[-2]) < support * 0.995 and
        curr >= support * 1.003
    ) if len(close) >= 2 else False

    if curr <= practical_stop:
        ai_status = "實戰停損"
        ai_exit_notice = "已跌破實戰停損價，建議停損出場，避免賺少賠多或虧損擴大。"

    elif curr <= invalid_price:
        ai_status = "假突破失效"
        ai_exit_notice = "已跌破假突破失效價，候選邏輯失效，建議取消或全出。"

    elif support_broken:
        ai_status = "支撐失守"
        ai_exit_notice = "已跌破支撐點，建議先出場或至少減碼。"

    elif curr <= standard_trail:
        ai_status = "跌破標準風控"
        ai_exit_notice = standard_break_action

    elif curr <= conservative_trail:
        ai_status = "跌破保守風控"
        ai_exit_notice = conservative_action

    elif curr <= loose_trail:
        ai_status = "趨勢轉弱"
        ai_exit_notice = loose_action

    elif support_stand_back:
        ai_status = "假跌破站回"
        ai_exit_notice = "昨日跌破支撐但今日站回，可能是假跌破轉強，可重新觀察試單。"

    elif progress_to_t1 >= 98 and candle.get("candle_score", 0) < 0:
        ai_status = "接近滿足點需停利"
        ai_exit_notice = "接近第一滿足點且K棒轉弱，建議考慮部分停利。"

    elif progress_to_t1 >= 98:
        ai_status = "接近第一滿足點"
        ai_exit_notice = "已接近第一波滿足點，續抱但須提高停利警戒。"

    elif curr >= target_2 * 0.98:
        ai_status = "接近第二滿足點"
        ai_exit_notice = "接近第二滿足點，建議逐步鎖利。"

    else:
        ai_status = "續抱"
        ai_exit_notice = "尚未跌破AI移動風控區、支撐點或實戰停損價，依策略續抱。"

    track.update({
        "curr": round(curr, 2),
        "pnl": pnl,
        "highest_since_entry": round(highest, 2),
        "atr": round(atr, 2),
        "support_price": round(support, 2),
        "practical_stop": practical_stop,
        "initial_stop": practical_stop,
        "invalid_price": invalid_price,
        "conservative_trail": conservative_trail,
        "standard_trail": standard_trail,
        "loose_trail": loose_trail,
        "trail_range": f"{loose_trail} ～ {conservative_trail}",
        "trail_zone_name": trail_zone_name,
        "standard_label": standard_label,
        "conservative_action": conservative_action,
        "standard_action": standard_break_action,
        "loose_action": loose_action,
        "egg_zone_now": egg["egg_zone"],
        "egg_position_pct_now": egg["egg_position_pct"],
        "wave_start_price": round(start_low, 2),
        "wave_target_1": round(target_1, 2),
        "wave_target_2": round(target_2, 2),
        "wave_target_3": round(target_3, 2),
        "progress_to_target_1": progress_to_t1,
        "candle_signal_now": candle["candle_signal"],
        "ai_holding_status": ai_status,
        "ai_exit_notice": ai_exit_notice
    })

    return track


# =====================================================
# 檔案讀寫
# =====================================================
def load_track():
    return read_json_file(TRACK_FILE, [])


def save_track(data):
    write_json_file(TRACK_FILE, data)


def load_trade_log():
    return read_json_file(TRADE_LOG_FILE, [])


def save_trade_log(data):
    write_json_file(TRADE_LOG_FILE, data)


def save_scan_results(data):
    write_json_file(RESULT_FILE, data)


def load_scan_results():
    return read_json_file(RESULT_FILE, {
        "updated_at": "尚未掃描",
        "market_status": "尚未掃描",
        "market_score": 0,
        "risk_mode": "-",
        "risk_switch": "-",
        "allow_new_positions": False,
        "risk_note": "-",
        "stock_pool_count": 0,
        "elite_count": 0,
        "s_count": 0,
        "a_count": 0,
        "elite_results": [],
        "s_results": [],
        "sector_rankings": [],
        "candidate_count": 0,
        "entry_alerts": [],
        "candidate_pool": []
    })


def calc_track_stats(tracks):
    valid = [x for x in tracks if isinstance(x.get("pnl"), (int, float))]

    if not valid:
        return 0, 0

    wins = [x for x in valid if x["pnl"] > 0]
    avg = sum(x["pnl"] for x in valid) / len(valid)

    return round(len(wins) / len(valid) * 100, 2), round(avg, 2)


# =====================================================
# 全市場掃描
# =====================================================
def scan_market():
    save_scan_status("running", "正在建立全市場股票池。")
    print("開始掃描：", taiwan_now())

    stocks = get_stock_pool()
    market_info = get_market_status()
    market_score = market_info["market_score"]

    analyzed = []
    total = len(stocks)

    save_scan_status("running", f"股票池建立完成：{total} 檔，開始掃描個股。")

    for i, (symbol, info) in enumerate(stocks.items(), start=1):
        try:
            name = info.get("name", symbol)
            industry = info.get("industry", "其他")
            sector = infer_sector(symbol, name, industry)

            df = download_stock(symbol, "1y")
            result = analyze_stock(df)

            if not result:
                continue

            item = {
                "symbol": symbol,
                "name": name,
                "industry": industry,
                "sector": sector,
                "df": df,
            }

            item.update(result)
            analyzed.append(item)

            if i % 100 == 0:
                save_scan_status("running", f"正在掃描全市場：{i}/{total}")

            time.sleep(0.02)

        except Exception as e:
            print("單檔掃描失敗：", symbol, e)
            continue

    sector_scores = calc_sector_scores(analyzed)
    sector_rankings = build_sector_rankings(sector_scores)
    sector_rank_map = {x["sector"]: x["rank"] for x in sector_rankings}

    s_results = []
    a_results = []

    for item in analyzed:
        sector_data = sector_scores.get(item["sector"], {
            "sector_score": 0,
            "sector_avg_5d": 0,
            "sector_avg_20d": 0,
            "sector_avg_main": 0,
            "sector_strong_ratio": 0,
            "sector_stock_count": 0
        })

        item.update(sector_data)
        item["sector_rank"] = sector_rank_map.get(item["sector"], 999)

        item["score"] = round(
            item.get("technical_score", 0) +
            item.get("sector_score", 0) +
            market_score,
            1
        )

        buy_type, entry_status, entry_reason = determine_entry_status(item)
        item["buy_type"] = buy_type
        item["entry_status"] = entry_status
        item["entry_reason"] = entry_reason

        item.update(build_trade_plan(item))
        item.update(calc_position_sizing(item, market_info))

        level = classify_stock(item)

        if not level:
            continue

        item["level"] = level

        if not market_info.get("allow_new_positions"):
            item["entry_status"] = "禁止新倉"
            item["entry_reason"] = market_info.get("risk_note", "市場風險偏高。")

        item.pop("df", None)

        if level == "S":
            s_results.append(item)
        else:
            a_results.append(item)

    s_results = sorted(s_results, key=lambda x: x["score"], reverse=True)
    a_results = sorted(a_results, key=lambda x: x["score"], reverse=True)

    elite_results = build_elite_results(s_results, a_results, market_info)

    all_candidates = s_results + a_results
    candidate_data = update_candidate_pool(all_candidates)

    candidate_pool_list = list(candidate_data.get("candidates", {}).values())[:MAX_CANDIDATE_DISPLAY]
    entry_alerts = candidate_data.get("entry_alerts", [])

    scan_data = {
        "updated_at": taiwan_now(),
        "market_status": market_info["market_status"],
        "market_score": market_info["market_score"],
        "risk_mode": market_info["risk_mode"],
        "risk_switch": market_info["risk_switch"],
        "allow_new_positions": market_info["allow_new_positions"],
        "risk_note": market_info["risk_note"],
        "risk_multiplier": market_info["risk_multiplier"],
        "stock_pool_count": total,
        "elite_count": len(elite_results),
        "s_count": len(s_results),
        "a_count": len(a_results),
        "elite_results": elite_results,
        "s_results": s_results[:MAX_S_RESULTS],
        "sector_rankings": sector_rankings,
        "candidate_count": len(candidate_pool_list),
        "candidate_pool": candidate_pool_list,
        "entry_alerts": entry_alerts
    }

    save_scan_results(scan_data)

    save_scan_status(
        "done",
        f"掃描完成：股票池 {total} 檔，今日精選 {len(elite_results)} 檔，S級 {len(s_results)} 檔，A級候選 {len(a_results)} 檔，進場提醒 {len(entry_alerts)} 檔。"
    )


# =====================================================
# 首頁
# =====================================================
@app.route("/")
def index():
    scan_data = load_scan_results()
    scan_status_data = load_scan_status()

    twii = get_index_price("^TWII")
    otc = get_index_price("^TWOII")

    tracks = load_track()
    trade_logs = load_trade_log()

    updated_tracks = []

    for t in tracks:
        df = download_stock(t["symbol"], "1y")
        t = calc_holding_management(t, df)
        updated_tracks.append(t)

    save_track(updated_tracks)

    winrate, avg = calc_track_stats(updated_tracks)

    return render_template(
        "index.html",
        now=taiwan_now(),
        twii=twii,
        otc=otc,

        market_status=scan_data.get("market_status", "尚未掃描"),
        market_score=scan_data.get("market_score", 0),
        risk_mode=scan_data.get("risk_mode", "-"),
        risk_switch=scan_data.get("risk_switch", "-"),
        allow_new_positions=scan_data.get("allow_new_positions", False),
        risk_note=scan_data.get("risk_note", "-"),
        risk_multiplier=scan_data.get("risk_multiplier", 0),
        scan_updated_at=scan_data.get("updated_at", "尚未掃描"),
        stock_pool_count=scan_data.get("stock_pool_count", 0),

        elite_count=scan_data.get("elite_count", 0),
        s_count=scan_data.get("s_count", 0),
        a_count=scan_data.get("a_count", 0),
        candidate_count=scan_data.get("candidate_count", 0),

        elite_results=scan_data.get("elite_results", []),
        s_results=scan_data.get("s_results", []),
        sector_rankings=scan_data.get("sector_rankings", []),
        candidate_pool=scan_data.get("candidate_pool", []),
        entry_alerts=scan_data.get("entry_alerts", []),

        scan_status=scan_status_data.get("status", "idle"),
        scan_message=scan_status_data.get("message", "尚未掃描"),
        scan_status_time=scan_status_data.get("updated_at", "-"),

        tracks=updated_tracks,
        trade_logs=trade_logs[-10:],
        winrate=winrate,
        avg=avg,
        account_size=ACCOUNT_SIZE,
        risk_per_trade=round(RISK_PER_TRADE * 100, 2)
    )


# =====================================================
# 路由
# =====================================================
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


def find_item_by_symbol(symbol):
    scan_data = load_scan_results()

    all_items = (
        scan_data.get("elite_results", []) +
        scan_data.get("s_results", [])
    )

    for x in all_items:
        if x.get("symbol") == symbol:
            return x

    candidate_data = load_candidate_pool()
    candidate = candidate_data.get("candidates", {}).get(symbol)

    if candidate:
        return candidate

    return None


@app.route("/track/<symbol>")
def track(symbol):
    item = find_item_by_symbol(symbol)

    if not item:
        return redirect(url_for("index"))

    data = load_track()

    if any(x["symbol"] == symbol for x in data):
        return redirect(url_for("index"))

    entry_price = safe_float(item.get("next_entry_low")) or safe_float(item.get("price"))

    data.append({
        "symbol": symbol,
        "name": item.get("name", symbol),
        "level": item.get("level", "-"),
        "sector": item.get("sector", "-"),
        "buy_type": item.get("buy_type", "-"),
        "price": entry_price,
        "entry_price": entry_price,
        "support_price": safe_float(item.get("support_price")),
        "no_entry_price": safe_float(item.get("no_entry_price")),
        "invalid_price": safe_float(item.get("invalid_price")),
        "practical_stop": safe_float(item.get("practical_stop")),
        "initial_stop": safe_float(item.get("practical_stop")) or safe_float(item.get("initial_stop")),
        "standard_trailing_stop": safe_float(item.get("standard_trailing_stop")),
        "risk_reward": safe_float(item.get("risk_reward")),
        "date": today_str(),
        "ai_holding_status": "剛加入追蹤",
        "ai_exit_notice": "等待隔日開盤與支撐確認。",
        "highest_since_entry": entry_price,
        "trail_range": "-",
        "trail_zone_name": "AI移動風控區",
        "wave_target_1": 0,
        "wave_target_2": 0,
        "wave_target_3": 0
    })

    save_track(data)
    return redirect(url_for("index"))


@app.route("/untrack/<symbol>")
def untrack(symbol):
    data = [x for x in load_track() if x["symbol"] != symbol]
    save_track(data)
    return redirect(url_for("index"))


@app.route("/close-trade/<symbol>")
def close_trade(symbol):
    tracks = load_track()
    logs = load_trade_log()

    item = next((x for x in tracks if x["symbol"] == symbol), None)

    if not item:
        return redirect(url_for("index"))

    logs.append({
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "entry_price": item.get("price"),
        "exit_price": item.get("curr"),
        "pnl_pct": item.get("pnl"),
        "entry_date": item.get("date"),
        "exit_date": today_str(),
        "level": item.get("level"),
        "buy_type": item.get("buy_type"),
        "sector": item.get("sector"),
        "ai_holding_status": item.get("ai_holding_status"),
        "ai_exit_notice": item.get("ai_exit_notice")
    })

    tracks = [x for x in tracks if x["symbol"] != symbol]

    save_trade_log(logs)
    save_track(tracks)

    return redirect(url_for("index"))


# =====================================================
# 每天 16:00 自動掃描
# =====================================================
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
