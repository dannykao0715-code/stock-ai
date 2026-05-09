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

MAX_ENTRY_ALERTS = 8
MAX_CANDIDATE_DISPLAY = 40

ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "1000000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))

MIN_AVG_VOLUME_20 = 500_000
MIN_AVG_AMOUNT_20 = 5_000_000

MIN_RISK_REWARD_ENTRY = 1.5
GOOD_RISK_REWARD = 2.0

MIN_FEEDBACK_SAMPLE = 3
MAX_FEEDBACK_BONUS = 35
MAX_FEEDBACK_PENALTY = -35

is_scanning = False


# =====================================================
# 通用工具
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


def safe_int(x, default=0):
    try:
        return int(float(x))
    except Exception:
        return default


def round2(x):
    return round(safe_float(x), 2)


def pct(a, b):
    try:
        a = safe_float(a)
        b = safe_float(b)
        if b == 0:
            return 0
        return (a - b) / b * 100
    except Exception:
        return 0


def days_between(start, end):
    try:
        d1 = datetime.strptime(str(start)[:10], "%Y-%m-%d")
        d2 = datetime.strptime(str(end)[:10], "%Y-%m-%d")
        return max((d2 - d1).days, 0)
    except Exception:
        return 0


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


def get_risk_reward_group(risk_reward):
    rr = safe_float(risk_reward)
    if rr >= 2.5:
        return "風報比 2.5以上"
    if rr >= 2.0:
        return "風報比 2.0~2.5"
    if rr >= 1.5:
        return "風報比 1.5~2.0"
    return "風報比 低於1.5"


# =====================================================
# 股票池：全市場 + 快取 + 保底龍頭股
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
        "2356.TW": ("英業達", "AI伺服器"),
        "2324.TW": ("仁寶", "AI伺服器"),

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

    return {s: {"name": n, "industry": ind} for s, (n, ind) in base.items()}


def normalize_stock_item(code, name, industry="其他", suffix=".TW"):
    code = str(code).strip()
    name = str(name).strip()
    industry = str(industry).strip() if industry else "其他"

    if len(code) == 4 and code.isdigit() and name:
        return f"{code}{suffix}", {"name": name, "industry": industry}

    return None, None


def fetch_json_url(url, timeout=20):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*"
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


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
        if item.get(k):
            code = item.get(k)
            break

    for k in name_keys:
        if item.get(k):
            name = item.get(k)
            break

    for k in industry_keys:
        if item.get(k):
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
            rows = data.get("data", []) if isinstance(data, dict) else data if isinstance(data, list) else []
            temp = {}

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
# 族群與龍頭
# =====================================================
SECTOR_LEADERS = {
    "半導體": ["2330.TW", "2303.TW"],
    "IC設計": ["2454.TW", "3034.TW", "2379.TW", "3661.TW", "3443.TW", "5274.TWO"],
    "AI伺服器": ["2317.TW", "2382.TW", "3231.TW", "6669.TW"],
    "散熱": ["3017.TW", "3324.TWO", "3653.TW", "8996.TWO"],
    "PCB": ["2383.TW", "3037.TW", "8046.TW", "3189.TWO"],
    "金融": ["2881.TW", "2882.TW", "2886.TW", "2891.TW"],
    "航運": ["2603.TW", "2609.TW", "2615.TW"],
    "航空": ["2618.TW", "2610.TW"],
    "重電": ["1513.TW", "1519.TW", "1609.TW", "1618.TW"],
    "生技": ["6446.TW", "1760.TW", "4743.TWO", "4105.TWO", "6472.TW"],
}


def infer_sector(symbol, name, industry):
    if industry and industry not in ["上市", "上櫃", "其他"]:
        return industry

    name = name or ""

    groups = {
        "AI伺服器": ["廣達", "緯創", "緯穎", "鴻海", "英業達", "技嘉", "華碩", "仁寶"],
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
# 技術與量價指標
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


def analyze_egg_position(price, low_value, high_value):
    if not high_value or not low_value or high_value <= low_value:
        return {
            "egg_zone": "無法判斷",
            "egg_score": 0,
            "egg_position_pct": 0,
            "egg_note": "區間不足"
        }

    pos = (price - low_value) / (high_value - low_value) * 100

    if pos <= 35:
        return {
            "egg_zone": "蛋黃區",
            "egg_score": 25,
            "egg_position_pct": round2(pos),
            "egg_note": "低位階，安全邊際較高"
        }

    if pos <= 70:
        return {
            "egg_zone": "蛋白區",
            "egg_score": 15,
            "egg_position_pct": round2(pos),
            "egg_note": "主升段或中段"
        }

    if pos <= 90:
        return {
            "egg_zone": "蛋殼區",
            "egg_score": -5,
            "egg_position_pct": round2(pos),
            "egg_note": "偏高位階，需提高停利警戒"
        }

    return {
        "egg_zone": "蛋殼過熱區",
        "egg_score": -25,
        "egg_position_pct": round2(pos),
        "egg_note": "過熱，不追高"
    }


def analyze_candle_pattern(df):
    if df is None or len(df) < 5:
        return {
            "candle_signal": "資料不足",
            "candle_score": 0,
            "candle_note": "K棒不足"
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
            "candle_note": "買盤反攻"
        }

    if is_red and body / rng >= 0.55 and pct(c, o) >= 2 and c >= h - rng * 0.25:
        return {
            "candle_signal": "帶量長紅K",
            "candle_score": 20,
            "candle_note": "買盤積極"
        }

    if lower / rng >= 0.45 and is_red:
        return {
            "candle_signal": "下影支撐紅K",
            "candle_score": 18,
            "candle_note": "支撐區有承接"
        }

    if is_black and pc > po and c < po and o > pc:
        return {
            "candle_signal": "黑K吞噬",
            "candle_score": -25,
            "candle_note": "轉弱K棒"
        }

    if is_black and body / rng >= 0.55 and pct(o, c) >= 2:
        return {
            "candle_signal": "長黑K",
            "candle_score": -25,
            "candle_note": "賣壓明顯"
        }

    if upper / rng >= 0.45:
        return {
            "candle_signal": "長上影K",
            "candle_score": -15,
            "candle_note": "上方壓力大"
        }

    return {
        "candle_signal": "中性K",
        "candle_score": 0,
        "candle_note": "K棒中性"
    }


def calc_liquidity(df):
    close = df["Close"]
    volume = df["Volume"]

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
        "avg_volume_20": round2(avg_volume_20),
        "avg_volume_20_lots": round2(avg_volume_20 / 1000),
        "avg_amount_20": round2(avg_amount_20),
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

    base_money = safe_float(ma_money_20.iloc[-1])
    money_ratio = safe_float(ma_money_5.iloc[-1] / base_money) if base_money else 0

    up_day = close > open_
    strong_up = (close > open_) & (pct(close, open_) > 2)
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
        "main_score": round2(score),
        "main_signals": signals,
        "money_ratio": round2(money_ratio),
        "main_buy_days": main_buy_days,
        "strong_buy_days": strong_buy_days
    }


# =====================================================
# Top-Down 大盤
# =====================================================
def analyze_index(symbol):
    df = download_stock(symbol, "1y")

    if df is None or len(df) < 120:
        return {
            "ok": False,
            "price": "-",
            "score": 0,
            "status": "資料不足",
            "egg_zone": "無法判斷",
            "pressure_note": "資料不足"
        }

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    price = safe_float(close.iloc[-1])
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()

    ma20_now = safe_float(ma20.iloc[-1])
    ma60_now = safe_float(ma60.iloc[-1])
    ma120_now = safe_float(ma120.iloc[-1])

    egg = analyze_egg_position(
        price,
        safe_float(low.rolling(120).min().iloc[-1]),
        safe_float(high.rolling(120).max().iloc[-1])
    )

    prev_high_60 = safe_float(high.rolling(60).max().iloc[-2])

    score = 0

    if price > ma20_now:
        score += 10

    if price > ma60_now:
        score += 10

    if ma20_now > ma60_now:
        score += 10

    if ma20_now > ma60_now > ma120_now:
        score += 15

    score += egg["egg_score"]

    if prev_high_60 and price < prev_high_60 and pct(prev_high_60, price) <= 3:
        score -= 10
        pressure_note = "接近前高壓力"
    elif prev_high_60 and price > prev_high_60:
        score += 15
        pressure_note = "突破前高"
    else:
        pressure_note = "前方壓力尚可"

    if score >= 45:
        status = "強多"
    elif score >= 25:
        status = "多頭"
    elif score >= 5:
        status = "盤整偏多"
    elif score >= -10:
        status = "盤整"
    else:
        status = "轉弱"

    return {
        "ok": True,
        "price": round2(price),
        "score": round2(score),
        "status": status,
        "egg_zone": egg["egg_zone"],
        "egg_position_pct": egg["egg_position_pct"],
        "pressure_note": pressure_note
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
            "risk_note": "大盤資料不足，暫不建議建立新倉。",
            "market_egg_zone": "無法判斷",
            "market_pressure_note": "資料不足"
        }

    score = twii["score"]

    if otc["ok"]:
        score += min(max(otc["score"] * 0.35, -10), 15)

    if twii["status"] == "強多" and otc.get("status") in ["強多", "多頭", "盤整偏多"]:
        return {
            "market_status": "強多市場",
            "market_score": 25,
            "risk_mode": "積極",
            "risk_switch": "允許新倉",
            "allow_new_positions": True,
            "risk_multiplier": 1.0,
            "risk_note": "大盤與櫃買偏多，允許正常部位。",
            "market_egg_zone": twii["egg_zone"],
            "market_pressure_note": twii["pressure_note"]
        }

    if score >= 25:
        return {
            "market_status": "多頭市場",
            "market_score": 15,
            "risk_mode": "正常",
            "risk_switch": "允許新倉",
            "allow_new_positions": True,
            "risk_multiplier": 0.8,
            "risk_note": "大盤偏多，可進場但部位略保守。",
            "market_egg_zone": twii["egg_zone"],
            "market_pressure_note": twii["pressure_note"]
        }

    if score >= 5:
        return {
            "market_status": "盤整偏多",
            "market_score": 5,
            "risk_mode": "保守",
            "risk_switch": "只允許高品質",
            "allow_new_positions": True,
            "risk_multiplier": 0.5,
            "risk_note": "大盤盤整偏多，只做高勝率與好風報比標的。",
            "market_egg_zone": twii["egg_zone"],
            "market_pressure_note": twii["pressure_note"]
        }

    if score >= -10:
        return {
            "market_status": "盤整偏弱",
            "market_score": -10,
            "risk_mode": "防守",
            "risk_switch": "降低部位",
            "allow_new_positions": True,
            "risk_multiplier": 0.25,
            "risk_note": "大盤盤整偏弱，僅允許非常明確的交易計畫。",
            "market_egg_zone": twii["egg_zone"],
            "market_pressure_note": twii["pressure_note"]
        }

    return {
        "market_status": "轉弱市場",
        "market_score": -25,
        "risk_mode": "禁止新倉",
        "risk_switch": "禁止新倉",
        "allow_new_positions": False,
        "risk_multiplier": 0,
        "risk_note": "大盤轉弱，禁止新倉，只管理持股。",
        "market_egg_zone": twii["egg_zone"],
        "market_pressure_note": twii["pressure_note"]
    }


def get_index_price(symbol):
    df = download_stock(symbol, "5d")

    if df is None or df.empty:
        return "-"

    return round2(df["Close"].iloc[-1])


# =====================================================
# 支撐壓力、盤整、突破回採
# =====================================================
def detect_resistance_zone(df):
    if df is None or len(df) < 80:
        return {
            "resistance_low": 0,
            "resistance_high": 0,
            "resistance_note": "資料不足"
        }

    recent = df.iloc[-120:-1] if len(df) >= 121 else df.iloc[:-1]

    if recent.empty:
        return {
            "resistance_low": 0,
            "resistance_high": 0,
            "resistance_note": "資料不足"
        }

    idx = recent["High"].idxmax()
    row = df.loc[idx]

    low = round2(row["Low"])
    high = round2(row["High"])
    price = round2(df["Close"].iloc[-1])

    if price < low:
        note = "尚未進入前高壓力區"
    elif low <= price <= high:
        note = "正在前高壓力區內消化賣壓"
    elif price > high:
        note = "已突破前高壓力區上緣"
    else:
        note = "壓力區判斷中"

    return {
        "resistance_low": low,
        "resistance_high": high,
        "resistance_note": note
    }


def detect_consolidation_zone(df, lookback=30):
    if df is None or len(df) < lookback + 20:
        return {
            "box_low": 0,
            "box_high": 0,
            "box_mid": 0,
            "box_range_pct": 0,
            "box_note": "資料不足",
            "has_box": False
        }

    recent = df.tail(lookback)
    high = safe_float(recent["High"].max())
    low = safe_float(recent["Low"].min())
    mid = (high + low) / 2
    price = safe_float(df["Close"].iloc[-1])
    box_range_pct = pct(high, low)

    has_box = box_range_pct <= 18

    if not has_box:
        note = "近期盤整區不明顯"
    elif price <= low * 1.03:
        note = "靠近盤整下緣，可觀察試單"
    elif price >= high * 0.985 and price <= high * 1.015:
        note = "接近盤整上緣，等待突破或回落"
    elif price > high * 1.015:
        note = "已突破盤整上緣，等待回採"
    else:
        note = "盤整區間內"

    return {
        "box_low": round2(low),
        "box_high": round2(high),
        "box_mid": round2(mid),
        "box_range_pct": round2(box_range_pct),
        "box_note": note,
        "has_box": has_box
    }


def analyze_breakout_pullback(df, resistance, box):
    if df is None or len(df) < 80:
        return {
            "breakout_state": "資料不足",
            "breakout_score": 0,
            "breakout_note": "資料不足"
        }

    price = safe_float(df["Close"].iloc[-1])

    resistance_low = resistance.get("resistance_low", 0)
    resistance_high = resistance.get("resistance_high", 0)
    box_low = box.get("box_low", 0)
    box_high = box.get("box_high", 0)

    recent = df.tail(8)

    broke_resistance = bool(resistance_high and price > resistance_high * 1.003)

    resistance_pullback = (
        broke_resistance and
        bool((recent["Low"] <= resistance_high * 1.015).any()) and
        price >= resistance_high * 0.995
    )

    if broke_resistance and resistance_pullback:
        return {
            "breakout_state": "前高區突破回採不破",
            "breakout_score": 35,
            "breakout_note": "突破前高K棒壓力區上緣後回採不破"
        }

    if broke_resistance:
        return {
            "breakout_state": "突破前高區等回採",
            "breakout_score": 15,
            "breakout_note": "已突破前高區，但尚未回採確認，不追高"
        }

    if resistance_low and resistance_low <= price <= resistance_high:
        return {
            "breakout_state": "前高壓力區盤整",
            "breakout_score": 10,
            "breakout_note": "價格正在前高K棒壓力區內消化賣壓"
        }

    if box.get("has_box") and box_low and price <= box_low * 1.035 and price >= box_low * 0.995:
        return {
            "breakout_state": "盤整下緣試單",
            "breakout_score": 18,
            "breakout_note": "價格靠近盤整區下緣，若K棒轉強可小部位試單"
        }

    if box.get("has_box") and box_high and price > box_high * 1.003:
        return {
            "breakout_state": "突破盤整等回採",
            "breakout_score": 15,
            "breakout_note": "突破盤整上緣，等待回採上緣不破"
        }

    if resistance_high and price < resistance_low * 0.985 and df.tail(8)["Close"].max() > resistance_low:
        return {
            "breakout_state": "前高區失守",
            "breakout_score": -25,
            "breakout_note": "進入前高壓力區後失守下緣，轉弱"
        }

    return {
        "breakout_state": "尚未到買點",
        "breakout_score": 0,
        "breakout_note": "尚未突破或靠近有效支撐區"
    }


# =====================================================
# 個股分析
# =====================================================
def analyze_stock(df):
    if df is None or len(df) < 120:
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    price = safe_float(close.iloc[-1])

    if not price:
        return None

    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()

    ma20_now = safe_float(ma20.iloc[-1])
    ma60_now = safe_float(ma60.iloc[-1])
    ma120_now = safe_float(ma120.iloc[-1])

    atr = safe_float(calc_atr(df).iloc[-1])
    atr_pct = atr / price * 100 if atr else 0

    change_5d = pct(close.iloc[-1], close.iloc[-5])
    change_20d = pct(close.iloc[-1], close.iloc[-20])
    change_60d = pct(close.iloc[-1], close.iloc[-60])

    low_120 = safe_float(low.rolling(120).min().iloc[-1])
    high_120 = safe_float(high.rolling(120).max().iloc[-1])

    score = 0
    signals = []
    warnings = []

    if price > ma20_now:
        score += 10
        signals.append("站上月線")
    else:
        score -= 15
        warnings.append("跌破月線")

    if price > ma60_now:
        score += 10
        signals.append("站上季線")

    if ma20_now > ma60_now:
        score += 10
        signals.append("月線大於季線")

    if ma20_now > ma60_now > ma120_now:
        score += 20
        signals.append("多頭排列")

    if 1 <= change_5d <= 15:
        score += 10
        signals.append("短線動能健康")

    if change_20d > 5:
        score += 10
        signals.append("波段轉強")

    if change_60d > 10:
        score += 10
        signals.append("中期趨勢轉強")

    if change_5d > 22:
        score -= 30
        warnings.append("5日漲幅過熱")

    if change_20d > 45:
        score -= 25
        warnings.append("20日漲幅過熱")

    ma20_distance = pct(price, ma20_now)
    ma60_distance = pct(price, ma60_now)

    if ma20_distance > 12:
        score -= 20
        warnings.append("距離月線過遠")

    if atr_pct > 10:
        score -= 15
        warnings.append("波動過大")

    liquidity = calc_liquidity(df)
    main_force = calc_main_force(df)
    egg = analyze_egg_position(price, low_120, high_120)
    candle = analyze_candle_pattern(df)
    resistance = detect_resistance_zone(df)
    box = detect_consolidation_zone(df)
    breakout = analyze_breakout_pullback(df, resistance, box)

    score += liquidity["liquidity_score"]
    score += main_force["main_score"]
    score += egg["egg_score"]
    score += candle["candle_score"]
    score += breakout["breakout_score"]

    if liquidity["liquidity_warnings"]:
        warnings.extend(liquidity["liquidity_warnings"])

    result = {
        "price": round2(price),
        "technical_score": round2(score),
        "change_5d": round2(change_5d),
        "change_20d": round2(change_20d),
        "change_60d": round2(change_60d),
        "ma20": round2(ma20_now),
        "ma60": round2(ma60_now),
        "ma120": round2(ma120_now),
        "ma20_distance": round2(ma20_distance),
        "ma60_distance": round2(ma60_distance),
        "atr": round2(atr),
        "atr_pct": round2(atr_pct),
        "low_120": round2(low_120),
        "high_120": round2(high_120),
        "latest_low": round2(low.iloc[-1]),
        "latest_high": round2(high.iloc[-1]),
        "signals": signals,
        "warnings": warnings
    }

    result.update(liquidity)
    result.update(main_force)
    result.update(egg)
    result.update(candle)
    result.update(resistance)
    result.update(box)
    result.update(breakout)

    return result


# =====================================================
# 族群強度與龍頭強度
# =====================================================
def calc_sector_scores(items):
    sector_map = {}

    for item in items:
        sector_map.setdefault(item["sector"], []).append(item)

    scores = {}

    for sector, arr in sector_map.items():
        avg_5d = sum(x["change_5d"] for x in arr) / len(arr)
        avg_20d = sum(x["change_20d"] for x in arr) / len(arr)
        avg_main = sum(x["main_score"] for x in arr) / len(arr)

        strong = len([x for x in arr if x["technical_score"] >= 80])
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

        if score >= 60:
            status = "主流多頭"
        elif score >= 35:
            status = "轉強族群"
        elif score >= 15:
            status = "盤整偏多"
        elif score >= 0:
            status = "盤整"
        else:
            status = "弱勢族群"

        scores[sector] = {
            "sector": sector,
            "sector_score": round2(score),
            "sector_status": status,
            "sector_avg_5d": round2(avg_5d),
            "sector_avg_20d": round2(avg_20d),
            "sector_avg_main": round2(avg_main),
            "sector_strong_ratio": round2(strong_ratio * 100),
            "sector_stock_count": len(arr)
        }

    return scores


def calc_leader_strength(sector, analyzed_map):
    leaders = SECTOR_LEADERS.get(sector, [])

    if not leaders:
        return {
            "leader_score": 0,
            "leader_status": "無明確龍頭資料",
            "leader_names": "-"
        }

    total = 0
    count = 0
    names = []

    for symbol in leaders:
        item = analyzed_map.get(symbol)

        if not item:
            continue

        names.append(item.get("name", symbol))

        score = 0

        if item.get("price", 0) > item.get("ma20", 0):
            score += 10

        if item.get("price", 0) > item.get("ma60", 0):
            score += 10

        if item.get("ma20", 0) > item.get("ma60", 0):
            score += 10

        if item.get("breakout_state") in ["前高區突破回採不破", "突破前高區等回採", "突破盤整等回採"]:
            score += 20

        if item.get("main_score", 0) >= 40:
            score += 15

        if item.get("change_20d", 0) > 8:
            score += 10

        total += score
        count += 1

    if count == 0:
        return {
            "leader_score": 0,
            "leader_status": "龍頭資料不足",
            "leader_names": "-"
        }

    avg = total / count

    if avg >= 45:
        status = "龍頭強勢帶動"
    elif avg >= 25:
        status = "龍頭偏強"
    elif avg >= 10:
        status = "龍頭普通"
    else:
        status = "龍頭偏弱"

    return {
        "leader_score": round2(avg),
        "leader_status": status,
        "leader_names": "、".join(names[:3])
    }


def build_sector_rankings(sector_scores, leader_scores):
    rows = []

    for sector, row in sector_scores.items():
        x = dict(row)
        x.update(leader_scores.get(sector, {
            "leader_score": 0,
            "leader_status": "無明確龍頭資料",
            "leader_names": "-"
        }))

        x["combined_sector_score"] = round2(x["sector_score"] + x["leader_score"] * 0.5)
        rows.append(x)

    rows = sorted(rows, key=lambda x: x["combined_sector_score"], reverse=True)

    ranked = []

    for i, row in enumerate(rows[:10], start=1):
        row["rank"] = i
        ranked.append(row)

    return ranked


# =====================================================
# 交易計畫
# =====================================================
def determine_entry_status(item):
    warnings = item.get("warnings", [])
    breakout_state = item.get("breakout_state", "")
    candle_score = item.get("candle_score", 0)
    main_score = item.get("main_score", 0)
    sector_status = item.get("sector_status", "")
    leader_status = item.get("leader_status", "")

    if not item.get("is_liquid_enough"):
        return "流動性不足", "不列入", "成交量或成交金額不足"

    if "跌破月線" in warnings:
        return "弱勢取消型", "跌破取消", "跌破月線，結構轉弱"

    if breakout_state in ["前高區失守"]:
        return "壓力區失守型", "跌破取消", "前高壓力區失守"

    if (
        "距離月線過遠" in warnings or
        "5日漲幅過熱" in warnings or
        "20日漲幅過熱" in warnings or
        item.get("egg_zone") == "蛋殼過熱區" or
        candle_score <= -20
    ):
        return "過熱觀察型", "過熱不追", "位階或漲幅偏高，不追高"

    if sector_status in ["弱勢族群"] and leader_status in ["龍頭偏弱"]:
        return "族群弱勢型", "僅列觀察", "族群與龍頭偏弱"

    if breakout_state == "前高區突破回採不破":
        if candle_score > 0 or main_score >= 40:
            return "前高區突破回採型", "可觀察進場", "突破前高區後回採不破"
        return "前高區突破回採型", "等轉強K", "回採不破，但需K棒確認"

    if breakout_state == "盤整下緣試單":
        if candle_score > 0:
            return "盤整下緣試單型", "可觀察進場", "靠近盤整下緣且K棒轉強"
        return "盤整下緣試單型", "等轉強K", "靠近盤整下緣，但需K棒轉強"

    if breakout_state in ["突破前高區等回採", "突破盤整等回採"]:
        return "突破等回採型", "等回採", "突破後不追高，等待回採"

    if breakout_state == "前高壓力區盤整":
        return "前高區盤整型", "等突破", "前高壓力區消化賣壓"

    if item.get("technical_score", 0) >= 150 and main_score >= 35:
        return "低位啟動型", "等突破", "量價轉強，等待突破"

    return "觀察型", "僅列觀察", "尚未達到明確進場條件"


def build_trade_plan(item):
    price = item.get("price", 0)
    atr = item.get("atr", 0)

    resistance_low = item.get("resistance_low", 0)
    resistance_high = item.get("resistance_high", 0)
    box_low = item.get("box_low", 0)
    box_high = item.get("box_high", 0)

    breakout_state = item.get("breakout_state", "")

    if breakout_state == "盤整下緣試單" and box_low:
        support = box_low
        entry_low = round2(box_low * 1.003)
        entry_high = round2(min(box_low + atr * 0.6, box_low * 1.025))
        no_entry = round2(box_low * 0.995)
        invalid = round2(box_low * 0.985)
        target = round2(box_high if box_high else entry_low + atr * 3)

    elif resistance_high:
        support = resistance_high
        entry_low = round2(resistance_high * 1.003)
        entry_high = round2(min(resistance_high + atr * 0.6, resistance_high * 1.025))
        no_entry = round2(resistance_high * 0.995)
        invalid = round2(
            min(
                resistance_low * 0.99 if resistance_low else resistance_high * 0.985,
                resistance_high - atr * 1.8
            )
        )
        target = round2(entry_low + atr * 3)

    else:
        support = item.get("ma20", price)
        entry_low = round2(support * 1.003)
        entry_high = round2(min(support + atr * 0.6, support * 1.025))
        no_entry = round2(support * 0.995)
        invalid = round2(support * 0.985)
        target = round2(entry_low + atr * 3)

    if entry_high < entry_low:
        entry_high = round2(entry_low + atr * 0.3)

    practical_stop = round2(max(support * 0.985, entry_low - atr * 1.5))

    risk = max(entry_low - practical_stop, 0.01)
    reward = max(target - entry_low, 0.01)

    risk_reward = round2(reward / risk)

    if risk_reward >= GOOD_RISK_REWARD:
        rr_note = "風報比良好"
    elif risk_reward >= MIN_RISK_REWARD_ENTRY:
        rr_note = "風報比尚可，建議降低部位"
    else:
        rr_note = "風報比不足，等待更低進場價"

    status = item.get("entry_status", "")

    if status == "可觀察進場" and risk_reward >= MIN_RISK_REWARD_ENTRY:
        ai_next_action = "明日開盤若站在進場區間且未跌破支撐，可第一筆試單"
    elif status == "可觀察進場" and risk_reward < MIN_RISK_REWARD_ENTRY:
        ai_next_action = "風報比不足，不列入進場，等待更好買點"
    elif status in ["等回採", "等突破", "等轉強K"]:
        ai_next_action = "持續觀察，等待回採、突破或轉強K"
    else:
        ai_next_action = "觀察"

    return {
        "support_price": round2(support),
        "next_entry_low": entry_low,
        "next_entry_high": entry_high,
        "no_entry_price": no_entry,
        "invalid_price": invalid,
        "practical_stop": practical_stop,
        "initial_stop": practical_stop,
        "target_price": target,
        "risk_reward": risk_reward,
        "risk_reward_note": rr_note,
        "risk_reward_group": get_risk_reward_group(risk_reward),
        "ai_next_action": ai_next_action,
        "trade_plan_note": "依大盤位階、族群、龍頭、壓力支撐、K棒、風報比綜合判斷"
    }


def calc_position_sizing(item, market_info):
    entry = item.get("next_entry_low") or item.get("price")
    stop = item.get("practical_stop") or item.get("initial_stop")

    risk_multiplier = market_info.get("risk_multiplier", 0)
    adjusted_risk_amount = ACCOUNT_SIZE * RISK_PER_TRADE * risk_multiplier

    if item.get("risk_reward", 0) < MIN_RISK_REWARD_ENTRY:
        adjusted_risk_amount = 0

    if not entry or not stop or entry <= stop or adjusted_risk_amount <= 0:
        return {
            "suggest_shares": 0,
            "suggest_lots": 0,
            "position_value": 0,
            "risk_per_share": 0
        }

    risk_per_share = entry - stop
    shares = math.floor(adjusted_risk_amount / risk_per_share)
    lots = math.floor(shares / 1000)

    return {
        "suggest_shares": shares,
        "suggest_lots": lots,
        "position_value": round2(shares * entry),
        "risk_per_share": round2(risk_per_share)
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

    if score >= 230 and item.get("main_score", 0) >= 50 and item.get("combined_sector_score", 0) >= 25:
        return "S"

    if score >= 175 and item.get("main_score", 0) >= 25 and item.get("combined_sector_score", 0) >= 10:
        return "A"

    return None


# =====================================================
# 交易統計 + AI反饋權重
# =====================================================
def trade_return_value(log):
    total = safe_float(log.get("total_pnl"), None)

    if total is not None and total != 0:
        return total

    pnl_pct = safe_float(log.get("pnl_pct"), 0)
    entry = safe_float(log.get("entry_price"), 0)
    shares = safe_int(log.get("shares"), 0)

    if entry and shares:
        return round2(entry * shares * pnl_pct / 100)

    return pnl_pct


def calc_group_stats(logs, key_name):
    group_map = {}

    for log in logs:
        key = log.get(key_name) or "未分類"
        group_map.setdefault(key, []).append(log)

    rows = []

    for key, arr in group_map.items():
        returns = [trade_return_value(x) for x in arr]
        pct_returns = [safe_float(x.get("pnl_pct"), 0) for x in arr]

        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]

        count = len(arr)
        winrate = round2(len(wins) / count * 100) if count else 0
        avg_return = round2(sum(pct_returns) / count) if count else 0
        total_pnl = round2(sum(returns))
        avg_win = round2(sum(wins) / len(wins)) if wins else 0
        avg_loss = round2(sum(losses) / len(losses)) if losses else 0

        payoff_ratio = round2(abs(avg_win / avg_loss)) if avg_loss != 0 else 0
        expectancy = round2(total_pnl / count) if count else 0

        if count < MIN_FEEDBACK_SAMPLE:
            ai_comment = "樣本不足，先觀察"
            ai_level = "neutral"
        elif winrate >= 60 and total_pnl > 0 and avg_return > 0:
            ai_comment = "表現良好，可保留或提高權重"
            ai_level = "good"
        elif winrate < 40 and total_pnl < 0:
            ai_comment = "表現偏弱，建議降權或提高門檻"
            ai_level = "bad"
        elif total_pnl > 0:
            ai_comment = "有獲利能力，但仍需觀察穩定性"
            ai_level = "normal"
        else:
            ai_comment = "效果普通，建議保守使用"
            ai_level = "warning"

        rows.append({
            "name": key,
            "count": count,
            "winrate": winrate,
            "avg_return": avg_return,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff_ratio": payoff_ratio,
            "expectancy": expectancy,
            "ai_comment": ai_comment,
            "ai_level": ai_level
        })

    return sorted(rows, key=lambda x: (x["total_pnl"], x["winrate"], x["count"]), reverse=True)


def calc_strategy_dashboard(logs):
    if not logs:
        return {
            "total_count": 0,
            "winrate": 0,
            "total_pnl": 0,
            "avg_return": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "payoff_ratio": 0,
            "best_trade": "-",
            "worst_trade": "-",
            "avg_hold_days": 0,
            "by_buy_type": [],
            "by_sector": [],
            "by_level": [],
            "by_risk_reward": [],
            "by_market": [],
            "by_leader": [],
            "ai_summary": "目前尚無結案交易，先累積樣本。"
        }

    returns = [trade_return_value(x) for x in logs]
    pct_returns = [safe_float(x.get("pnl_pct"), 0) for x in logs]

    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    count = len(logs)
    winrate = round2(len(wins) / count * 100)
    total_pnl = round2(sum(returns))
    avg_return = round2(sum(pct_returns) / count)

    avg_win = round2(sum(wins) / len(wins)) if wins else 0
    avg_loss = round2(sum(losses) / len(losses)) if losses else 0

    payoff_ratio = round2(abs(avg_win / avg_loss)) if avg_loss != 0 else 0

    best = max(logs, key=lambda x: trade_return_value(x))
    worst = min(logs, key=lambda x: trade_return_value(x))

    hold_days = [
        days_between(x.get("entry_date", ""), x.get("exit_date", ""))
        for x in logs
    ]

    avg_hold_days = round2(sum(hold_days) / len(hold_days)) if hold_days else 0

    by_buy_type = calc_group_stats(logs, "buy_type")
    by_sector = calc_group_stats(logs, "sector")
    by_level = calc_group_stats(logs, "level")
    by_risk_reward = calc_group_stats(logs, "risk_reward_group")
    by_market = calc_group_stats(logs, "market_status")
    by_leader = calc_group_stats(logs, "leader_status")

    ai_notes = []

    if winrate >= 55 and total_pnl > 0:
        ai_notes.append("整體策略目前為正向，可持續累積樣本。")
    elif total_pnl < 0:
        ai_notes.append("整體績效目前偏弱，建議降低部位並檢查失敗類型。")
    else:
        ai_notes.append("整體樣本仍需累積，先不要過度調整策略。")

    if by_buy_type:
        best_type = by_buy_type[0]
        ai_notes.append(
            f"目前表現較佳的買點為「{best_type['name']}」，"
            f"勝率 {best_type['winrate']}%，總損益 {best_type['total_pnl']}。"
        )

    return {
        "total_count": count,
        "winrate": winrate,
        "total_pnl": total_pnl,
        "avg_return": avg_return,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "best_trade": f"{best.get('name', '-')} / {trade_return_value(best)}",
        "worst_trade": f"{worst.get('name', '-')} / {trade_return_value(worst)}",
        "avg_hold_days": avg_hold_days,
        "by_buy_type": by_buy_type,
        "by_sector": by_sector,
        "by_level": by_level,
        "by_risk_reward": by_risk_reward,
        "by_market": by_market,
        "by_leader": by_leader,
        "ai_summary": " ".join(ai_notes)
    }


def calc_feedback_score(row):
    count = safe_int(row.get("count", 0))
    winrate = safe_float(row.get("winrate", 0))
    total_pnl = safe_float(row.get("total_pnl", 0))
    avg_return = safe_float(row.get("avg_return", 0))
    expectancy = safe_float(row.get("expectancy", 0))

    if count < MIN_FEEDBACK_SAMPLE:
        return 0, "樣本不足，不調整"

    score = 0

    if winrate >= 65 and total_pnl > 0:
        score += 18
    elif winrate >= 55 and total_pnl > 0:
        score += 10
    elif winrate < 40 and total_pnl < 0:
        score -= 18
    elif winrate < 45 and avg_return < 0:
        score -= 10

    if avg_return >= 5:
        score += 8
    elif avg_return <= -3:
        score -= 8

    if expectancy > 0:
        score += min(expectancy / 1000, 8)
    elif expectancy < 0:
        score += max(expectancy / 1000, -8)

    score = round2(max(min(score, MAX_FEEDBACK_BONUS), MAX_FEEDBACK_PENALTY))

    if score > 0:
        note = f"歷史表現佳，加分 {score}"
    elif score < 0:
        note = f"歷史表現弱，扣分 {score}"
    else:
        note = "表現普通，不調整"

    return score, note


def calc_strategy_feedback(logs):
    dashboard = calc_strategy_dashboard(logs)

    groups = {
        "buy_type": dashboard.get("by_buy_type", []),
        "sector": dashboard.get("by_sector", []),
        "level": dashboard.get("by_level", []),
        "risk_reward_group": dashboard.get("by_risk_reward", []),
        "market_status": dashboard.get("by_market", []),
        "leader_status": dashboard.get("by_leader", []),
    }

    feedback = {
        "enabled": len(logs) >= MIN_FEEDBACK_SAMPLE,
        "total_logs": len(logs),
        "min_sample": MIN_FEEDBACK_SAMPLE,
        "weights": {},
        "rows": [],
        "summary": ""
    }

    if len(logs) < MIN_FEEDBACK_SAMPLE:
        feedback["summary"] = (
            f"目前結案交易 {len(logs)} 筆，未達 {MIN_FEEDBACK_SAMPLE} 筆，"
            "AI反饋權重暫不啟用。"
        )
        return feedback

    for group_name, rows in groups.items():
        feedback["weights"][group_name] = {}

        for row in rows:
            name = row.get("name", "未分類")
            score, note = calc_feedback_score(row)

            feedback["weights"][group_name][name] = score

            if score != 0:
                feedback["rows"].append({
                    "group": group_name,
                    "name": name,
                    "score": score,
                    "note": note,
                    "count": row.get("count", 0),
                    "winrate": row.get("winrate", 0),
                    "total_pnl": row.get("total_pnl", 0),
                    "avg_return": row.get("avg_return", 0)
                })

    positive = len([x for x in feedback["rows"] if x["score"] > 0])
    negative = len([x for x in feedback["rows"] if x["score"] < 0])

    feedback["summary"] = (
        f"AI反饋權重已啟用：目前根據 {len(logs)} 筆結案交易，"
        f"產生 {positive} 個加分條件、{negative} 個扣分條件。"
    )

    feedback["rows"] = sorted(feedback["rows"], key=lambda x: abs(x["score"]), reverse=True)

    return feedback


def apply_strategy_feedback(item, feedback):
    if not feedback.get("enabled"):
        item["feedback_score"] = 0
        item["feedback_notes"] = ["樣本不足，尚未套用AI反饋權重"]
        return item

    weights = feedback.get("weights", {})
    total = 0
    notes = []

    mapping = {
        "buy_type": item.get("buy_type", "未分類"),
        "sector": item.get("sector", "未分類"),
        "level": item.get("level", "未分類"),
        "risk_reward_group": item.get("risk_reward_group", "未分類"),
        "market_status": item.get("market_status", "未分類"),
        "leader_status": item.get("leader_status", "未分類"),
    }

    for group_name, key in mapping.items():
        score = safe_float(weights.get(group_name, {}).get(key, 0))

        if score != 0:
            total += score
            notes.append(f"{group_name}:{key} {score:+}")

    total = round2(max(min(total, MAX_FEEDBACK_BONUS), MAX_FEEDBACK_PENALTY))

    item["feedback_score"] = total
    item["feedback_notes"] = notes if notes else ["無明顯反饋調整"]
    item["score"] = round2(item.get("score", 0) + total)

    return item


# =====================================================
# 候選池
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
            "feedback_score": item.get("feedback_score", 0),
            "feedback_notes": item.get("feedback_notes", []),
            "support_price": item.get("support_price", 0),
            "next_entry_low": item.get("next_entry_low", 0),
            "next_entry_high": item.get("next_entry_high", 0),
            "no_entry_price": item.get("no_entry_price", 0),
            "invalid_price": item.get("invalid_price", 0),
            "practical_stop": item.get("practical_stop", 0),
            "risk_reward": item.get("risk_reward", 0),
            "risk_reward_note": item.get("risk_reward_note", "-"),
            "risk_reward_group": item.get("risk_reward_group", "-"),
            "target_price": item.get("target_price", 0),
            "sector_status": item.get("sector_status", "-"),
            "leader_status": item.get("leader_status", "-"),
            "leader_names": item.get("leader_names", "-"),
            "market_status": item.get("market_status", "-"),
            "resistance_low": item.get("resistance_low", 0),
            "resistance_high": item.get("resistance_high", 0),
            "box_low": item.get("box_low", 0),
            "box_high": item.get("box_high", 0),
            "breakout_state": item.get("breakout_state", "-"),
            "egg_zone": item.get("egg_zone", "-"),
            "candle_signal": item.get("candle_signal", "-"),
            "ai_next_action": item.get("ai_next_action", "-"),
            "trade_plan_note": item.get("trade_plan_note", "-"),
            "updated_at": now,
            "first_seen": old.get("first_seen", today),
            "last_seen": today
        }

        new_candidates[symbol] = candidate

        if current_status == "可觀察進場" and item.get("risk_reward", 0) >= MIN_RISK_REWARD_ENTRY:
            entry_alerts.append(dict(candidate))

    sorted_candidates = dict(
        sorted(
            new_candidates.items(),
            key=lambda kv: (
                1 if kv[1].get("current_status") == "可觀察進場" else 0,
                kv[1].get("risk_reward", 0),
                kv[1].get("score", 0)
            ),
            reverse=True
        )
    )

    entry_alerts = sorted(
        entry_alerts,
        key=lambda x: (
            x.get("level") == "S",
            x.get("risk_reward", 0),
            x.get("feedback_score", 0),
            x.get("score", 0)
        ),
        reverse=True
    )[:MAX_ENTRY_ALERTS]

    data = {
        "updated_at": now,
        "candidates": sorted_candidates,
        "entry_alerts": entry_alerts
    }

    save_candidate_pool(data)

    return data


# =====================================================
# 持股管理
# =====================================================
def normalize_track_record(track):
    if "price" not in track:
        track["price"] = safe_float(track.get("entry_price"), 0)

    if "entry_price" not in track:
        track["entry_price"] = safe_float(track.get("price"), 0)

    if "shares" not in track:
        track["shares"] = 0

    if "realized_pnl" not in track:
        track["realized_pnl"] = 0

    if "trade_actions" not in track:
        track["trade_actions"] = []

    if "note" not in track:
        track["note"] = ""

    if "risk_reward_group" not in track:
        track["risk_reward_group"] = get_risk_reward_group(track.get("risk_reward", 0))

    if "feedback_score" not in track:
        track["feedback_score"] = 0

    if "feedback_notes" not in track:
        track["feedback_notes"] = []

    return track


def calc_holding_management(track, df):
    track = normalize_track_record(track)

    if df is None or df.empty:
        return track

    curr = safe_float(df["Close"].iloc[-1])
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    atr = safe_float(calc_atr(df).iloc[-1])
    entry = safe_float(track.get("price"))
    shares = safe_int(track.get("shares"))
    realized_pnl = safe_float(track.get("realized_pnl"))

    support = safe_float(track.get("support_price")) or entry
    invalid_price = safe_float(track.get("invalid_price")) or support * 0.985

    practical_stop = safe_float(track.get("practical_stop"))

    if not practical_stop and atr:
        practical_stop = max(support * 0.985, entry - atr * 1.5)
    elif not practical_stop:
        practical_stop = support * 0.985

    practical_stop = round2(practical_stop)
    invalid_price = round2(invalid_price)

    entry_date = track.get("date", today_str())
    entry_dt = pd.to_datetime(entry_date, errors="coerce")

    if pd.notna(entry_dt):
        after_entry = df[df.index >= entry_dt]
    else:
        after_entry = df.tail(60)

    if after_entry.empty:
        after_entry = df.tail(60)

    highest = max(safe_float(after_entry["High"].max()), entry)

    conservative_trail = round2(highest - atr * 2.0) if atr else 0
    standard_trail = round2(highest - atr * 2.5) if atr else 0
    loose_trail = round2(highest - atr * 3.0) if atr else 0

    if standard_trail > entry and curr >= entry + atr * 1.5:
        trail_zone_name = "移動停利區"
        standard_action = "跌破標準移動停利，建議停利出場。"
    else:
        trail_zone_name = "移動停損區"
        standard_action = "跌破標準移動停損，建議停損出場。"

    conservative_action = "跌破保守風控，建議先減碼或提高警戒。"
    loose_action = "跌破寬鬆風控，趨勢轉弱，建議全出。"

    low_120 = safe_float(low.rolling(120).min().iloc[-1])
    high_120 = safe_float(high.rolling(120).max().iloc[-1])
    egg = analyze_egg_position(curr, low_120, high_120)

    start_low = min(safe_float(after_entry["Low"].min()), support)

    wave_1 = round2(support + (support - start_low))
    wave_2 = round2(support + (support - start_low) * 1.618)
    wave_3 = round2(support + (support - start_low) * 2.0)

    atr_t1 = round2(entry + atr * 3) if atr else 0
    atr_t2 = round2(entry + atr * 5) if atr else 0
    atr_t3 = round2(entry + atr * 8) if atr else 0

    target_1 = max(wave_1, atr_t1)
    target_2 = max(wave_2, atr_t2)
    target_3 = max(wave_3, atr_t3)

    progress_to_t1 = round2(curr / target_1 * 100) if target_1 else 0
    pnl_pct = round2(pct(curr, entry)) if entry else 0

    unrealized_pnl = round2((curr - entry) * shares) if shares else 0
    total_pnl = round2(realized_pnl + unrealized_pnl)

    position_value = round2(curr * shares) if shares else 0
    cost_value = round2(entry * shares) if shares else 0

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
        ai_exit_notice = standard_action

    elif curr <= conservative_trail:
        ai_status = "跌破保守風控"
        ai_exit_notice = conservative_action

    elif support_stand_back:
        ai_status = "假跌破站回"
        ai_exit_notice = "昨日跌破支撐但今日站回，可能是假跌破轉強，可重新觀察試單。"

    elif progress_to_t1 >= 98 and candle.get("candle_score", 0) < 0:
        ai_status = "接近滿足點需停利"
        ai_exit_notice = "接近第一滿足點且K棒轉弱，建議部分停利。"

    elif progress_to_t1 >= 98:
        ai_status = "接近第一滿足點"
        ai_exit_notice = "已接近第一波滿足點，續抱但須提高停利警戒。"

    elif curr >= target_2 * 0.98:
        ai_status = "接近第二滿足點"
        ai_exit_notice = "接近第二滿足點，建議逐步鎖利。"

    else:
        ai_status = "續抱"
        ai_exit_notice = "尚未跌破AI移動風控區、支撐點或實戰停損價，依策略續抱。"

    scale_out_note = "尚未觸發分批出場"

    if curr <= conservative_trail:
        scale_out_note = "建議先減碼 1/3"

    if curr <= standard_trail:
        scale_out_note = "建議再減碼 1/3 或停損"

    if curr <= practical_stop:
        scale_out_note = "建議全出"

    track.update({
        "curr": round2(curr),
        "pnl": pnl_pct,
        "position_value": position_value,
        "cost_value": cost_value,
        "realized_pnl": round2(realized_pnl),
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "highest_since_entry": round2(highest),
        "atr": round2(atr),
        "support_price": round2(support),
        "practical_stop": practical_stop,
        "initial_stop": practical_stop,
        "invalid_price": invalid_price,
        "conservative_trail": conservative_trail,
        "standard_trail": standard_trail,
        "loose_trail": loose_trail,
        "trail_range": f"{loose_trail} ～ {conservative_trail}",
        "trail_zone_name": trail_zone_name,
        "conservative_action": conservative_action,
        "standard_action": standard_action,
        "loose_action": loose_action,
        "scale_out_note": scale_out_note,
        "egg_zone_now": egg["egg_zone"],
        "egg_position_pct_now": egg["egg_position_pct"],
        "wave_start_price": round2(start_low),
        "wave_target_1": round2(target_1),
        "wave_target_2": round2(target_2),
        "wave_target_3": round2(target_3),
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
    data = read_json_file(TRACK_FILE, [])
    return [normalize_track_record(x) for x in data]


def save_track(data):
    write_json_file(TRACK_FILE, data)


def load_trade_log():
    logs = read_json_file(TRADE_LOG_FILE, [])

    for log in logs:
        if "risk_reward_group" not in log:
            log["risk_reward_group"] = get_risk_reward_group(log.get("risk_reward", 0))

        if "market_status" not in log:
            log["market_status"] = "未記錄"

        if "leader_status" not in log:
            log["leader_status"] = "未記錄"

        if "feedback_score" not in log:
            log["feedback_score"] = 0

    return logs


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
        "market_egg_zone": "-",
        "market_pressure_note": "-",
        "stock_pool_count": 0,
        "entry_alerts": [],
        "candidate_pool": [],
        "sector_rankings": [],
        "candidate_count": 0,
        "strategy_feedback": {
            "enabled": False,
            "summary": "尚未建立AI反饋權重",
            "rows": []
        }
    })


def calc_track_stats(tracks):
    valid = [x for x in tracks if isinstance(x.get("pnl"), (int, float))]

    if not valid:
        return 0, 0

    wins = [x for x in valid if x["pnl"] > 0]
    avg = sum(x["pnl"] for x in valid) / len(valid)

    return round2(len(wins) / len(valid) * 100), round2(avg)


# =====================================================
# 全市場掃描
# =====================================================
def scan_market():
    save_scan_status("running", "正在建立全市場股票池。")
    print("開始掃描：", taiwan_now())

    stocks = get_stock_pool()
    trade_logs = load_trade_log()
    strategy_feedback = calc_strategy_feedback(trade_logs)

    market_info = get_market_status()
    market_score = market_info["market_score"]

    analyzed = []
    analyzed_map = {}
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
                "df": df
            }

            item.update(result)

            analyzed.append(item)
            analyzed_map[symbol] = item

            if i % 100 == 0:
                save_scan_status("running", f"正在掃描全市場：{i}/{total}")

            time.sleep(0.02)

        except Exception as e:
            print("單檔掃描失敗：", symbol, e)
            continue

    sector_scores = calc_sector_scores(analyzed)

    leader_scores = {}

    for sector in sector_scores.keys():
        leader_scores[sector] = calc_leader_strength(sector, analyzed_map)

    sector_rankings = build_sector_rankings(sector_scores, leader_scores)
    sector_rank_map = {x["sector"]: x["rank"] for x in sector_rankings}
    combined_sector_score_map = {x["sector"]: x["combined_sector_score"] for x in sector_rankings}

    final_items = []

    for item in analyzed:
        sector_data = sector_scores.get(item["sector"], {
            "sector_score": 0,
            "sector_status": "弱勢族群",
            "sector_avg_5d": 0,
            "sector_avg_20d": 0,
            "sector_avg_main": 0,
            "sector_strong_ratio": 0,
            "sector_stock_count": 0
        })

        leader_data = leader_scores.get(item["sector"], {
            "leader_score": 0,
            "leader_status": "無明確龍頭資料",
            "leader_names": "-"
        })

        item.update(sector_data)
        item.update(leader_data)

        item["sector_rank"] = sector_rank_map.get(item["sector"], 999)
        item["combined_sector_score"] = combined_sector_score_map.get(
            item["sector"],
            item.get("sector_score", 0)
        )

        item["market_status"] = market_info.get("market_status", "-")

        top_down_bonus = (
            item.get("combined_sector_score", 0) +
            item.get("leader_score", 0) * 0.3 +
            market_score
        )

        item["score"] = round2(item.get("technical_score", 0) + top_down_bonus)

        buy_type, entry_status, entry_reason = determine_entry_status(item)

        item["buy_type"] = buy_type
        item["entry_status"] = entry_status
        item["entry_reason"] = entry_reason

        item.update(build_trade_plan(item))
        item.update(calc_position_sizing(item, market_info))

        preliminary_level = "A"

        if item["score"] >= 230 and item.get("main_score", 0) >= 50 and item.get("combined_sector_score", 0) >= 25:
            preliminary_level = "S"

        item["level"] = preliminary_level

        item = apply_strategy_feedback(item, strategy_feedback)

        level = classify_stock(item)

        if not level:
            continue

        item["level"] = level

        item = apply_strategy_feedback(item, strategy_feedback)

        if not market_info.get("allow_new_positions"):
            item["entry_status"] = "禁止新倉"
            item["entry_reason"] = market_info.get("risk_note", "市場風險偏高。")
            item["ai_next_action"] = "大盤風險偏高，禁止新倉"

        item.pop("df", None)
        final_items.append(item)

    final_items = sorted(
        final_items,
        key=lambda x: (
            x.get("level") == "S",
            x.get("risk_reward", 0),
            x.get("feedback_score", 0),
            x.get("score", 0)
        ),
        reverse=True
    )

    candidate_data = update_candidate_pool(final_items)

    candidate_pool_list = list(candidate_data.get("candidates", {}).values())[:MAX_CANDIDATE_DISPLAY]
    entry_alerts = candidate_data.get("entry_alerts", [])

    s_count = len([x for x in final_items if x.get("level") == "S"])
    a_count = len([x for x in final_items if x.get("level") == "A"])

    scan_data = {
        "updated_at": taiwan_now(),
        "market_status": market_info["market_status"],
        "market_score": market_info["market_score"],
        "risk_mode": market_info["risk_mode"],
        "risk_switch": market_info["risk_switch"],
        "allow_new_positions": market_info["allow_new_positions"],
        "risk_note": market_info["risk_note"],
        "risk_multiplier": market_info["risk_multiplier"],
        "market_egg_zone": market_info.get("market_egg_zone", "-"),
        "market_pressure_note": market_info.get("market_pressure_note", "-"),
        "stock_pool_count": total,
        "s_count": s_count,
        "a_count": a_count,
        "sector_rankings": sector_rankings,
        "candidate_count": len(candidate_pool_list),
        "candidate_pool": candidate_pool_list,
        "entry_alerts": entry_alerts,
        "strategy_feedback": strategy_feedback
    }

    save_scan_results(scan_data)

    save_scan_status(
        "done",
        f"掃描完成：股票池 {total} 檔，S級 {s_count} 檔，A級候選 {a_count} 檔，進場提醒 {len(entry_alerts)} 檔。"
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
    strategy_dashboard = calc_strategy_dashboard(trade_logs)

    strategy_feedback = scan_data.get("strategy_feedback")

    if not strategy_feedback:
        strategy_feedback = calc_strategy_feedback(trade_logs)

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
        market_egg_zone=scan_data.get("market_egg_zone", "-"),
        market_pressure_note=scan_data.get("market_pressure_note", "-"),
        scan_updated_at=scan_data.get("updated_at", "尚未掃描"),
        stock_pool_count=scan_data.get("stock_pool_count", 0),

        s_count=scan_data.get("s_count", 0),
        a_count=scan_data.get("a_count", 0),
        candidate_count=scan_data.get("candidate_count", 0),

        sector_rankings=scan_data.get("sector_rankings", []),
        candidate_pool=scan_data.get("candidate_pool", []),
        entry_alerts=scan_data.get("entry_alerts", []),

        scan_status=scan_status_data.get("status", "idle"),
        scan_message=scan_status_data.get("message", "尚未掃描"),
        scan_status_time=scan_status_data.get("updated_at", "-"),

        tracks=updated_tracks,
        trade_logs=trade_logs[-10:],
        strategy_dashboard=strategy_dashboard,
        strategy_feedback=strategy_feedback,
        winrate=winrate,
        avg=avg,
        account_size=ACCOUNT_SIZE,
        risk_per_trade=round2(RISK_PER_TRADE * 100)
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
    candidate_data = load_candidate_pool()
    candidate = candidate_data.get("candidates", {}).get(symbol)

    if candidate:
        return candidate

    return None


@app.route("/track/<symbol>", methods=["GET", "POST"])
def track(symbol):
    item = find_item_by_symbol(symbol)

    if not item:
        return redirect(url_for("index"))

    data = load_track()

    if any(x["symbol"] == symbol for x in data):
        return redirect(url_for("index"))

    actual_price = request.form.get("actual_price") if request.method == "POST" else None
    shares = request.form.get("shares") if request.method == "POST" else None
    note = request.form.get("note") if request.method == "POST" else ""

    entry_price = (
        safe_float(actual_price, 0) or
        safe_float(item.get("next_entry_low")) or
        safe_float(item.get("price"))
    )

    actual_shares = safe_int(shares, 0)

    data.append({
        "symbol": symbol,
        "name": item.get("name", symbol),
        "level": item.get("level", "-"),
        "sector": item.get("sector", "-"),
        "buy_type": item.get("buy_type", "-"),
        "price": entry_price,
        "entry_price": entry_price,
        "shares": actual_shares,
        "realized_pnl": 0,
        "trade_actions": [
            {
                "type": "初始追蹤",
                "price": entry_price,
                "shares": actual_shares,
                "note": note or "加入追蹤",
                "date": taiwan_now()
            }
        ],
        "note": note or "",
        "support_price": safe_float(item.get("support_price")),
        "no_entry_price": safe_float(item.get("no_entry_price")),
        "invalid_price": safe_float(item.get("invalid_price")),
        "practical_stop": safe_float(item.get("practical_stop")),
        "initial_stop": safe_float(item.get("practical_stop")) or safe_float(item.get("initial_stop")),
        "risk_reward": safe_float(item.get("risk_reward")),
        "risk_reward_group": item.get("risk_reward_group", get_risk_reward_group(item.get("risk_reward", 0))),
        "sector_status": item.get("sector_status", "-"),
        "leader_status": item.get("leader_status", "-"),
        "market_status": item.get("market_status", "-"),
        "feedback_score": item.get("feedback_score", 0),
        "feedback_notes": item.get("feedback_notes", []),
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


@app.route("/update-track/<symbol>", methods=["POST"])
def update_track(symbol):
    data = load_track()

    for t in data:
        if t["symbol"] == symbol:
            old_price = safe_float(t.get("price"))
            old_shares = safe_int(t.get("shares"))

            new_price = safe_float(request.form.get("price"), old_price)
            new_shares = safe_int(request.form.get("shares"), old_shares)
            new_stop = safe_float(request.form.get("practical_stop"), safe_float(t.get("practical_stop")))
            new_note = request.form.get("note", t.get("note", ""))

            t["price"] = new_price
            t["entry_price"] = new_price
            t["shares"] = new_shares
            t["practical_stop"] = new_stop
            t["initial_stop"] = new_stop
            t["note"] = new_note

            t.setdefault("trade_actions", []).append({
                "type": "修改資料",
                "price": new_price,
                "shares": new_shares,
                "note": new_note or "手動修改成本、股數或停損",
                "date": taiwan_now()
            })

            break

    save_track(data)

    return redirect(url_for("index"))


@app.route("/add-position/<symbol>", methods=["POST"])
def add_position(symbol):
    data = load_track()

    add_price = safe_float(request.form.get("add_price"))
    add_shares = safe_int(request.form.get("add_shares"))
    add_note = request.form.get("add_note", "")

    if add_price <= 0 or add_shares <= 0:
        return redirect(url_for("index"))

    for t in data:
        if t["symbol"] == symbol:
            old_price = safe_float(t.get("price"))
            old_shares = safe_int(t.get("shares"))

            total_cost = old_price * old_shares + add_price * add_shares
            total_shares = old_shares + add_shares

            new_avg_price = round2(total_cost / total_shares) if total_shares > 0 else old_price

            t["price"] = new_avg_price
            t["entry_price"] = new_avg_price
            t["shares"] = total_shares

            t.setdefault("trade_actions", []).append({
                "type": "加碼",
                "price": add_price,
                "shares": add_shares,
                "note": add_note or "手動加碼",
                "date": taiwan_now()
            })

            break

    save_track(data)

    return redirect(url_for("index"))


@app.route("/reduce-position/<symbol>", methods=["POST"])
def reduce_position(symbol):
    data = load_track()

    reduce_price = safe_float(request.form.get("reduce_price"))
    reduce_shares = safe_int(request.form.get("reduce_shares"))
    reduce_note = request.form.get("reduce_note", "")

    if reduce_price <= 0 or reduce_shares <= 0:
        return redirect(url_for("index"))

    for t in data:
        if t["symbol"] == symbol:
            old_price = safe_float(t.get("price"))
            old_shares = safe_int(t.get("shares"))
            sell_shares = min(reduce_shares, old_shares)

            realized = round2((reduce_price - old_price) * sell_shares)

            t["shares"] = max(old_shares - sell_shares, 0)
            t["realized_pnl"] = round2(safe_float(t.get("realized_pnl")) + realized)

            t.setdefault("trade_actions", []).append({
                "type": "減碼",
                "price": reduce_price,
                "shares": sell_shares,
                "realized_pnl": realized,
                "note": reduce_note or "手動減碼",
                "date": taiwan_now()
            })

            break

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
        "shares": item.get("shares", 0),
        "pnl_pct": item.get("pnl"),
        "realized_pnl": item.get("realized_pnl", 0),
        "unrealized_pnl": item.get("unrealized_pnl", 0),
        "total_pnl": item.get("total_pnl", 0),
        "entry_date": item.get("date"),
        "exit_date": today_str(),
        "level": item.get("level"),
        "buy_type": item.get("buy_type"),
        "sector": item.get("sector"),
        "risk_reward": item.get("risk_reward", 0),
        "risk_reward_group": item.get("risk_reward_group", get_risk_reward_group(item.get("risk_reward", 0))),
        "market_status": item.get("market_status", "未記錄"),
        "sector_status": item.get("sector_status", "未記錄"),
        "leader_status": item.get("leader_status", "未記錄"),
        "feedback_score": item.get("feedback_score", 0),
        "feedback_notes": item.get("feedback_notes", []),
        "note": item.get("note", ""),
        "trade_actions": item.get("trade_actions", []),
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
