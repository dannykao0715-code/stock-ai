import os
import json
import math
import time
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

APP_VERSION_NAME = "AI交易助理正式驗證自我優化版_2026-05-22"

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456")
TZ = ZoneInfo("Asia/Taipei")

RESULT_FILE = "scan_results.json"
TRACK_FILE = "track.json"
TRADE_LOG_FILE = "trade_log.json"
STOCK_POOL_FILE = "stock_pool.json"
SCAN_STATUS_FILE = "scan_status.json"
CANDIDATE_FILE = "candidate_pool.json"
LINE_USER_FILE = "line_user.json"
LINE_NOTIFY_LOG_FILE = "line_notify_log.json"
SIGNAL_DATABASE_FILE = "signal_database.json"
STRATEGY_WEIGHTS_FILE = "strategy_weights.json"
OPTIMIZATION_LOG_FILE = "optimization_log.json"

FULL_MARKET_MIN_COUNT = 1700
MAX_ENTRY_ALERTS = 8
MAX_CANDIDATE_DISPLAY = 60
ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "1000000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))
MIN_RISK_REWARD_ENTRY = 1.5
MIN_AVG_VOLUME_20 = 500_000
MIN_AVG_AMOUNT_20 = 5_000_000
MIN_FEEDBACK_SAMPLE = 3

# =====================================================
# AI交易助理正式驗證版設定
# =====================================================
# 目前先跑1～2個月觀察，不給重倉建議，只做正式流程驗證。
VALIDATION_MODE = os.getenv("VALIDATION_MODE", "1") == "1"
MAX_VALIDATION_POSITION_TEXT = "小部位試單"
MIN_AI_CONFIDENCE_TO_NOTIFY = int(os.getenv("MIN_AI_CONFIDENCE_TO_NOTIFY", "75"))

# =====================================================
# AI自我優化模組設定
# =====================================================
# 累積資料不足時只統計，不亂調整。
AUTO_OPTIMIZATION_ENABLED = os.getenv("AUTO_OPTIMIZATION_ENABLED", "1") == "1"
OPTIMIZE_SUGGEST_AFTER = int(os.getenv("OPTIMIZE_SUGGEST_AFTER", "30"))
OPTIMIZE_SMALL_AFTER = int(os.getenv("OPTIMIZE_SMALL_AFTER", "100"))
OPTIMIZE_MEDIUM_AFTER = int(os.getenv("OPTIMIZE_MEDIUM_AFTER", "200"))
OPTIMIZE_MIN_GROUP_COUNT = int(os.getenv("OPTIMIZE_MIN_GROUP_COUNT", "8"))
OPTIMIZE_TARGET_RETURN_KEY = os.getenv("OPTIMIZE_TARGET_RETURN_KEY", "return_5d_pct")
OPTIMIZE_MAX_WEIGHT = float(os.getenv("OPTIMIZE_MAX_WEIGHT", "20"))
OPTIMIZE_MIN_WEIGHT = float(os.getenv("OPTIMIZE_MIN_WEIGHT", "-20"))

# =====================================================
# 省資源掃描模式設定
# =====================================================
# 全市場約 1800 檔會先做「粗篩」，只留下分數較高的股票再做完整細算。
# 這樣可以保留全市場掃描概念，但大幅降低 CPU 與記憶體消耗。
ENABLE_RESOURCE_SAVING_SCAN = os.getenv("ENABLE_RESOURCE_SAVING_SCAN", "1") == "1"
ROUGH_SCAN_TOP_N = int(os.getenv("ROUGH_SCAN_TOP_N", "320"))
ROUGH_SCAN_MIN_SCORE = float(os.getenv("ROUGH_SCAN_MIN_SCORE", "15"))
DETAILED_ANALYSIS_SLEEP = float(os.getenv("DETAILED_ANALYSIS_SLEEP", "0.01"))
ROUGH_ANALYSIS_SLEEP = float(os.getenv("ROUGH_ANALYSIS_SLEEP", "0.005"))
DATA_CACHE_DIR = os.getenv("DATA_CACHE_DIR", "data_cache")

is_scanning = False


def check_auth(username, password):
    return username == ADMIN_USER and password == ADMIN_PASSWORD


def require_auth():
    return Response("需要登入才能使用此網站", 401, {"WWW-Authenticate": 'Basic realm="Stock AI Login"'})


@app.before_request
def protect_site():
    # LINE Webhook 不能被 Basic Auth 擋住，否則 LINE 無法把 User ID 傳進來
    if request.path == "/callback":
        return None

    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return require_auth()


def taiwan_now():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
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
    a = safe_float(a)
    b = safe_float(b)
    if b == 0:
        return 0
    return (a - b) / b * 100


def save_scan_status(status, message):
    write_json(SCAN_STATUS_FILE, {"status": status, "message": message, "updated_at": taiwan_now()})


def load_scan_status():
    return read_json(SCAN_STATUS_FILE, {"status": "idle", "message": "尚未掃描", "updated_at": "-"})


def risk_reward_group(rr):
    rr = safe_float(rr)
    if rr >= 2.5:
        return "風報比 2.5以上"
    if rr >= 2.0:
        return "風報比 2.0~2.5"
    if rr >= 1.5:
        return "風報比 1.5~2.0"
    return "風報比 低於1.5"


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


def fallback_stock_pool():
    rows = {
        "2330.TW": ("台積電", "半導體"), "2303.TW": ("聯電", "半導體"),
        "2454.TW": ("聯發科", "IC設計"), "3034.TW": ("聯詠", "IC設計"),
        "2317.TW": ("鴻海", "AI伺服器"), "2382.TW": ("廣達", "AI伺服器"),
        "3231.TW": ("緯創", "AI伺服器"), "6669.TW": ("緯穎", "AI伺服器"),
        "3017.TW": ("奇鋐", "散熱"), "3324.TWO": ("雙鴻", "散熱"),
        "2383.TW": ("台光電", "PCB"), "3037.TW": ("欣興", "PCB"),
        "2881.TW": ("富邦金", "金融"), "2882.TW": ("國泰金", "金融"),
        "2603.TW": ("長榮", "航運"), "2609.TW": ("陽明", "航運"),
        "1513.TW": ("中興電", "重電"), "1519.TW": ("華城", "重電"),
        "6446.TW": ("藥華藥", "生技"), "4743.TWO": ("合一", "生技"),
    }
    return {k: {"name": v[0], "industry": v[1]} for k, v in rows.items()}


def normalize_stock(code, name, industry, suffix):
    code = str(code).strip()
    name = str(name).strip()
    industry = str(industry).strip() if industry else "其他"
    if len(code) == 4 and code.isdigit() and name:
        return f"{code}{suffix}", {"name": name, "industry": industry}
    return None, None


def fetch_json_url(url):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    r.raise_for_status()
    return r.json()


def fetch_twse_pool():
    market = {}
    try:
        data = fetch_json_url("https://openapi.twse.com.tw/v1/opendata/t187ap03_L")
        for item in data:
            symbol, info = normalize_stock(item.get("公司代號", ""), item.get("公司簡稱", "") or item.get("公司名稱", ""), item.get("產業別", "上市"), ".TW")
            if symbol:
                market[symbol] = info
    except Exception as e:
        print("TWSE pool failed", e)
    return market


def parse_tpex_item(item):
    code_keys = ["公司代號", "股票代號", "有價證券代號", "證券代號", "Code", "stock_id", "stk_code"]
    name_keys = ["公司簡稱", "公司名稱", "股票名稱", "有價證券名稱", "證券簡稱", "Name", "stock_name", "stk_name"]
    industry_keys = ["產業別", "產業類別", "Industry", "industry"]
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
    return normalize_stock(code, name, industry, ".TWO")


def fetch_tpex_pool():
    market = {}
    urls = [
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_company",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_security_info",
    ]
    for url in urls:
        try:
            data = fetch_json_url(url)
            rows = data.get("data", []) if isinstance(data, dict) else data if isinstance(data, list) else []
            temp = {}
            for item in rows:
                if isinstance(item, dict):
                    symbol, info = parse_tpex_item(item)
                    if symbol:
                        temp[symbol] = info
            if len(temp) > len(market):
                market = temp
        except Exception as e:
            print("TPEX pool failed", url, e)
    return market


def fetch_isin_pool(mode, suffix, industry_label):
    market = {}
    try:
        url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        text = r.content.decode("big5", errors="ignore")
        df = pd.read_html(StringIO(text))[0]
        df = df[df[0].astype(str).str.contains(r"^\d{4}", na=False)]
        for val in df[0]:
            parts = str(val).split()
            if len(parts) >= 2:
                symbol, info = normalize_stock(parts[0], parts[1], industry_label, suffix)
                if symbol:
                    market[symbol] = info
    except Exception as e:
        print("ISIN pool failed", e)
    return market


def get_stock_pool():
    cache_data = read_json(STOCK_POOL_FILE, {})
    cache = cache_data.get("stocks", {}) if isinstance(cache_data, dict) else {}
    log = []
    if cache:
        log.append(f"快取:{len(cache)}")
    market = {}
    twse = fetch_twse_pool()
    market.update(twse)
    log.append(f"上市:{len(twse)}")
    tpex = fetch_tpex_pool()
    market.update(tpex)
    log.append(f"上櫃:{len(tpex)}")
    if len(market) < FULL_MARKET_MIN_COUNT:
        isin = {}
        isin.update(fetch_isin_pool(2, ".TW", "上市"))
        isin.update(fetch_isin_pool(4, ".TWO", "上櫃"))
        log.append(f"ISIN:{len(isin)}")
        if len(isin) > len(market):
            market = isin
    if len(market) >= FULL_MARKET_MIN_COUNT:
        write_json(STOCK_POOL_FILE, {"updated_at": taiwan_now(), "count": len(market), "source_note": "；".join(log), "stocks": market})
        return market
    if cache and len(cache) > len(market):
        save_scan_status("running", "股票池來源不足，改用快取。" + "；".join(log))
        return cache
    if market:
        write_json(STOCK_POOL_FILE, {"updated_at": taiwan_now(), "count": len(market), "source_note": "；".join(log), "stocks": market})
        return market
    save_scan_status("running", "股票池來源失敗，使用備援龍頭池。")
    return fallback_stock_pool()


def download_stock(symbol, period="1y"):
    """
    省資源資料下載：
    1. 同一檔股票、同一天、同一 period 只下載一次。
    2. 09:10 / 13:20 LINE 通知只會讀候選池與持股，避免重複抓全市場。
    3. Railway 重新部署後快取可能消失，這是正常現象，不影響功能。
    """
    try:
        if not symbol:
            return None

        os.makedirs(DATA_CACHE_DIR, exist_ok=True)
        safe_symbol = str(symbol).replace("/", "_").replace("\\", "_").replace(":", "_")
        cache_path = os.path.join(DATA_CACHE_DIR, f"{safe_symbol}_{period}_{today_str()}.csv")

        if os.path.exists(cache_path):
            try:
                cached = pd.read_csv(cache_path, index_col=0, parse_dates=True)
                if cached is not None and not cached.empty:
                    return cached.dropna()
            except Exception:
                pass

        df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=False, threads=False)

        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.dropna()

        try:
            df.to_csv(cache_path)
        except Exception:
            pass

        return df

    except Exception as e:
        print("download failed", symbol, e)
        return None

def infer_sector(symbol, name, industry):
    if industry and industry not in ["上市", "上櫃", "其他"]:
        return industry
    groups = {
        "AI伺服器": ["廣達", "緯創", "緯穎", "鴻海", "英業達", "仁寶"],
        "散熱": ["奇鋐", "雙鴻", "健策", "高力"],
        "PCB": ["台光電", "欣興", "南電", "景碩"],
        "半導體": ["台積電", "聯電", "世界", "力積電"],
        "IC設計": ["聯發科", "聯詠", "瑞昱", "創意", "世芯", "信驊"],
        "金融": ["金", "中租"],
        "航運": ["長榮", "陽明", "萬海"],
        "航空": ["華航", "長榮航"],
        "重電": ["華城", "中興電", "東元", "大亞", "合機"],
        "生技": ["藥", "生", "醫", "保瑞", "合一", "東洋"],
    }
    for sector, keys in groups.items():
        if any(k in str(name) for k in keys):
            return sector
    return "其他"


def calc_atr(df, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h-l, abs(h-pc), abs(l-pc)], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def egg_position(price, low_value, high_value):
    if not low_value or not high_value or high_value <= low_value:
        return {"egg_zone": "無法判斷", "egg_score": 0, "egg_position_pct": 0}
    pos = (price-low_value)/(high_value-low_value)*100
    if pos <= 35:
        return {"egg_zone": "蛋黃區", "egg_score": 25, "egg_position_pct": round2(pos)}
    if pos <= 70:
        return {"egg_zone": "蛋白區", "egg_score": 15, "egg_position_pct": round2(pos)}
    if pos <= 90:
        return {"egg_zone": "蛋殼區", "egg_score": -5, "egg_position_pct": round2(pos)}
    return {"egg_zone": "蛋殼過熱區", "egg_score": -25, "egg_position_pct": round2(pos)}


def candle_pattern(df):
    if df is None or len(df) < 5:
        return {"candle_signal": "資料不足", "candle_score": 0}
    o, h, l, c = [safe_float(df[x].iloc[-1]) for x in ["Open", "High", "Low", "Close"]]
    po, pc = safe_float(df["Open"].iloc[-2]), safe_float(df["Close"].iloc[-2])
    rng = max(h-l, 0.0001)
    body = abs(c-o)
    upper = h-max(o, c)
    lower = min(o, c)-l
    if c > o and pc < po and c > po and o < pc:
        return {"candle_signal": "紅K吞噬", "candle_score": 25}
    if c > o and body/rng >= 0.55 and pct(c, o) >= 2:
        return {"candle_signal": "帶量長紅K", "candle_score": 20}
    if c > o and lower/rng >= 0.45:
        return {"candle_signal": "下影支撐K", "candle_score": 18}
    if c < o and pc > po and c < po and o > pc:
        return {"candle_signal": "黑K吞噬", "candle_score": -25}
    if c < o and body/rng >= 0.55:
        return {"candle_signal": "長黑K", "candle_score": -25}
    if upper/rng >= 0.45:
        return {"candle_signal": "長上影K", "candle_score": -15}
    return {"candle_signal": "中性K", "candle_score": 0}


def liquidity(df):
    close, vol = df["Close"], df["Volume"]
    avgv = safe_float(vol.rolling(20).mean().iloc[-1])
    avga = safe_float((close*vol).rolling(20).mean().iloc[-1])
    score, level, warnings = 0, "普通", []
    if avgv >= 500_000: score += 10
    if avgv >= 1_000_000: score += 10; level = "佳"
    if avgv >= 3_000_000: score += 10; level = "優"
    if avga >= 50_000_000: score += 10
    if avgv < MIN_AVG_VOLUME_20: score -= 30; level = "不足"; warnings.append("20日均量不足")
    if avga < MIN_AVG_AMOUNT_20: score -= 20; level = "不足"; warnings.append("成交金額不足")
    return {"avg_volume_20": round2(avgv), "avg_volume_20_lots": round2(avgv/1000), "avg_amount_20": round2(avga), "liquidity_score": score, "liquidity_level": level, "liquidity_warnings": warnings, "is_liquid_enough": avgv >= MIN_AVG_VOLUME_20 and avga >= MIN_AVG_AMOUNT_20}


def main_force(df):
    close, open_, high, low, vol = df["Close"], df["Open"], df["High"], df["Low"], df["Volume"]
    money = close * vol
    ma5, ma20 = money.rolling(5).mean(), money.rolling(20).mean()
    base = safe_float(ma20.iloc[-1])
    money_ratio = safe_float(ma5.iloc[-1]/base) if base else 0
    up = close > open_
    strong_up = (close > open_) & (((close-open_)/open_)*100 > 2)
    near_high = ((high-close)/(high-low+0.0001)) < 0.25
    main_buy_days = int(((up) & (money > ma20*1.3)).tail(10).sum())
    strong_buy_days = int(((strong_up) & near_high & (money > ma20*1.5)).tail(10).sum())
    score = 0
    if money_ratio > 1.1: score += 10
    if money_ratio > 1.2: score += 15
    if money_ratio > 1.6: score += 25
    if main_buy_days >= 2: score += 15
    if main_buy_days >= 3: score += 20
    if strong_buy_days >= 1: score += 15
    if strong_buy_days >= 2: score += 25
    if close.iloc[-1] < close.iloc[-2] and vol.iloc[-1] > vol.rolling(20).mean().iloc[-1]*1.5:
        score -= 20
    return {"main_score": round2(score), "money_ratio": round2(money_ratio), "main_buy_days": main_buy_days, "strong_buy_days": strong_buy_days}


def multi_timeframe(df):
    if df is None or len(df) < 160:
        return {"weekly_trend": "資料不足", "weekly_score": 0, "daily_signal": "資料不足", "daily_score": 0, "mtf_status": "資料不足", "mtf_score": 0}
    close = df["Close"]
    price = safe_float(close.iloc[-1])
    ma20, ma60 = safe_float(close.rolling(20).mean().iloc[-1]), safe_float(close.rolling(60).mean().iloc[-1])
    weekly = df.resample("W").agg({"Open":"first", "High":"max", "Low":"min", "Close":"last", "Volume":"sum"}).dropna()
    if len(weekly) < 30:
        return {"weekly_trend": "週K資料不足", "weekly_score": 0, "daily_signal": "日K資料不足", "daily_score": 0, "mtf_status": "資料不足", "mtf_score": 0}
    wc = weekly["Close"]
    wp, wma10, wma20 = safe_float(wc.iloc[-1]), safe_float(wc.rolling(10).mean().iloc[-1]), safe_float(wc.rolling(20).mean().iloc[-1])
    wh26 = safe_float(weekly["High"].rolling(26).max().iloc[-2])
    ws = 0
    if wp > wma10: ws += 15
    if wp > wma20: ws += 15
    if wma10 > wma20: ws += 15
    if wh26 and wp > wh26: ws += 15
    weekly_trend = "週K多頭強勢" if ws >= 45 else "週K多頭" if ws >= 30 else "週K盤整偏多" if ws >= 15 else "週K偏弱"
    ds = 0
    if price > ma20: ds += 10
    if price > ma60: ds += 10
    if ma20 > ma60: ds += 10
    daily_signal = "日K多頭" if ds >= 25 else "日K轉強" if ds >= 15 else "日K偏弱"
    mtf_score = ws + ds
    if ws >= 30 and ds >= 20:
        mtf_status = "週K多頭 + 日K買點同步"
    elif ws >= 30:
        mtf_status = "週K偏多，等待日K轉強"
    elif ws < 15 and ds >= 20:
        mtf_status = "週K偏弱，日K僅短彈"; mtf_score -= 20
    else:
        mtf_status = "多時間框架未共振"
    return {"weekly_trend": weekly_trend, "weekly_score": round2(ws), "daily_signal": daily_signal, "daily_score": round2(ds), "mtf_status": mtf_status, "mtf_score": round2(mtf_score)}


def resistance_zone(df):
    if df is None or len(df) < 80:
        return {"resistance_low": 0, "resistance_high": 0, "resistance_note": "資料不足"}
    recent = df.iloc[-120:-1] if len(df) >= 121 else df.iloc[:-1]
    idx = recent["High"].idxmax()
    row = df.loc[idx]
    low, high = round2(row["Low"]), round2(row["High"])
    price = round2(df["Close"].iloc[-1])
    if price < low: note = "尚未進入前高壓力區"
    elif price <= high: note = "正在前高壓力區內消化賣壓"
    else: note = "已突破前高壓力區上緣"
    return {"resistance_low": low, "resistance_high": high, "resistance_note": note}


def box_zone(df, lookback=30):
    if df is None or len(df) < lookback + 20:
        return {"box_low": 0, "box_high": 0, "box_mid": 0, "box_range_pct": 0, "box_note": "資料不足", "has_box": False}
    recent = df.tail(lookback)
    high, low = safe_float(recent["High"].max()), safe_float(recent["Low"].min())
    mid = (high+low)/2
    price = safe_float(df["Close"].iloc[-1])
    br = pct(high, low)
    has_box = br <= 18
    if not has_box: note = "近期盤整區不明顯"
    elif price <= low*1.03: note = "靠近盤整下緣，可觀察試單"
    elif price >= high*0.985 and price <= high*1.015: note = "接近盤整上緣，等待突破或回落"
    elif price > high*1.015: note = "已突破盤整上緣，等待回採"
    else: note = "盤整區間內"
    return {"box_low": round2(low), "box_high": round2(high), "box_mid": round2(mid), "box_range_pct": round2(br), "box_note": note, "has_box": has_box}


def breakout_state(df, rz, bz):
    if df is None or len(df) < 80:
        return {"breakout_state": "資料不足", "breakout_score": 0, "breakout_note": "資料不足"}
    price = safe_float(df["Close"].iloc[-1])
    rl, rh = rz.get("resistance_low", 0), rz.get("resistance_high", 0)
    bl, bh = bz.get("box_low", 0), bz.get("box_high", 0)
    recent = df.tail(8)
    broke = bool(rh and price > rh*1.003)
    pullback = broke and bool((recent["Low"] <= rh*1.015).any()) and price >= rh*0.995
    if broke and pullback:
        return {"breakout_state": "前高區突破回採不破", "breakout_score": 35, "breakout_note": "突破前高後回採不破"}
    if broke:
        return {"breakout_state": "突破前高區等回採", "breakout_score": 15, "breakout_note": "突破後不追高，等待回採"}
    if rl and rl <= price <= rh:
        return {"breakout_state": "前高壓力區盤整", "breakout_score": 10, "breakout_note": "前高區消化賣壓"}
    if bz.get("has_box") and bl and bl*0.995 <= price <= bl*1.035:
        return {"breakout_state": "盤整下緣試單", "breakout_score": 18, "breakout_note": "靠近盤整下緣"}
    if bz.get("has_box") and bh and price > bh*1.003:
        return {"breakout_state": "突破盤整等回採", "breakout_score": 15, "breakout_note": "突破盤整上緣，等待回採"}
    if rh and price < rl*0.985 and df.tail(8)["Close"].max() > rl:
        return {"breakout_state": "前高區失守", "breakout_score": -25, "breakout_note": "前高壓力區失守"}
    return {"breakout_state": "尚未到買點", "breakout_score": 0, "breakout_note": "尚未觸發"}


def analyze_index(symbol):
    df = download_stock(symbol, "1y")
    if df is None or len(df) < 120:
        return {"ok": False, "price": "-", "score": 0, "status": "資料不足", "egg_zone": "無法判斷", "pressure_note": "資料不足"}
    c, h, l = df["Close"], df["High"], df["Low"]
    price = safe_float(c.iloc[-1])
    ma20, ma60, ma120 = safe_float(c.rolling(20).mean().iloc[-1]), safe_float(c.rolling(60).mean().iloc[-1]), safe_float(c.rolling(120).mean().iloc[-1])
    egg = egg_position(price, safe_float(l.rolling(120).min().iloc[-1]), safe_float(h.rolling(120).max().iloc[-1]))
    prev_high = safe_float(h.rolling(60).max().iloc[-2])
    score = 0
    if price > ma20: score += 10
    if price > ma60: score += 10
    if ma20 > ma60: score += 10
    if ma20 > ma60 > ma120: score += 15
    score += egg["egg_score"]
    if prev_high and price < prev_high and pct(prev_high, price) <= 3:
        score -= 10; pressure = "接近前高壓力"
    elif prev_high and price > prev_high:
        score += 15; pressure = "突破前高"
    else:
        pressure = "前方壓力尚可"
    status = "強多" if score >= 45 else "多頭" if score >= 25 else "盤整偏多" if score >= 5 else "盤整" if score >= -10 else "轉弱"
    return {"ok": True, "price": round2(price), "score": round2(score), "status": status, "egg_zone": egg["egg_zone"], "egg_position_pct": egg["egg_position_pct"], "pressure_note": pressure}


def market_status():
    twii = analyze_index("^TWII")
    otc = analyze_index("^TWOII")
    if not twii["ok"]:
        return {"market_status": "資料不足", "market_score": 0, "risk_mode": "防守", "risk_switch": "保守觀察", "allow_new_positions": False, "risk_multiplier": 0, "risk_note": "大盤資料不足，暫不建議建立新倉。", "market_egg_zone": "無法判斷", "market_pressure_note": "資料不足"}
    score = twii["score"] + (min(max(otc["score"]*0.35, -10), 15) if otc["ok"] else 0)
    if twii["status"] == "強多" and otc.get("status") in ["強多", "多頭", "盤整偏多"]:
        return {"market_status": "強多市場", "market_score": 25, "risk_mode": "積極", "risk_switch": "允許新倉", "allow_new_positions": True, "risk_multiplier": 1.0, "risk_note": "大盤與櫃買偏多，允許正常部位。", "market_egg_zone": twii["egg_zone"], "market_pressure_note": twii["pressure_note"]}
    if score >= 25:
        return {"market_status": "多頭市場", "market_score": 15, "risk_mode": "正常", "risk_switch": "允許新倉", "allow_new_positions": True, "risk_multiplier": 0.8, "risk_note": "大盤偏多，可進場但部位略保守。", "market_egg_zone": twii["egg_zone"], "market_pressure_note": twii["pressure_note"]}
    if score >= 5:
        return {"market_status": "盤整偏多", "market_score": 5, "risk_mode": "保守", "risk_switch": "只允許高品質", "allow_new_positions": True, "risk_multiplier": 0.5, "risk_note": "大盤盤整偏多，只做高勝率與好風報比標的。", "market_egg_zone": twii["egg_zone"], "market_pressure_note": twii["pressure_note"]}
    if score >= -10:
        return {"market_status": "盤整偏弱", "market_score": -10, "risk_mode": "防守", "risk_switch": "降低部位", "allow_new_positions": True, "risk_multiplier": 0.25, "risk_note": "大盤盤整偏弱，僅允許非常明確的交易計畫。", "market_egg_zone": twii["egg_zone"], "market_pressure_note": twii["pressure_note"]}
    return {"market_status": "轉弱市場", "market_score": -25, "risk_mode": "禁止新倉", "risk_switch": "禁止新倉", "allow_new_positions": False, "risk_multiplier": 0, "risk_note": "大盤轉弱，禁止新倉，只管理持股。", "market_egg_zone": twii["egg_zone"], "market_pressure_note": twii["pressure_note"]}


def get_index_price(symbol):
    df = download_stock(symbol, "5d")
    if df is None or df.empty:
        return "-"
    return round2(df["Close"].iloc[-1])


def analyze_stock(df):
    if df is None or len(df) < 120:
        return None
    c, h, l = df["Close"], df["High"], df["Low"]
    price = safe_float(c.iloc[-1])
    if not price:
        return None
    ma20, ma60, ma120 = safe_float(c.rolling(20).mean().iloc[-1]), safe_float(c.rolling(60).mean().iloc[-1]), safe_float(c.rolling(120).mean().iloc[-1])
    atr = safe_float(calc_atr(df).iloc[-1])
    score, signals, warnings = 0, [], []
    if price > ma20: score += 10; signals.append("站上月線")
    else: score -= 15; warnings.append("跌破月線")
    if price > ma60: score += 10; signals.append("站上季線")
    if ma20 > ma60: score += 10; signals.append("月線大於季線")
    if ma20 > ma60 > ma120: score += 20; signals.append("多頭排列")
    ch5, ch20, ch60 = pct(c.iloc[-1], c.iloc[-5]), pct(c.iloc[-1], c.iloc[-20]), pct(c.iloc[-1], c.iloc[-60])
    if 1 <= ch5 <= 15: score += 10
    if ch20 > 5: score += 10
    if ch60 > 10: score += 10
    if ch5 > 22: score -= 30; warnings.append("5日漲幅過熱")
    if ch20 > 45: score -= 25; warnings.append("20日漲幅過熱")
    if pct(price, ma20) > 12: score -= 20; warnings.append("距離月線過遠")
    if atr and atr/price*100 > 10: score -= 15; warnings.append("波動過大")
    liq = liquidity(df); mf = main_force(df); egg = egg_position(price, safe_float(l.rolling(120).min().iloc[-1]), safe_float(h.rolling(120).max().iloc[-1]))
    candle = candle_pattern(df); rz = resistance_zone(df); bz = box_zone(df); bo = breakout_state(df, rz, bz); mtf = multi_timeframe(df)
    score += liq["liquidity_score"] + mf["main_score"] + egg["egg_score"] + candle["candle_score"] + bo["breakout_score"] + mtf["mtf_score"]*0.35
    warnings.extend(liq["liquidity_warnings"])
    out = {"price": round2(price), "technical_score": round2(score), "change_5d": round2(ch5), "change_20d": round2(ch20), "change_60d": round2(ch60), "ma20": round2(ma20), "ma60": round2(ma60), "ma120": round2(ma120), "ma20_distance": round2(pct(price, ma20)), "atr": round2(atr), "latest_low": round2(l.iloc[-1]), "latest_high": round2(h.iloc[-1]), "signals": signals, "warnings": warnings}
    for d in [liq, mf, egg, candle, rz, bz, bo, mtf]:
        out.update(d)
    return out


def sector_scores(items):
    mp = {}
    for item in items:
        mp.setdefault(item["sector"], []).append(item)
    out = {}
    for sec, arr in mp.items():
        avg5 = sum(x["change_5d"] for x in arr)/len(arr)
        avg20 = sum(x["change_20d"] for x in arr)/len(arr)
        avgmain = sum(x["main_score"] for x in arr)/len(arr)
        strong_ratio = len([x for x in arr if x["technical_score"] >= 80]) / len(arr)
        score = 0
        if avg5 > 2: score += 10
        if avg5 > 5: score += 10
        if avg20 > 5: score += 10
        if avg20 > 12: score += 10
        if avgmain >= 35: score += 10
        if strong_ratio >= 0.25: score += 10
        if strong_ratio >= 0.4: score += 15
        status = "主流多頭" if score >= 60 else "轉強族群" if score >= 35 else "盤整偏多" if score >= 15 else "盤整" if score >= 0 else "弱勢族群"
        out[sec] = {"sector": sec, "sector_score": round2(score), "sector_status": status, "sector_avg_5d": round2(avg5), "sector_avg_20d": round2(avg20), "sector_avg_main": round2(avgmain), "sector_strong_ratio": round2(strong_ratio*100), "sector_stock_count": len(arr)}
    return out


def leader_strength(sector, amap):
    leaders = SECTOR_LEADERS.get(sector, [])
    if not leaders:
        return {"leader_score": 0, "leader_status": "無明確龍頭資料", "leader_names": "-"}
    scores, names = [], []
    for sym in leaders:
        it = amap.get(sym)
        if not it: continue
        names.append(it.get("name", sym))
        s = 0
        if it.get("price", 0) > it.get("ma20", 0): s += 10
        if it.get("price", 0) > it.get("ma60", 0): s += 10
        if it.get("ma20", 0) > it.get("ma60", 0): s += 10
        if it.get("breakout_state") in ["前高區突破回採不破", "突破前高區等回採", "突破盤整等回採"]: s += 20
        if it.get("main_score", 0) >= 40: s += 15
        if it.get("change_20d", 0) > 8: s += 10
        scores.append(s)
    if not scores:
        return {"leader_score": 0, "leader_status": "龍頭資料不足", "leader_names": "-"}
    avg = sum(scores)/len(scores)
    status = "龍頭強勢帶動" if avg >= 45 else "龍頭偏強" if avg >= 25 else "龍頭普通" if avg >= 10 else "龍頭偏弱"
    return {"leader_score": round2(avg), "leader_status": status, "leader_names": "、".join(names[:3])}


def sector_rankings(sec_scores, lead_scores):
    rows = []
    for sec, row in sec_scores.items():
        x = dict(row); x.update(lead_scores.get(sec, {"leader_score": 0, "leader_status": "無明確龍頭資料", "leader_names": "-"}))
        x["combined_sector_score"] = round2(x["sector_score"] + x["leader_score"]*0.5)
        rows.append(x)
    rows = sorted(rows, key=lambda x: x["combined_sector_score"], reverse=True)
    for i, r in enumerate(rows[:10], 1): r["rank"] = i
    return rows[:10]


def add_sector_relative_rank(items):
    mp = {}
    for item in items:
        mp.setdefault(item["sector"], []).append(item)
    for sec, arr in mp.items():
        sorted_arr = sorted(arr, key=lambda x: x.get("score", x.get("technical_score", 0)), reverse=True)
        total = len(sorted_arr)
        for i, item in enumerate(sorted_arr, 1):
            item["sector_relative_rank"] = i; item["sector_relative_total"] = total
            if i <= 3:
                item["sector_relative_status"] = "族群前三強"; item["sector_relative_score"] = 20
            elif i/total <= 0.2:
                item["sector_relative_status"] = "族群前20%"; item["sector_relative_score"] = 12
            elif i/total <= 0.5:
                item["sector_relative_status"] = "族群中段"; item["sector_relative_score"] = 0
            else:
                item["sector_relative_status"] = "族群後段"; item["sector_relative_score"] = -10
    return items


def entry_status(item):
    w = item.get("warnings", [])
    bs = item.get("breakout_state", "")
    if not item.get("is_liquid_enough"):
        return "流動性不足", "不列入", "成交量或金額不足"
    if "跌破月線" in w:
        return "弱勢取消型", "跌破取消", "跌破月線"
    if bs == "前高區失守":
        return "壓力區失守型", "跌破取消", "前高壓力區失守"
    if "距離月線過遠" in w or "5日漲幅過熱" in w or "20日漲幅過熱" in w or item.get("egg_zone") == "蛋殼過熱區" or item.get("candle_score", 0) <= -20:
        return "過熱觀察型", "過熱不追", "位階或漲幅偏高"
    if "週K偏弱" in item.get("weekly_trend", "") and bs != "盤整下緣試單":
        return "週K弱勢反彈型", "僅列觀察", "週K偏弱"
    if item.get("sector_status") == "弱勢族群" and item.get("leader_status") == "龍頭偏弱":
        return "族群弱勢型", "僅列觀察", "族群與龍頭偏弱"
    if bs == "前高區突破回採不破":
        if item.get("candle_score", 0) > 0 or item.get("main_score", 0) >= 40:
            return "前高區突破回採型", "可觀察進場", "回採不破"
        return "前高區突破回採型", "等轉強K", "需K棒確認"
    if bs == "盤整下緣試單":
        if item.get("candle_score", 0) > 0:
            return "盤整下緣試單型", "可觀察進場", "下緣轉強"
        return "盤整下緣試單型", "等轉強K", "等待K棒"
    if bs in ["突破前高區等回採", "突破盤整等回採"]:
        return "突破等回採型", "等回採", "突破後不追高"
    if bs == "前高壓力區盤整":
        return "前高區盤整型", "等突破", "消化賣壓"
    if item.get("technical_score", 0) >= 150 and item.get("main_score", 0) >= 35:
        return "低位啟動型", "等突破", "量價轉強"
    return "觀察型", "僅列觀察", "尚未達到買點"


def trade_plan(item):
    price, atr = item.get("price", 0), item.get("atr", 0)
    rl, rh, bl, bh = item.get("resistance_low", 0), item.get("resistance_high", 0), item.get("box_low", 0), item.get("box_high", 0)
    bs = item.get("breakout_state", "")
    if bs == "盤整下緣試單" and bl:
        support = bl; entry_low = round2(bl*1.003); entry_high = round2(min(bl+atr*0.6, bl*1.025)); no_entry = round2(bl*0.995); invalid = round2(bl*0.985); target = round2(bh if bh else entry_low+atr*3)
    elif rh:
        support = rh; entry_low = round2(rh*1.003); entry_high = round2(min(rh+atr*0.6, rh*1.025)); no_entry = round2(rh*0.995); invalid = round2(min(rl*0.99 if rl else rh*0.985, rh-atr*1.8)); target = round2(entry_low+atr*3)
    else:
        support = item.get("ma20", price); entry_low = round2(support*1.003); entry_high = round2(min(support+atr*0.6, support*1.025)); no_entry = round2(support*0.995); invalid = round2(support*0.985); target = round2(entry_low+atr*3)
    if entry_high < entry_low: entry_high = round2(entry_low + atr*0.3)
    stop = round2(max(support*0.985, entry_low-atr*1.5))
    rr = round2(max(target-entry_low, 0.01) / max(entry_low-stop, 0.01))
    rr_note = "風報比良好" if rr >= 2.0 else "風報比尚可，建議降低部位" if rr >= 1.5 else "風報比不足，等待更低進場價"
    if item.get("entry_status") == "可觀察進場" and rr >= MIN_RISK_REWARD_ENTRY:
        action = "明日開盤若站在進場區間且未跌破支撐，可第一筆試單"
    elif item.get("entry_status") == "可觀察進場":
        action = "風報比不足，等待更好買點"
    elif item.get("entry_status") in ["等回採", "等突破", "等轉強K"]:
        action = "持續觀察，等待回採、突破或轉強K"
    else:
        action = "觀察"
    return {"support_price": round2(support), "next_entry_low": entry_low, "next_entry_high": entry_high, "no_entry_price": no_entry, "invalid_price": invalid, "practical_stop": stop, "initial_stop": stop, "target_price": target, "risk_reward": rr, "risk_reward_note": rr_note, "risk_reward_group": risk_reward_group(rr), "ai_next_action": action, "trade_plan_note": "依Top-Down、買點、風報比綜合判斷"}


def position_sizing(item, market):
    entry = item.get("next_entry_low") or item.get("price")
    stop = item.get("practical_stop") or item.get("initial_stop")
    amount = ACCOUNT_SIZE * RISK_PER_TRADE * market.get("risk_multiplier", 0)
    if item.get("risk_reward", 0) < MIN_RISK_REWARD_ENTRY:
        amount = 0
    if not entry or not stop or entry <= stop or amount <= 0:
        return {"suggest_shares": 0, "suggest_lots": 0, "position_value": 0, "risk_per_share": 0}
    shares = math.floor(amount/(entry-stop))
    return {"suggest_shares": shares, "suggest_lots": math.floor(shares/1000), "position_value": round2(shares*entry), "risk_per_share": round2(entry-stop)}


def open_execution(df, item):
    if df is None or len(df) < 25:
        return {"open_price": 0, "day_high": 0, "day_low": 0, "day_close": 0, "open_status": "資料不足", "intraday_status": "資料不足", "execution_action": "等待資料", "execution_score": 0, "execution_note": "資料不足"}
    last = df.iloc[-1]
    op, hi, lo, cl, vol = [safe_float(last[x]) for x in ["Open", "High", "Low", "Close", "Volume"]]
    el, eh, ne, support, rh = [safe_float(item.get(x)) for x in ["next_entry_low", "next_entry_high", "no_entry_price", "support_price", "resistance_high"]]
    avgv = safe_float(df["Volume"].rolling(20).mean().iloc[-1])
    score = 0
    if not el or not eh:
        return {"open_price": round2(op), "day_high": round2(hi), "day_low": round2(lo), "day_close": round2(cl), "open_status": "尚無進場區", "intraday_status": "尚無進場區", "execution_action": "等待交易計畫", "execution_score": 0, "execution_note": "缺少進場區"}
    gap = pct(op, safe_float(df["Close"].iloc[-2])) if len(df) >= 2 else 0
    if op > eh*1.02 or gap > 3:
        open_status = "開盤跳高不追"; score -= 25
    elif op < ne:
        open_status = "開盤跌破不進"; score -= 35
    elif el <= op <= eh:
        open_status = "開盤落在進場區"; score += 25
    elif op < el and op >= ne:
        open_status = "開盤低於進場區但未破支撐"; score += 5
    else:
        open_status = "開盤等待確認"
    high_vol = vol > avgv*1.5 if avgv else False
    low_vol = vol < avgv*0.8 if avgv else False
    if support and lo < support*0.995 and cl >= support*1.003:
        intraday = "盤中跌破後站回支撐"; score += 20
    elif support and cl < support*0.995 and high_vol:
        intraday = "盤中跌破支撐且量增"; score -= 35
    elif rh and cl > rh*1.003 and high_vol:
        intraday = "盤中放量突破壓力"; score += 25
    elif low_vol and cl < el:
        intraday = "量能不足等待確認"; score -= 10
    elif el <= cl <= eh:
        intraday = "收盤仍在進場區"; score += 15
    elif cl > eh*1.02:
        intraday = "已脫離進場區不追"; score -= 15
    else:
        intraday = "盤中觀察中"
    action = "可試單" if score >= 35 else "可小部位試單" if score >= 15 else "等待確認" if score >= 0 else "取消候選" if score <= -30 else "暫停進場"
    return {"open_price": round2(op), "day_high": round2(hi), "day_low": round2(lo), "day_close": round2(cl), "open_status": open_status, "intraday_status": intraday, "execution_action": action, "execution_score": round2(score), "execution_note": f"開盤：{open_status}；盤中/收盤：{intraday}"}


def classify(item):
    if not item.get("is_liquid_enough") or item.get("entry_status") in ["跌破取消", "過熱不追", "不列入"] or item.get("candle_score", 0) <= -20 or "跌破月線" in item.get("warnings", []):
        return None
    if item.get("score", 0) >= 245 and item.get("main_score", 0) >= 50 and item.get("combined_sector_score", 0) >= 25:
        return "S"
    if item.get("score", 0) >= 185 and item.get("main_score", 0) >= 25 and item.get("combined_sector_score", 0) >= 10:
        return "A"
    return None


def trade_value(log):
    total = safe_float(log.get("total_pnl"), None)
    if total is not None and total != 0: return total
    pctv, entry, shares = safe_float(log.get("pnl_pct", 0)), safe_float(log.get("entry_price", 0)), safe_int(log.get("shares", 0))
    if entry and shares: return round2(entry*shares*pctv/100)
    return pctv


def group_stats(logs, key):
    mp = {}
    for log in logs:
        mp.setdefault(log.get(key) or "未分類", []).append(log)
    rows = []
    for name, arr in mp.items():
        vals = [trade_value(x) for x in arr]
        pcts = [safe_float(x.get("pnl_pct", 0)) for x in arr]
        wins, losses = [v for v in vals if v > 0], [v for v in vals if v < 0]
        count = len(arr); winrate = round2(len(wins)/count*100) if count else 0; total = round2(sum(vals)); avg_return = round2(sum(pcts)/count) if count else 0
        avg_win = round2(sum(wins)/len(wins)) if wins else 0; avg_loss = round2(sum(losses)/len(losses)) if losses else 0
        payoff = round2(abs(avg_win/avg_loss)) if avg_loss else 0; expectancy = round2(total/count) if count else 0
        if count < MIN_FEEDBACK_SAMPLE: comment, level = "樣本不足，先觀察", "neutral"
        elif winrate >= 60 and total > 0 and avg_return > 0: comment, level = "表現良好，可保留或提高權重", "good"
        elif winrate < 40 and total < 0: comment, level = "表現偏弱，建議降權或提高門檻", "bad"
        elif total > 0: comment, level = "有獲利能力，但仍需觀察穩定性", "normal"
        else: comment, level = "效果普通，建議保守使用", "warning"
        rows.append({"name": name, "count": count, "winrate": winrate, "avg_return": avg_return, "total_pnl": total, "avg_win": avg_win, "avg_loss": avg_loss, "payoff_ratio": payoff, "expectancy": expectancy, "ai_comment": comment, "ai_level": level})
    return sorted(rows, key=lambda x: (x["total_pnl"], x["winrate"], x["count"]), reverse=True)


def days_between(start, end):
    try:
        d1 = datetime.strptime(str(start)[:10], "%Y-%m-%d"); d2 = datetime.strptime(str(end)[:10], "%Y-%m-%d")
        return max((d2-d1).days, 0)
    except Exception:
        return 0


def strategy_dashboard(logs):
    if not logs:
        return {"total_count":0,"winrate":0,"total_pnl":0,"avg_return":0,"avg_win":0,"avg_loss":0,"payoff_ratio":0,"best_trade":"-","worst_trade":"-","avg_hold_days":0,"avg_max_favorable":0,"avg_giveback":0,"stop_execution_rate":0,"by_buy_type":[],"by_sector":[],"by_level":[],"by_risk_reward":[],"by_market":[],"by_leader":[],"by_failure_type":[],"by_execution_quality":[],"ai_summary":"目前尚無結案交易，先累積樣本。"}
    vals = [trade_value(x) for x in logs]; pcts = [safe_float(x.get("pnl_pct",0)) for x in logs]
    wins = [v for v in vals if v>0]; losses = [v for v in vals if v<0]; count=len(logs)
    total = round2(sum(vals)); winrate = round2(len(wins)/count*100); avg_return = round2(sum(pcts)/count)
    avg_win = round2(sum(wins)/len(wins)) if wins else 0; avg_loss = round2(sum(losses)/len(losses)) if losses else 0; payoff = round2(abs(avg_win/avg_loss)) if avg_loss else 0
    best = max(logs, key=trade_value); worst = min(logs, key=trade_value)
    hold_days = [days_between(x.get("entry_date",""), x.get("exit_date","")) for x in logs]
    favs = [safe_float(x.get("max_favorable_pct",0)) for x in logs]; gives=[safe_float(x.get("profit_giveback_pct",0)) for x in logs]
    losses_count = len([x for x in logs if safe_float(x.get("pnl_pct",0)) < 0])
    stopped = len([x for x in logs if str(x.get("failure_type","")).find("未照停損") == -1 and safe_float(x.get("pnl_pct",0)) < 0])
    by_buy_type = group_stats(logs,"buy_type"); by_failure_type = group_stats(logs,"failure_type")
    notes = ["整體策略目前為正向，可持續累積樣本。" if winrate >= 55 and total > 0 else "整體績效目前偏弱，建議降低部位並檢查失敗類型。" if total < 0 else "整體樣本仍需累積，先不要過度調整策略。"]
    if by_buy_type: notes.append(f"目前表現較佳買點為「{by_buy_type[0]['name']}」，勝率 {by_buy_type[0]['winrate']}%。")
    return {"total_count":count,"winrate":winrate,"total_pnl":total,"avg_return":avg_return,"avg_win":avg_win,"avg_loss":avg_loss,"payoff_ratio":payoff,"best_trade":f"{best.get('name','-')} / {trade_value(best)}","worst_trade":f"{worst.get('name','-')} / {trade_value(worst)}","avg_hold_days":round2(sum(hold_days)/len(hold_days)) if hold_days else 0,"avg_max_favorable":round2(sum(favs)/len(favs)) if favs else 0,"avg_giveback":round2(sum(gives)/len(gives)) if gives else 0,"stop_execution_rate":round2(stopped/losses_count*100) if losses_count else 0,"by_buy_type":by_buy_type,"by_sector":group_stats(logs,"sector"),"by_level":group_stats(logs,"level"),"by_risk_reward":group_stats(logs,"risk_reward_group"),"by_market":group_stats(logs,"market_status"),"by_leader":group_stats(logs,"leader_status"),"by_failure_type":by_failure_type,"by_execution_quality":group_stats(logs,"execution_quality"),"ai_summary":" ".join(notes)}


def strategy_feedback(logs):
    dash = strategy_dashboard(logs)
    feedback = {"enabled": len(logs)>=MIN_FEEDBACK_SAMPLE, "rows": [], "weights": {}, "summary": ""}
    if len(logs) < MIN_FEEDBACK_SAMPLE:
        feedback["summary"] = f"目前結案交易 {len(logs)} 筆，未達 {MIN_FEEDBACK_SAMPLE} 筆，AI反饋權重暫不啟用。"
        return feedback
    for group, rows in {"buy_type":dash["by_buy_type"],"sector":dash["by_sector"],"level":dash["by_level"],"risk_reward_group":dash["by_risk_reward"],"market_status":dash["by_market"],"leader_status":dash["by_leader"],"failure_type":dash["by_failure_type"],"execution_quality":dash["by_execution_quality"]}.items():
        feedback["weights"][group] = {}
        for r in rows:
            if r["count"] < MIN_FEEDBACK_SAMPLE: score = 0
            elif r["winrate"] >= 60 and r["total_pnl"] > 0: score = 12
            elif r["winrate"] < 40 and r["total_pnl"] < 0: score = -18
            else: score = 0
            feedback["weights"][group][r["name"]] = score
            if score:
                feedback["rows"].append({"group":group,"name":r["name"],"score":score,"note":"歷史績效自動調整","count":r["count"],"winrate":r["winrate"],"total_pnl":r["total_pnl"],"avg_return":r["avg_return"]})
    pos = len([x for x in feedback["rows"] if x["score"]>0]); neg = len([x for x in feedback["rows"] if x["score"]<0])
    feedback["summary"] = f"AI反饋權重已啟用：產生 {pos} 個加分條件、{neg} 個扣分條件。"
    return feedback


def apply_feedback(item, feedback):
    if not feedback.get("enabled"):
        item["feedback_score"] = 0; item["feedback_notes"] = ["樣本不足，尚未套用AI反饋權重"]
        return item
    total = 0; notes=[]; weights=feedback.get("weights",{})
    mapping={"buy_type":item.get("buy_type","未分類"),"sector":item.get("sector","未分類"),"level":item.get("level","未分類"),"risk_reward_group":item.get("risk_reward_group","未分類"),"market_status":item.get("market_status","未分類"),"leader_status":item.get("leader_status","未分類")}
    for g,k in mapping.items():
        s=safe_float(weights.get(g,{}).get(k,0)); total += s
        if s: notes.append(f"{g}:{k} {s:+}")
    item["feedback_score"]=round2(total); item["feedback_notes"]=notes or ["無明顯反饋調整"]; item["score"]=round2(item.get("score",0)+total)
    return item


def update_candidate_pool(items):
    old=read_json(CANDIDATE_FILE,{"candidates":{}}).get("candidates",{})
    now=taiwan_now(); today=today_str(); new={}; alerts=[]
    for item in items:
        if item.get("level") not in ["S","A"] or item.get("entry_status") in ["跌破取消","過熱不追","不列入","流動性不足"]: continue
        sym=item["symbol"]; prev=old.get(sym,{})
        c={k:item.get(k) for k in ["symbol","name","level","sector","buy_type","score","feedback_score","support_price","next_entry_low","next_entry_high","no_entry_price","invalid_price","practical_stop","risk_reward","risk_reward_note","risk_reward_group","target_price","sector_status","leader_status","leader_names","market_status","resistance_low","resistance_high","box_low","box_high","breakout_state","egg_zone","candle_signal","weekly_trend","daily_signal","mtf_status","sector_relative_rank","sector_relative_total","sector_relative_status","open_status","intraday_status","execution_action","execution_score","execution_note","open_price","day_high","day_low","day_close","ai_next_action","trade_plan_note"]}
        c.update({"previous_status":prev.get("current_status","-"),"current_status":item.get("entry_status","-"),"feedback_notes":item.get("feedback_notes",[]),"updated_at":now,"first_seen":prev.get("first_seen",today),"last_seen":today})
        new[sym]=c
        if c["current_status"]=="可觀察進場" and c.get("risk_reward",0)>=MIN_RISK_REWARD_ENTRY and c.get("execution_action") in ["可試單","可小部位試單","等待確認"]:
            alerts.append(c)
    new=dict(sorted(new.items(), key=lambda kv:(kv[1].get("current_status")=="可觀察進場", kv[1].get("execution_action") in ["可試單","可小部位試單"], kv[1].get("risk_reward",0), kv[1].get("score",0)), reverse=True))
    alerts=sorted(alerts, key=lambda x:(x.get("level")=="S", x.get("execution_action")=="可試單", x.get("risk_reward",0), x.get("score",0)), reverse=True)[:MAX_ENTRY_ALERTS]
    data={"updated_at":now,"candidates":new,"entry_alerts":alerts}
    write_json(CANDIDATE_FILE,data)
    return data



# =====================================================
# LINE 推播通知：Webhook + 測試推播 + 自動排程
# =====================================================
def get_line_token():
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()


def get_line_user_id():
    env_user_id = os.getenv("LINE_USER_ID", "").strip()
    if env_user_id:
        return env_user_id
    data = read_json(LINE_USER_FILE, {})
    return data.get("user_id", "").strip()


def save_line_user_id(user_id):
    if user_id:
        write_json(LINE_USER_FILE, {
            "user_id": user_id,
            "updated_at": taiwan_now()
        })


def line_enabled():
    return bool(get_line_token()) and bool(get_line_user_id())


def push_line_message(message):
    token = get_line_token()
    user_id = get_line_user_id()

    if not token:
        return False, "尚未設定 LINE_CHANNEL_ACCESS_TOKEN"

    if not user_id:
        return False, "尚未取得 LINE User ID。請先設定 Webhook，並用你的 LINE 傳訊息給官方帳號。"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": str(message)[:4900]
            }
        ]
    }

    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers,
            json=payload,
            timeout=15
        )

        if r.status_code in [200, 202]:
            return True, "LINE通知已送出"

        return False, f"LINE通知失敗：HTTP {r.status_code} {r.text[:300]}"

    except Exception as e:
        return False, f"LINE通知例外：{e}"


def short_symbol(symbol):
    return str(symbol).replace(".TW", "").replace(".TWO", "")



def load_signal_database():
    return read_json(SIGNAL_DATABASE_FILE, {"updated_at": taiwan_now(), "signals": []})


def save_signal_database(data):
    data["updated_at"] = taiwan_now()
    write_json(SIGNAL_DATABASE_FILE, data)


def signal_unique_key(signal_type, symbol, action, price):
    return f"{today_str()}|{signal_type}|{symbol}|{action}|{round2(price)}"


def record_ai_signal(signal_type, item, action_level, action, price, note="", extra=None):
    """
    AI訊號資料庫：
    用來跑1～2個月驗證訊號，不是直接保證獲利。
    目前會記錄：
    1. 09:10持股風控訊號
    2. 13:20可試單進場訊號
    3. 後續3/5/10日結果會由evaluate_signal_database()補上
    """
    try:
        db = load_signal_database()
        signals = db.get("signals", [])
        symbol = item.get("symbol", "")
        if not symbol:
            return

        key = signal_unique_key(signal_type, symbol, action, price)
        if any(x.get("key") == key for x in signals):
            return

        rec = {
            "key": key,
            "date": today_str(),
            "time": taiwan_now(),
            "signal_type": signal_type,
            "action_level": action_level,
            "action": action,
            "symbol": symbol,
            "short_symbol": short_symbol(symbol),
            "name": item.get("name", "-"),
            "price": round2(price),
            "level": item.get("level", "-"),
            "sector": item.get("sector", "-"),
            "buy_type": item.get("buy_type", "-"),
            "entry_low": item.get("next_entry_low", "-"),
            "entry_high": item.get("next_entry_high", "-"),
            "stop": item.get("practical_stop", item.get("no_entry_price", "-")),
            "target": item.get("target_price", "-"),
            "risk_reward": item.get("risk_reward", "-"),
            "score": item.get("score", "-"),
            "market_status": item.get("market_status", "-"),
            "note": note,
            "evaluated": False,
            "result": {},
        }

        if extra:
            rec.update(extra)

        signals.append(rec)
        db["signals"] = signals[-1000:]
        save_signal_database(db)

    except Exception as e:
        print("record_ai_signal failed", e)


def evaluate_signal_database():
    """
    每次盤後掃描時更新訊號結果：
    - 3日、5日、10日後收盤報酬
    - 訊號後最高漲幅、最大回檔
    - 是否碰到停損 / 目標價
    """
    try:
        db = load_signal_database()
        signals = db.get("signals", [])
        changed = False

        for rec in signals:
            if rec.get("signal_type") != "entry":
                continue

            symbol = rec.get("symbol")
            price = safe_float(rec.get("price", 0))
            if not symbol or not price:
                continue

            try:
                signal_date = pd.to_datetime(rec.get("date"))
            except Exception:
                continue

            df = download_stock(symbol, "3mo")
            if df is None or df.empty:
                continue

            after = df[df.index.normalize() >= signal_date.normalize()]
            if after.empty:
                continue

            result = rec.get("result", {})
            stop = safe_float(rec.get("stop", 0))
            target = safe_float(rec.get("target", 0))

            for days in [3, 5, 10]:
                if len(after) >= days:
                    window = after.head(days)
                    close_px = safe_float(window["Close"].iloc[-1])
                    high_px = safe_float(window["High"].max())
                    low_px = safe_float(window["Low"].min())
                    result[f"return_{days}d_pct"] = round2(pct(close_px, price))
                    result[f"max_up_{days}d_pct"] = round2(pct(high_px, price))
                    result[f"max_down_{days}d_pct"] = round2(pct(low_px, price))
                    result[f"close_{days}d"] = round2(close_px)

            if stop:
                result["hit_stop"] = bool(safe_float(after["Low"].min()) <= stop)

            if target:
                result["hit_target"] = bool(safe_float(after["High"].max()) >= target)

            if len(after) >= 10:
                rec["evaluated"] = True

            rec["result"] = result
            changed = True

        if changed:
            save_signal_database(db)

    except Exception as e:
        print("evaluate_signal_database failed", e)


def signal_summary_text():
    db = load_signal_database()
    signals = db.get("signals", [])
    entries = [x for x in signals if x.get("signal_type") == "entry"]
    holdings = [x for x in signals if x.get("signal_type") == "holding"]

    done3 = [x for x in entries if x.get("result", {}).get("return_3d_pct") is not None]
    done5 = [x for x in entries if x.get("result", {}).get("return_5d_pct") is not None]
    done10 = [x for x in entries if x.get("result", {}).get("return_10d_pct") is not None]

    def avg_return(rows, key):
        if not rows:
            return 0
        return round2(sum(safe_float(x.get("result", {}).get(key, 0)) for x in rows) / len(rows))

    def win_rate(rows, key):
        if not rows:
            return 0
        return round2(len([x for x in rows if safe_float(x.get("result", {}).get(key, 0)) > 0]) / len(rows) * 100)

    return (
        f"AI交易助理正式驗證版\\n"
        f"版本：{APP_VERSION_NAME}\\n"
        f"驗證模式：{'開啟' if VALIDATION_MODE else '關閉'}\\n"
        f"持股風控訊號：{len(holdings)} 筆\\n"
        f"進場試單訊號：{len(entries)} 筆\\n"
        f"\\n"
        f"3日結果：{len(done3)} 筆｜平均報酬 {avg_return(done3, 'return_3d_pct')}%｜勝率 {win_rate(done3, 'return_3d_pct')}%\\n"
        f"5日結果：{len(done5)} 筆｜平均報酬 {avg_return(done5, 'return_5d_pct')}%｜勝率 {win_rate(done5, 'return_5d_pct')}%\\n"
        f"10日結果：{len(done10)} 筆｜平均報酬 {avg_return(done10, 'return_10d_pct')}%｜勝率 {win_rate(done10, 'return_10d_pct')}%\\n"
        f"\\n"
        f"自我優化狀態：{load_strategy_weights().get('mode', 'collecting')}｜可統計樣本 {load_strategy_weights().get('entry_count', 0)} 筆\\n"\
        f"提醒：目前先跑100～200筆收集資料，不以此結果直接保證獲利。"
    )


def default_strategy_weights():
    return {
        "updated_at": taiwan_now(),
        "mode": "collecting",
        "entry_count": 0,
        "weights": {
            "buy_type": {},
            "sector": {},
            "level": {},
            "risk_reward_group": {},
            "market_status": {},
        },
        "stats": {},
        "suggestions": [],
    }


def load_strategy_weights():
    return read_json(STRATEGY_WEIGHTS_FILE, default_strategy_weights())


def save_strategy_weights(data):
    data["updated_at"] = taiwan_now()
    write_json(STRATEGY_WEIGHTS_FILE, data)


def load_optimization_log():
    return read_json(OPTIMIZATION_LOG_FILE, [])


def save_optimization_log(rows):
    write_json(OPTIMIZATION_LOG_FILE, rows[-200:])


def rr_group(value):
    v = safe_float(value, 0)
    if v >= 3:
        return "RR>=3"
    if v >= 2:
        return "RR 2~3"
    if v >= 1.5:
        return "RR 1.5~2"
    if v > 0:
        return "RR<1.5"
    return "RR未知"


def signal_group_value(rec, key):
    if key == "risk_reward_group":
        return rr_group(rec.get("risk_reward"))
    return rec.get(key) or "未分類"


def summarize_group_performance(entries, key):
    groups = {}

    for rec in entries:
        result = rec.get("result", {})
        if result.get(OPTIMIZE_TARGET_RETURN_KEY) is None:
            continue

        name = signal_group_value(rec, key)
        groups.setdefault(name, []).append(rec)

    rows = []

    for name, arr in groups.items():
        vals = [safe_float(x.get("result", {}).get(OPTIMIZE_TARGET_RETURN_KEY, 0)) for x in arr]
        max_down = [safe_float(x.get("result", {}).get("max_down_5d_pct", 0)) for x in arr if x.get("result", {}).get("max_down_5d_pct") is not None]
        hit_stop = [x for x in arr if x.get("result", {}).get("hit_stop") is True]
        hit_target = [x for x in arr if x.get("result", {}).get("hit_target") is True]
        count = len(vals)

        if not count:
            continue

        rows.append({
            "group_key": key,
            "group_name": name,
            "count": count,
            "win_rate": round2(len([v for v in vals if v > 0]) / count * 100),
            "avg_return": round2(sum(vals) / count),
            "avg_max_down": round2(sum(max_down) / len(max_down)) if max_down else 0,
            "hit_stop_rate": round2(len(hit_stop) / count * 100),
            "hit_target_rate": round2(len(hit_target) / count * 100),
        })

    return sorted(rows, key=lambda x: (x["avg_return"], x["win_rate"], x["count"]), reverse=True)


def decide_weight_adjustment(row, total_entries):
    """
    回傳權重調整值。
    樣本不足只建議，不自動大幅調整。
    """
    count = row.get("count", 0)
    win_rate = safe_float(row.get("win_rate", 0))
    avg_return = safe_float(row.get("avg_return", 0))
    hit_stop_rate = safe_float(row.get("hit_stop_rate", 0))

    if count < OPTIMIZE_MIN_GROUP_COUNT:
        return 0, "樣本不足，暫不調整"

    # 30~99筆：只建議，不套用
    if total_entries < OPTIMIZE_SMALL_AFTER:
        if win_rate >= 60 and avg_return > 1:
            return 0, "表現佳，建議未來加權"
        if win_rate <= 42 and avg_return < 0:
            return 0, "表現弱，建議未來降權"
        return 0, "觀察中"

    # 100~199筆：小幅自動調整
    step_good = 5
    step_bad = -5

    # 200筆以上：中度自動調整
    if total_entries >= OPTIMIZE_MEDIUM_AFTER:
        step_good = 8
        step_bad = -8

    if win_rate >= 62 and avg_return >= 1.2:
        return step_good, "勝率與平均報酬佳，自動加權"
    if win_rate >= 56 and avg_return >= 0.8 and hit_stop_rate < 35:
        return round2(step_good * 0.6), "表現穩定，小幅加權"
    if win_rate <= 42 and avg_return <= 0:
        return step_bad, "勝率低且報酬不佳，自動降權"
    if hit_stop_rate >= 55 and avg_return < 0.5:
        return step_bad, "停損命中率偏高，自動降權"

    return 0, "暫不調整"


def optimize_strategy_weights():
    """
    AI自我優化模組：
    - 30筆前：只收集
    - 30~99筆：產生建議
    - 100~199筆：小幅自動調整
    - 200筆以上：中度自動調整
    """
    if not AUTO_OPTIMIZATION_ENABLED:
        return load_strategy_weights()

    evaluate_signal_database()

    db = load_signal_database()
    signals = db.get("signals", [])
    entries = [
        x for x in signals
        if x.get("signal_type") == "entry"
        and x.get("result", {}).get(OPTIMIZE_TARGET_RETURN_KEY) is not None
    ]

    total = len(entries)
    data = load_strategy_weights()
    weights = data.get("weights", default_strategy_weights()["weights"])

    if total < OPTIMIZE_SUGGEST_AFTER:
        data.update({
            "mode": "collecting",
            "entry_count": total,
            "stats": {},
            "suggestions": [f"目前有效樣本 {total} 筆，未滿 {OPTIMIZE_SUGGEST_AFTER} 筆，只收集不優化。"],
        })
        save_strategy_weights(data)
        return data

    all_stats = {}
    suggestions = []
    changes = []

    for key in ["buy_type", "sector", "level", "risk_reward_group", "market_status"]:
        rows = summarize_group_performance(entries, key)
        all_stats[key] = rows

        for row in rows:
            delta, reason = decide_weight_adjustment(row, total)
            name = row["group_name"]

            if total >= OPTIMIZE_SMALL_AFTER and delta != 0:
                old = safe_float(weights.setdefault(key, {}).get(name, 0))
                new = max(OPTIMIZE_MIN_WEIGHT, min(OPTIMIZE_MAX_WEIGHT, old + delta))
                weights[key][name] = round2(new)

                changes.append({
                    "time": taiwan_now(),
                    "group_key": key,
                    "group_name": name,
                    "old_weight": old,
                    "new_weight": round2(new),
                    "delta": delta,
                    "reason": reason,
                    "count": row.get("count"),
                    "win_rate": row.get("win_rate"),
                    "avg_return": row.get("avg_return"),
                })

            if row.get("count", 0) >= OPTIMIZE_MIN_GROUP_COUNT:
                suggestions.append(
                    f"{key}:{name}｜{row.get('count')}筆｜勝率{row.get('win_rate')}%｜"
                    f"均報{row.get('avg_return')}%｜{reason}"
                )

    mode = "suggestion_only"
    if total >= OPTIMIZE_MEDIUM_AFTER:
        mode = "medium_auto"
    elif total >= OPTIMIZE_SMALL_AFTER:
        mode = "small_auto"

    data.update({
        "mode": mode,
        "entry_count": total,
        "weights": weights,
        "stats": all_stats,
        "suggestions": suggestions[:30],
        "last_optimized_at": taiwan_now(),
    })

    save_strategy_weights(data)

    if changes:
        log = load_optimization_log()
        log.extend(changes)
        save_optimization_log(log)

    return data


def adaptive_weight_score(item):
    """
    把已驗證出來的高勝率條件加入分數。
    權重來源：strategy_weights.json
    """
    data = load_strategy_weights()
    weights = data.get("weights", {})
    total = safe_int(data.get("entry_count", 0))

    # 樣本數未滿100前，不真正影響選股，只統計與建議。
    if total < OPTIMIZE_SMALL_AFTER:
        return 0, []

    score = 0
    notes = []

    mapping = {
        "buy_type": item.get("buy_type", "未分類"),
        "sector": item.get("sector", "未分類"),
        "level": item.get("level", "未分類"),
        "risk_reward_group": rr_group(item.get("risk_reward")),
        "market_status": item.get("market_status", "未分類"),
    }

    for key, value in mapping.items():
        w = safe_float(weights.get(key, {}).get(value, 0))
        if w:
            score += w
            notes.append(f"{key}:{value} {w:+}")

    return round2(score), notes


def optimization_summary_text():
    data = optimize_strategy_weights()
    weights = data.get("weights", {})
    suggestions = data.get("suggestions", [])
    mode = data.get("mode", "collecting")
    count = data.get("entry_count", 0)

    rows = [
        "AI自我優化模組",
        f"模式：{mode}",
        f"可統計進場樣本：{count} 筆",
        "",
        "目前權重：",
    ]

    for key, mp in weights.items():
        if not mp:
            continue
        rows.append(f"\n[{key}]")
        for name, val in sorted(mp.items(), key=lambda kv: safe_float(kv[1]), reverse=True)[:10]:
            rows.append(f"{name}: {val:+}")

    rows.append("\n優化建議：")
    if suggestions:
        rows.extend(suggestions[:20])
    else:
        rows.append("目前樣本不足，先持續收集資料。")

    return "\n".join(rows)



def classify_holding_line_action(t):
    status = t.get("ai_holding_status", "")
    pnl = safe_float(t.get("pnl", 0))
    curr = safe_float(t.get("curr", 0))
    standard = safe_float(t.get("standard_trail", 0))
    practical_stop = safe_float(t.get("practical_stop", 0))
    support = safe_float(t.get("support_price", 0))
    max_favorable = safe_float(t.get("max_favorable_pct", 0))
    giveback = safe_float(t.get("profit_giveback_pct", 0))

    # A級：必須處理
    if status in ["實戰停損", "假突破失效", "支撐失守"] or (practical_stop and curr and curr <= practical_stop):
        return "A", "停損", "🚨", "跌破實戰停損或支撐，建議優先出場。"

    # B級：建議處理
    if status in ["跌破標準風控", "跌破保守風控"] or (standard and curr and curr <= standard):
        return "B", "停利/減碼", "⚠️", "跌破移動風控，建議停利或減碼。"

    if status in ["接近第一滿足點", "接近第二滿足點", "接近滿足點需停利", "獲利回吐警戒"]:
        return "B", "停利", "💰", "接近滿足點或獲利回吐，建議分批停利。"

    if max_favorable >= 8 and giveback >= 5:
        return "B", "停利/減碼", "⚠️", "浮盈回吐偏多，建議至少部分鎖利。"

    # C級：可處理，但不是強制
    if pnl >= 2 and status in ["續抱", "假跌破站回"] and support and curr and curr > support * 1.02:
        return "C", "可觀察加碼", "🟢", "持股轉強，可觀察是否加碼，但不追高。"

    # D級：只觀察，不推播
    return "D", "續抱", "✅", "尚未觸發停利或停損，系統自行觀察。"

def format_line_holding_status():
    tracks = load_track()

    if not tracks:
        return "📌 持股狀態\n目前沒有追蹤中的持股。"

    rows = ["📌 持股狀態"]
    has_warning = False

    for t in tracks:
        try:
            df = download_stock(t["symbol"], "1y")
            t = manage_holding(t, df)
        except Exception:
            pass

        action, icon, simple_note = classify_holding_line_action(t)

        if action != "續抱":
            has_warning = True

        rows.append(
            f"\n{icon} {t.get('name','-')} {short_symbol(t.get('symbol',''))}\n"
            f"建議：{action}\n"
            f"現價：{t.get('curr','-')}｜成本：{t.get('price','-')}｜損益：{t.get('pnl','-')}%\n"
            f"停損：{t.get('practical_stop','-')}｜風控：{t.get('trail_range','-')}\n"
            f"說明：{simple_note}"
        )

    if not has_warning:
        rows.insert(1, "\n目前沒有需要馬上處理的持股。")

    return "\n".join(rows)

def format_line_entry_alerts():
    scan = load_scan_results()
    alerts = scan.get("entry_alerts", [])

    if not alerts:
        return (
            "🚀 AI進場提醒\n"
            "目前沒有C級以上可試單訊號。\n"
            "D級觀察股會留在候選池，由系統自行追蹤，不另外通知。"
        )

    rows = ["🚀 AI進場提醒｜只顯示可試單"]

    for a in alerts[:5]:
        name = a.get("name", "-")
        symbol = short_symbol(a.get("symbol", ""))
        buy_type = a.get("buy_type", "-")
        entry_low = a.get("next_entry_low", "-")
        entry_high = a.get("next_entry_high", "-")
        no_entry = a.get("no_entry_price", "-")
        stop = a.get("practical_stop", "-")
        rr = a.get("risk_reward", "-")

        rows.append(
            f"\n🚀 C級｜可試單\n"
            f"{name} {symbol}\n"
            f"買點：{buy_type}\n"
            f"進場區：{entry_low} ～ {entry_high}\n"
            f"跌破不進：{no_entry}\n"
            f"停損：{stop}\n"
            f"風報比：{rr}"
        )

    return "\n".join(rows)

def format_line_daily_review():
    scan = load_scan_results()
    tracks = load_track()
    sectors = scan.get("sector_rankings", [])
    top_sectors = "、".join([x.get("sector", "-") for x in sectors[:3]]) if sectors else "無資料"

    return (
        "📊 AI盤後檢討\n"
        f"大盤：{scan.get('market_status', '-')}\n"
        f"操作模式：{scan.get('risk_mode', '-')}\n"
        f"風險開關：{scan.get('risk_switch', '-')}\n"
        f"股票池：{scan.get('stock_pool_count', 0)} 檔\n"
        f"今日進場提醒：{len(scan.get('entry_alerts', []))} 檔\n"
        f"AI候選：{scan.get('candidate_count', 0)} 檔\n"
        f"已追蹤持股：{len(tracks)} 檔\n"
        f"主流族群：{top_sectors}\n"
        f"掃描時間：{scan.get('updated_at', '-')}"
    )



def format_line_open_watch():
    tracks = load_track()

    rows = [
        "🌅 09:10 AI持股監控",
        "只通知需要處理的持股；一般續抱不打擾。"
    ]

    if not tracks:
        rows.append("\n目前沒有追蹤中的持股。")
        return "\n".join(rows)

    action_rows = []
    silent_count = 0

    for t in tracks:
        try:
            df = download_stock(t["symbol"], "1y")
            t = manage_holding(t, df)
        except Exception:
            pass

        level, action, icon, simple_note = classify_holding_line_action(t)

        if level == "D":
            silent_count += 1
            continue

        curr = safe_float(t.get("curr", 0))

        record_ai_signal(
            "holding",
            t,
            level,
            action,
            curr,
            simple_note,
            extra={
                "pnl": t.get("pnl", "-"),
                "cost": t.get("price", "-"),
                "support": t.get("support_price", "-"),
                "trail_range": t.get("trail_range", "-"),
            },
        )

        action_rows.append(
            f"\n{icon} {level}級｜{action}\n"
            f"{t.get('name','-')} {short_symbol(t.get('symbol',''))}\n"
            f"現價：{t.get('curr','-')}｜成本：{t.get('price','-')}｜損益：{t.get('pnl','-')}%\n"
            f"停損：{t.get('practical_stop','-')}｜支撐：{t.get('support_price','-')}\n"
            f"風控：{t.get('trail_range','-')}\n"
            f"原因：{simple_note}"
        )

    if action_rows:
        rows.append("\n以下持股需要處理：")
        rows.extend(action_rows)
        if silent_count:
            rows.append(f"\n其餘 {silent_count} 檔為D級觀察，系統已自動監控，不另外通知。")
    else:
        rows.append("\n目前沒有A/B/C級持股警示。")
        rows.append("D級續抱股由系統自行觀察，不另外通知。")

    return "\n".join(rows)

def format_line_preclose_decision():
    scan = load_scan_results()
    alerts = scan.get("entry_alerts", [])
    candidates = scan.get("candidate_pool", [])

    # 正式驗證版：只通知C級以上可試單訊號。
    # D級觀察股：等回採、開高不追、還沒到買點，全部留在候選池，不推播。
    watch_list = alerts if alerts else candidates[:20]

    rows = [
        "🕐 13:20 AI交易助理｜進場指令",
        f"大盤：{scan.get('market_status', '-')}",
        f"操作模式：{scan.get('risk_mode', '-')}",
        "驗證模式：只觀察與小部位試單評估，不給重倉建議。",
    ]

    if not watch_list:
        rows.append("\n目前沒有C級以上可試單訊號。")
        rows.append("D級觀察股由系統放入候選池追蹤，不另外通知。")
        return "\n".join(rows)

    trial_rows = []

    for a in watch_list[:20]:
        name = a.get("name", "-")
        symbol = short_symbol(a.get("symbol", ""))
        entry_low = safe_float(a.get("next_entry_low", 0))
        entry_high = safe_float(a.get("next_entry_high", 0))
        no_entry = safe_float(a.get("no_entry_price", 0))
        stop = safe_float(a.get("practical_stop", 0)) or no_entry
        rr = safe_float(a.get("risk_reward", 0))
        buy_type = a.get("buy_type", "-")
        current_price = safe_float(a.get("day_close", 0)) or safe_float(a.get("price", 0))

        if not current_price:
            try:
                df = download_stock(a.get("symbol"), "5d")
                current_price = safe_float(df["Close"].iloc[-1]) if df is not None and not df.empty else 0
            except Exception:
                current_price = 0

        is_trial = False
        advice = ""

        if current_price and entry_low and entry_high:
            if entry_low <= current_price <= entry_high and current_price > no_entry and rr >= MIN_RISK_REWARD_ENTRY:
                is_trial = True
                advice = "C級可試單：價格落在進場區，未跌破不進價，風報比符合。"
        else:
            if a.get("current_status") == "可觀察進場" and rr >= MIN_RISK_REWARD_ENTRY:
                is_trial = True
                advice = "C級可試單：候選池顯示可觀察進場。"

        if not is_trial:
            continue

        confidence = 60
        if a.get("level") == "S":
            confidence += 10
        if rr >= 2:
            confidence += 10
        if a.get("execution_action") == "可試單":
            confidence += 10
        if scan.get("allow_new_positions"):
            confidence += 5
        if a.get("sector_rank", 999) and safe_float(a.get("sector_rank", 999)) <= 5:
            confidence += 5

        adaptive_score, adaptive_notes = adaptive_weight_score(a)
        confidence += int(max(-10, min(10, adaptive_score / 2)))

        confidence = min(confidence, 95)

        # 正式驗證期：就算信心高，也只給小部位試單文字，不給重倉建議。
        if VALIDATION_MODE:
            position_note = MAX_VALIDATION_POSITION_TEXT
        else:
            position_note = "正常試單" if confidence >= 85 else "小部位試單"

        if confidence < MIN_AI_CONFIDENCE_TO_NOTIFY:
            continue

        record_ai_signal(
            "entry",
            a,
            "C",
            "可試單",
            current_price,
            advice,
            extra={
                "confidence": confidence,
                "position_note": position_note,
                "current_status": a.get("current_status", "-"),
                "execution_action": a.get("execution_action", "-"),
                "validation_mode": VALIDATION_MODE,
                "adaptive_score": adaptive_score,
                "adaptive_notes": adaptive_notes,
            },
        )

        trial_rows.append(
            f"\n🚀 C級｜可試單\n"
            f"{name} {symbol}\n"
            f"AI信心：{confidence}\n"
            f"建議：{position_note}\n"
            f"目前價：約 {round2(current_price)}\n"
            f"進場區：{entry_low} ～ {entry_high}\n"
            f"跌破不進：{no_entry}\n"
            f"停損：{round2(stop)}\n"
            f"風報比：{rr}\n"
            f"買點：{buy_type}\n"
            f"備註：正式驗證期先觀察，不建議直接放大資金。"
        )

    if trial_rows:
        rows.append("\n以下為可執行觀察訊號：")
        rows.extend(trial_rows[:5])
    else:
        rows.append("\n目前沒有C級以上可試單訊號。")
        rows.append("D級：等回採、開高不追、還沒到買點，已由系統放在候選池自行觀察。")

    return "\n".join(rows)

def format_line_next_day_plan():
    return (
        "📊 盤後通知已關閉\n"
        "目前設定不推播隔日交易計畫。\n"
        "系統仍可在網站內查看 AI候選池、進場提醒與族群排行。"
    )

def format_line_message(mode="all"):
    if mode == "open":
        return format_line_open_watch()
    if mode == "preclose":
        return format_line_preclose_decision()
    if mode == "nextday":
        return format_line_next_day_plan()
    if mode == "holding":
        return format_line_holding_status()
    if mode == "entry":
        return format_line_entry_alerts()
    if mode == "review":
        return format_line_daily_review()

    return (
        format_line_holding_status()
        + "\n\n----------------\n\n"
        + format_line_entry_alerts()
    )

def send_line_notification(mode="all"):
    ok, info = push_line_message(format_line_message(mode))

    logs = read_json(LINE_NOTIFY_LOG_FILE, [])
    logs.append({
        "mode": mode,
        "ok": ok,
        "info": info,
        "time": taiwan_now()
    })
    write_json(LINE_NOTIFY_LOG_FILE, logs[-50:])

    return ok, info


@app.route("/callback", methods=["GET", "POST"])
def line_callback():
    # 用瀏覽器打開 /callback 會看到這行，代表路由已存在。
    # LINE Verify 會用 POST，所以也支援 POST。
    if request.method == "GET":
        return "LINE callback ready", 200

    data = request.get_json(silent=True) or {}
    events = data.get("events", [])

    for event in events:
        source = event.get("source", {})
        user_id = source.get("userId")

        if user_id:
            save_line_user_id(user_id)
            push_line_message(
                "✅ AI選股通知已綁定成功\n"
                "之後系統會推播：\n"
                "1. 目前追蹤持股狀態\n"
                "2. 推薦進場股票與價格區間"
            )

    return "OK", 200


@app.route("/line-test")
def line_test():
    ok, info = send_line_notification("all")
    save_scan_status("done" if ok else "error", info)
    return redirect(url_for("index"))


@app.route("/line-holding")
def line_holding():
    ok, info = send_line_notification("holding")
    save_scan_status("done" if ok else "error", info)
    return redirect(url_for("index"))


@app.route("/line-entry")
def line_entry():
    ok, info = send_line_notification("entry")
    save_scan_status("done" if ok else "error", info)
    return redirect(url_for("index"))


@app.route("/line-open-watch")
def line_open_watch():
    ok, info = send_line_notification("open")
    save_scan_status("done" if ok else "error", info)
    return redirect(url_for("index"))


@app.route("/line-preclose")
def line_preclose():
    ok, info = send_line_notification("preclose")
    save_scan_status("done" if ok else "error", info)
    return redirect(url_for("index"))


@app.route("/line-nextday")
def line_nextday():
    save_scan_status("done", "16:05隔日交易計畫LINE推播已關閉。此版本只保留09:10持股風控與13:20可試單提醒。")
    return redirect(url_for("index"))






@app.route("/optimization-summary")
def optimization_summary():
    return Response(optimization_summary_text(), mimetype="text/plain; charset=utf-8")


@app.route("/strategy-weights")
def strategy_weights():
    return load_strategy_weights()


@app.route("/version")
def version():
    return {
        "version": APP_VERSION_NAME,
        "validation_mode": VALIDATION_MODE,
        "resource_saving_scan": ENABLE_RESOURCE_SAVING_SCAN,
        "auto_optimization": AUTO_OPTIMIZATION_ENABLED,
        "optimization_mode": load_strategy_weights().get("mode", "collecting"),
        "optimization_samples": load_strategy_weights().get("entry_count", 0),
        "line_notify": {
            "09:10": "AI持股監控，只通知A/B/C級",
            "13:20": "AI進場指令，只通知C級可試單",
            "16:05": "只背景掃描，不推播隔日交易計畫",
        },
    }


@app.route("/signal-database")
def signal_database():
    return load_signal_database()


@app.route("/signal-summary")
def signal_summary():
    return Response(signal_summary_text(), mimetype="text/plain; charset=utf-8")


def load_track(): return read_json(TRACK_FILE, [])
def save_track(data): write_json(TRACK_FILE, data)
def load_trade_log(): return read_json(TRADE_LOG_FILE, [])
def save_trade_log(data): write_json(TRADE_LOG_FILE, data)
def load_scan_results(): return read_json(RESULT_FILE, {"updated_at":"尚未掃描","market_status":"尚未掃描","market_score":0,"risk_mode":"-","risk_switch":"-","allow_new_positions":False,"risk_note":"-","market_egg_zone":"-","market_pressure_note":"-","stock_pool_count":0,"entry_alerts":[],"candidate_pool":[],"sector_rankings":[],"candidate_count":0,"strategy_feedback":{"enabled":False,"summary":"尚未建立AI反饋權重","rows":[]}})
def save_scan_results(data): write_json(RESULT_FILE, data)


def execution_quality(price, low, high, no_entry):
    if not price or not low or not high: return "未記錄"
    if low <= price <= high: return "合理區間成交"
    if price > high*1.02: return "追高執行"
    if price > high: return "買太高"
    if price < no_entry: return "跌破支撐仍進場"
    if price < low: return "低於建議區成交"
    return "未分類"


def manage_holding(t, df):
    if df is None or df.empty: return t
    curr=safe_float(df["Close"].iloc[-1]); entry=safe_float(t.get("price")); shares=safe_int(t.get("shares")); realized=safe_float(t.get("realized_pnl"))
    atr=safe_float(calc_atr(df).iloc[-1]); support=safe_float(t.get("support_price")) or entry; invalid=safe_float(t.get("invalid_price")) or support*0.985
    stop=safe_float(t.get("practical_stop")) or max(support*0.985, entry-atr*1.5 if atr else entry*0.95)
    ed=pd.to_datetime(t.get("date",today_str()), errors="coerce"); after=df[df.index>=ed] if pd.notna(ed) else df.tail(60)
    if after.empty: after=df.tail(60)
    high=max(safe_float(after["High"].max()),entry); low=min(safe_float(after["Low"].min()),entry)
    pnl=round2(pct(curr,entry)) if entry else 0; maxfav=round2(pct(high,entry)) if entry else 0; maxdd=round2(pct(low,entry)) if entry else 0; give=round2(max(maxfav-pnl,0))
    ctrail=round2(high-atr*2.0) if atr else 0; strail=round2(high-atr*2.5) if atr else 0; ltrail=round2(high-atr*3.0) if atr else 0
    trail_name="移動停利區" if strail>entry and curr>=entry+atr*1.5 else "移動停損區"
    standard_action="跌破標準移動停利，建議停利出場。" if trail_name=="移動停利區" else "跌破標準移動停損，建議停損出場。"
    egg=egg_position(curr, safe_float(df["Low"].rolling(120).min().iloc[-1]), safe_float(df["High"].rolling(120).max().iloc[-1]))
    start_low=min(safe_float(after["Low"].min()), support)
    t1=max(round2(support+(support-start_low)), round2(entry+atr*3) if atr else 0); t2=max(round2(support+(support-start_low)*1.618), round2(entry+atr*5) if atr else 0); t3=max(round2(support+(support-start_low)*2), round2(entry+atr*8) if atr else 0)
    progress=round2(curr/t1*100) if t1 else 0
    support_broken=curr<support*0.995
    stand_back=len(df)>=2 and safe_float(df["Close"].iloc[-2])<support*0.995 and curr>=support*1.003
    candle=candle_pattern(df)
    if curr<=stop: status,notice="實戰停損","已跌破實戰停損價，建議停損出場。"
    elif curr<=invalid: status,notice="假突破失效","已跌破假突破失效價，候選邏輯失效。"
    elif support_broken: status,notice="支撐失守","已跌破支撐點，建議先出場或減碼。"
    elif curr<=strail: status,notice="跌破標準風控",standard_action
    elif curr<=ctrail: status,notice="跌破保守風控","跌破保守風控，建議先減碼或提高警戒。"
    elif stand_back: status,notice="假跌破站回","昨日跌破支撐但今日站回，可能是假跌破轉強。"
    elif progress>=98 and candle.get("candle_score",0)<0: status,notice="接近滿足點需停利","接近第一滿足點且K棒轉弱，建議部分停利。"
    elif progress>=98: status,notice="接近第一滿足點","已接近第一波滿足點，續抱但提高警戒。"
    elif curr>=t2*0.98: status,notice="接近第二滿足點","接近第二滿足點，建議逐步鎖利。"
    elif give>=5 and maxfav>=8: status,notice="獲利回吐警戒","最大浮盈已有明顯回吐，建議檢查是否分批鎖利。"
    else: status,notice="續抱","尚未跌破AI風控區、支撐或停損，依策略續抱。"
    scale="尚未觸發分批出場"
    if curr<=ctrail: scale="建議先減碼 1/3"
    if curr<=strail: scale="建議再減碼 1/3 或停損"
    if curr<=stop: scale="建議全出"
    if give>=5 and maxfav>=8: scale="獲利回吐偏多，建議至少減碼"
    t.update({"curr":round2(curr),"pnl":pnl,"realized_pnl":round2(realized),"unrealized_pnl":round2((curr-entry)*shares) if shares else 0,"total_pnl":round2(realized+((curr-entry)*shares if shares else 0)),"highest_since_entry":round2(high),"lowest_since_entry":round2(low),"max_favorable_pct":maxfav,"max_drawdown_pct":maxdd,"profit_giveback_pct":give,"atr":round2(atr),"support_price":round2(support),"practical_stop":round2(stop),"invalid_price":round2(invalid),"conservative_trail":ctrail,"standard_trail":strail,"loose_trail":ltrail,"trail_range":f"{ltrail} ～ {ctrail}","trail_zone_name":trail_name,"conservative_action":"跌破保守風控，建議先減碼或提高警戒。","standard_action":standard_action,"loose_action":"跌破寬鬆風控，趨勢轉弱，建議全出。","scale_out_note":scale,"egg_zone_now":egg["egg_zone"],"egg_position_pct_now":egg["egg_position_pct"],"wave_start_price":round2(start_low),"wave_target_1":round2(t1),"wave_target_2":round2(t2),"wave_target_3":round2(t3),"progress_to_target_1":progress,"candle_signal_now":candle["candle_signal"],"ai_holding_status":status,"ai_exit_notice":notice})
    for k,v in {"shares":0,"note":"","trade_actions":[],"feedback_score":0,"feedback_notes":[],"weekly_trend":"-","daily_signal":"-","mtf_status":"-","execution_quality":"未記錄","entry_deviation_pct":0,"suggest_entry_low":0,"suggest_entry_high":0}.items():
        t.setdefault(k,v)
    return t


def failure_type(item):
    if safe_float(item.get("pnl")) >= 0:
        return "獲利但回吐偏多" if safe_float(item.get("profit_giveback_pct"))>=5 else "成功交易"
    if item.get("execution_quality") in ["追高執行","買太高"]: return "追高失敗"
    if item.get("execution_quality")=="跌破支撐仍進場": return "未照計畫進場失敗"
    if "假突破" in item.get("ai_holding_status",""): return "假突破失敗"
    if "支撐失守" in item.get("ai_holding_status",""): return "跌破支撐失敗"
    if "轉弱" in item.get("market_status","") or "盤整偏弱" in item.get("market_status",""): return "大盤轉弱失敗"
    if "龍頭偏弱" in item.get("leader_status",""): return "族群龍頭退潮失敗"
    if safe_float(item.get("risk_reward",0)) < MIN_RISK_REWARD_ENTRY: return "風報比不足失敗"
    if safe_float(item.get("max_favorable_pct")) > 5: return "獲利回吐轉虧"
    return "一般停損失敗"



def quick_rough_score(df):
    """
    全市場粗篩分數：
    只計算最必要的趨勢、量能、過熱與流動性，先把不適合的股票排除。
    詳細的 K棒、波浪、雞蛋理論、風報比，留到第二層細算處理。
    """
    if df is None or len(df) < 80:
        return None

    try:
        c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
        price = safe_float(c.iloc[-1])
        if not price:
            return None

        ma20 = safe_float(c.rolling(20).mean().iloc[-1])
        ma60 = safe_float(c.rolling(60).mean().iloc[-1])
        high60 = safe_float(h.rolling(60).max().iloc[-2]) if len(h) > 61 else safe_float(h.max())
        avg_vol20 = safe_float(v.rolling(20).mean().iloc[-1])
        avg_amt20 = safe_float((c * v).rolling(20).mean().iloc[-1])
        ch5 = pct(c.iloc[-1], c.iloc[-5]) if len(c) >= 5 else 0
        ch20 = pct(c.iloc[-1], c.iloc[-20]) if len(c) >= 20 else 0
        ch60 = pct(c.iloc[-1], c.iloc[-60]) if len(c) >= 60 else 0

        score = 0
        reasons = []

        if avg_vol20 >= MIN_AVG_VOLUME_20:
            score += 10
        else:
            score -= 20
            reasons.append("量能不足")

        if avg_amt20 >= MIN_AVG_AMOUNT_20:
            score += 10
        else:
            score -= 10
            reasons.append("成交金額不足")

        if ma20 and price > ma20:
            score += 15
            reasons.append("站上月線")
        else:
            score -= 20
            reasons.append("跌破月線")

        if ma60 and price > ma60:
            score += 12
            reasons.append("站上季線")

        if ma20 and ma60 and ma20 > ma60:
            score += 12
            reasons.append("月線大於季線")

        if ch20 > 5:
            score += 10
        if ch60 > 8:
            score += 10

        if high60 and price >= high60 * 0.96:
            score += 12
            reasons.append("接近60日前高")

        if ch5 > 22 or ch20 > 45:
            score -= 30
            reasons.append("短線過熱")

        return {
            "rough_score": round2(score),
            "rough_reasons": reasons,
            "rough_price": round2(price),
            "rough_avg_amount_20": round2(avg_amt20),
            "rough_ch20": round2(ch20),
            "rough_ch60": round2(ch60),
        }

    except Exception as e:
        print("quick_rough_score failed", e)
        return None


def scan_market():
    save_scan_status("running", "正在建立全市場股票池。")

    stocks = get_stock_pool()
    logs = load_trade_log()
    feedback = strategy_feedback(logs)
    optimize_strategy_weights()
    market = market_status()
    total = len(stocks)

    save_scan_status(
        "running",
        f"股票池建立完成：{total} 檔。省資源模式：{'啟用' if ENABLE_RESOURCE_SAVING_SCAN else '關閉'}。"
    )

    rough_rows = []

    # 第一層：全市場粗篩
    if ENABLE_RESOURCE_SAVING_SCAN:
        save_scan_status("running", f"第一層粗篩：開始掃描全市場 {total} 檔。")
        for i, (sym, info) in enumerate(stocks.items(), 1):
            try:
                name = info.get("name", sym)
                industry = info.get("industry", "其他")
                sector = infer_sector(sym, name, industry)
                df = download_stock(sym, "6mo")
                rough = quick_rough_score(df)

                if not rough:
                    continue

                row = {
                    "symbol": sym,
                    "name": name,
                    "industry": industry,
                    "sector": sector,
                }
                row.update(rough)

                if row.get("rough_score", 0) >= ROUGH_SCAN_MIN_SCORE:
                    rough_rows.append(row)

                if i % 100 == 0:
                    save_scan_status(
                        "running",
                        f"第一層粗篩中：{i}/{total}，目前通過粗篩 {len(rough_rows)} 檔。"
                    )

                time.sleep(ROUGH_ANALYSIS_SLEEP)

            except Exception as e:
                print("rough scan failed", sym, e)

        rough_rows = sorted(rough_rows, key=lambda x: x.get("rough_score", 0), reverse=True)
        detail_targets = rough_rows[:ROUGH_SCAN_TOP_N]

        save_scan_status(
            "running",
            f"第一層粗篩完成：通過 {len(rough_rows)} 檔，進入第二層細算 {len(detail_targets)} 檔。"
        )

    else:
        detail_targets = [
            {
                "symbol": sym,
                "name": info.get("name", sym),
                "industry": info.get("industry", "其他"),
                "sector": infer_sector(sym, info.get("name", sym), info.get("industry", "其他")),
                "rough_score": 0,
                "rough_reasons": [],
            }
            for sym, info in stocks.items()
        ]

    # 第二層：只針對粗篩後的股票做完整 AI 細算
    analyzed = []
    amap = {}
    detail_total = len(detail_targets)

    for i, info in enumerate(detail_targets, 1):
        sym = info.get("symbol")
        try:
            name = info.get("name", sym)
            industry = info.get("industry", "其他")
            sector = info.get("sector") or infer_sector(sym, name, industry)

            df = download_stock(sym, "1y")
            res = analyze_stock(df)

            if not res:
                continue

            item = {
                "symbol": sym,
                "name": name,
                "industry": industry,
                "sector": sector,
                "df": df,
                "rough_score": info.get("rough_score", 0),
                "rough_reasons": info.get("rough_reasons", []),
            }

            item.update(res)
            analyzed.append(item)
            amap[sym] = item

            if i % 50 == 0:
                save_scan_status("running", f"第二層細算中：{i}/{detail_total}")

            time.sleep(DETAILED_ANALYSIS_SLEEP)

        except Exception as e:
            print("detail scan failed", sym, e)

    ss = sector_scores(analyzed)
    ls = {sec: leader_strength(sec, amap) for sec in ss}
    ranks = sector_rankings(ss, ls)

    rank_map = {x["sector"]: x["rank"] for x in ranks}
    cs_map = {x["sector"]: x["combined_sector_score"] for x in ranks}

    for item in analyzed:
        item.update(
            ss.get(
                item["sector"],
                {
                    "sector_score": 0,
                    "sector_status": "弱勢族群",
                    "sector_avg_5d": 0,
                    "sector_avg_20d": 0,
                    "sector_avg_main": 0,
                    "sector_strong_ratio": 0,
                    "sector_stock_count": 0,
                },
            )
        )
        item.update(
            ls.get(
                item["sector"],
                {"leader_score": 0, "leader_status": "無明確龍頭資料", "leader_names": "-"},
            )
        )

        item["sector_rank"] = rank_map.get(item["sector"], 999)
        item["combined_sector_score"] = cs_map.get(item["sector"], item.get("sector_score", 0))
        item["market_status"] = market["market_status"]
        item["score"] = round2(
            item.get("technical_score", 0)
            + item.get("combined_sector_score", 0)
            + item.get("leader_score", 0) * 0.3
            + market["market_score"]
            + item.get("rough_score", 0) * 0.15
        )

    analyzed = add_sector_relative_rank(analyzed)

    final = []

    for item in analyzed:
        item["score"] = round2(item.get("score", 0) + item.get("sector_relative_score", 0))

        bt, es, er = entry_status(item)
        item["buy_type"] = bt
        item["entry_status"] = es
        item["entry_reason"] = er

        item.update(trade_plan(item))
        item.update(position_sizing(item, market))
        item.update(open_execution(item.get("df"), item))

        adaptive_score, adaptive_notes = adaptive_weight_score(item)
        item["adaptive_score"] = adaptive_score
        item["adaptive_notes"] = adaptive_notes

        item["score"] = round2(item.get("score", 0) + item.get("execution_score", 0) * 0.5 + adaptive_score)
        item["level"] = "S" if item["score"] >= 245 and item.get("main_score", 0) >= 50 and item.get("combined_sector_score", 0) >= 25 else "A"

        item = apply_feedback(item, feedback)
        level = classify(item)

        if not level:
            continue

        item["level"] = level
        item = apply_feedback(item, feedback)

        if not market["allow_new_positions"]:
            item["entry_status"] = "禁止新倉"
            item["entry_reason"] = market["risk_note"]
            item["ai_next_action"] = "大盤風險偏高，禁止新倉"
            item["execution_action"] = "禁止新倉"

        item.pop("df", None)
        final.append(item)

    final = sorted(
        final,
        key=lambda x: (
            x.get("level") == "S",
            x.get("execution_action") == "可試單",
            x.get("risk_reward", 0),
            x.get("feedback_score", 0),
            x.get("score", 0),
        ),
        reverse=True,
    )

    cand = update_candidate_pool(final)
    cand_list = list(cand.get("candidates", {}).values())[:MAX_CANDIDATE_DISPLAY]
    alerts = cand.get("entry_alerts", [])

    data = {
        "updated_at": taiwan_now(),
        **market,
        "resource_saving_scan": ENABLE_RESOURCE_SAVING_SCAN,
        "rough_scan_total": len(rough_rows) if ENABLE_RESOURCE_SAVING_SCAN else total,
        "detailed_scan_total": detail_total,
        "stock_pool_count": total,
        "s_count": len([x for x in final if x.get("level") == "S"]),
        "a_count": len([x for x in final if x.get("level") == "A"]),
        "sector_rankings": ranks,
        "candidate_count": len(cand_list),
        "candidate_pool": cand_list,
        "entry_alerts": alerts,
        "strategy_feedback": feedback,
    }

    save_scan_results(data)

    save_scan_status(
        "done",
        f"掃描完成：全市場 {total} 檔，粗篩 {data['rough_scan_total']} 檔，細算 {detail_total} 檔，"
        f"S級 {data['s_count']} 檔，A級候選 {data['a_count']} 檔，進場提醒 {len(alerts)} 檔。"
    )

@app.route("/")
def index():
    scan=load_scan_results(); status=load_scan_status(); tracks=load_track(); logs=load_trade_log(); updated=[]
    for t in tracks:
        updated.append(manage_holding(t, download_stock(t["symbol"],"1y")))
    save_track(updated); dash=strategy_dashboard(logs); feedback=scan.get("strategy_feedback") or strategy_feedback(logs)
    return render_template("index.html", now=taiwan_now(), twii=get_index_price("^TWII"), otc=get_index_price("^TWOII"), market_status=scan.get("market_status","尚未掃描"), market_score=scan.get("market_score",0), risk_mode=scan.get("risk_mode","-"), risk_switch=scan.get("risk_switch","-"), allow_new_positions=scan.get("allow_new_positions",False), risk_note=scan.get("risk_note","-"), risk_multiplier=scan.get("risk_multiplier",0), market_egg_zone=scan.get("market_egg_zone","-"), market_pressure_note=scan.get("market_pressure_note","-"), scan_updated_at=scan.get("updated_at","尚未掃描"), stock_pool_count=scan.get("stock_pool_count",0), s_count=scan.get("s_count",0), a_count=scan.get("a_count",0), candidate_count=scan.get("candidate_count",0), sector_rankings=scan.get("sector_rankings",[]), candidate_pool=scan.get("candidate_pool",[]), entry_alerts=scan.get("entry_alerts",[]), scan_status=status.get("status","idle"), scan_message=status.get("message","尚未掃描"), scan_status_time=status.get("updated_at","-"), tracks=updated, trade_logs=logs[-15:], strategy_dashboard=dash, strategy_feedback=feedback, account_size=ACCOUNT_SIZE, risk_per_trade=round2(RISK_PER_TRADE*100), line_token_ready=bool(get_line_token()), line_user_ready=bool(get_line_user_id()), line_enabled=line_enabled(), line_user_id=get_line_user_id())


@app.route("/scan-now")
def scan_now():
    global is_scanning
    if is_scanning: return redirect(url_for("index"))
    def run():
        global is_scanning
        try:
            is_scanning=True; scan_market()
        except Exception as e:
            save_scan_status("error", f"掃描失敗：{e}"); print("掃描失敗",e)
        finally: is_scanning=False
    threading.Thread(target=run,daemon=True).start(); return redirect(url_for("index"))


def find_candidate(symbol): return read_json(CANDIDATE_FILE,{"candidates":{}}).get("candidates",{}).get(symbol)


@app.route("/track/<symbol>", methods=["GET","POST"])
def track(symbol):
    item=find_candidate(symbol)
    if not item: return redirect(url_for("index"))
    tracks=load_track()
    if any(x["symbol"]==symbol for x in tracks): return redirect(url_for("index"))
    actual=request.form.get("actual_price") if request.method=="POST" else None; shares=request.form.get("shares") if request.method=="POST" else None; note=request.form.get("note") if request.method=="POST" else ""
    price=safe_float(actual,0) or safe_float(item.get("next_entry_low")) or safe_float(item.get("price")); qty=safe_int(shares,0)
    eq=execution_quality(price,safe_float(item.get("next_entry_low")),safe_float(item.get("next_entry_high")),safe_float(item.get("no_entry_price")))
    tracks.append({"symbol":symbol,"name":item.get("name",symbol),"level":item.get("level","-"),"sector":item.get("sector","-"),"buy_type":item.get("buy_type","-"),"price":price,"entry_price":price,"shares":qty,"realized_pnl":0,"trade_actions":[{"type":"初始追蹤","price":price,"shares":qty,"note":note or "加入追蹤","date":taiwan_now()}],"note":note or "","support_price":safe_float(item.get("support_price")),"no_entry_price":safe_float(item.get("no_entry_price")),"invalid_price":safe_float(item.get("invalid_price")),"practical_stop":safe_float(item.get("practical_stop")),"initial_stop":safe_float(item.get("practical_stop")),"risk_reward":safe_float(item.get("risk_reward")),"risk_reward_group":item.get("risk_reward_group",risk_reward_group(item.get("risk_reward",0))),"sector_status":item.get("sector_status","-"),"leader_status":item.get("leader_status","-"),"market_status":item.get("market_status","-"),"weekly_trend":item.get("weekly_trend","-"),"daily_signal":item.get("daily_signal","-"),"mtf_status":item.get("mtf_status","-"),"execution_quality":eq,"entry_deviation_pct":round2(pct(price,safe_float(item.get("next_entry_high")))) if item.get("next_entry_high") else 0,"suggest_entry_low":safe_float(item.get("next_entry_low")),"suggest_entry_high":safe_float(item.get("next_entry_high")),"feedback_score":item.get("feedback_score",0),"feedback_notes":item.get("feedback_notes",[]),"date":today_str(),"ai_holding_status":"剛加入追蹤","ai_exit_notice":"等待隔日開盤與支撐確認。","highest_since_entry":price,"lowest_since_entry":price,"max_favorable_pct":0,"max_drawdown_pct":0,"profit_giveback_pct":0,"trail_range":"-","trail_zone_name":"AI移動風控區","wave_target_1":0,"wave_target_2":0,"wave_target_3":0})
    save_track(tracks); return redirect(url_for("index"))


@app.route("/update-track/<symbol>", methods=["POST"])
def update_track(symbol):
    data=load_track()
    for t in data:
        if t["symbol"]==symbol:
            price=safe_float(request.form.get("price"),safe_float(t.get("price"))); shares=safe_int(request.form.get("shares"),safe_int(t.get("shares"))); stop=safe_float(request.form.get("practical_stop"),safe_float(t.get("practical_stop"))); note=request.form.get("note",t.get("note",""))
            t.update({"price":price,"entry_price":price,"shares":shares,"practical_stop":stop,"initial_stop":stop,"note":note,"execution_quality":execution_quality(price,safe_float(t.get("suggest_entry_low")),safe_float(t.get("suggest_entry_high")),safe_float(t.get("no_entry_price")))})
            t.setdefault("trade_actions",[]).append({"type":"修改資料","price":price,"shares":shares,"note":note or "手動修改","date":taiwan_now()}); break
    save_track(data); return redirect(url_for("index"))


@app.route("/add-position/<symbol>", methods=["POST"])
def add_position(symbol):
    data=load_track(); price=safe_float(request.form.get("add_price")); shares=safe_int(request.form.get("add_shares")); note=request.form.get("add_note","")
    if price<=0 or shares<=0: return redirect(url_for("index"))
    for t in data:
        if t["symbol"]==symbol:
            oldp=safe_float(t.get("price")); oldq=safe_int(t.get("shares")); totalq=oldq+shares; avg=round2((oldp*oldq+price*shares)/totalq) if totalq else oldp
            t.update({"price":avg,"entry_price":avg,"shares":totalq}); t.setdefault("trade_actions",[]).append({"type":"加碼","price":price,"shares":shares,"note":note or "手動加碼","date":taiwan_now()}); break
    save_track(data); return redirect(url_for("index"))


@app.route("/reduce-position/<symbol>", methods=["POST"])
def reduce_position(symbol):
    data=load_track(); price=safe_float(request.form.get("reduce_price")); shares=safe_int(request.form.get("reduce_shares")); note=request.form.get("reduce_note","")
    if price<=0 or shares<=0: return redirect(url_for("index"))
    for t in data:
        if t["symbol"]==symbol:
            oldp=safe_float(t.get("price")); oldq=safe_int(t.get("shares")); sell=min(shares,oldq); realized=round2((price-oldp)*sell)
            t["shares"]=max(oldq-sell,0); t["realized_pnl"]=round2(safe_float(t.get("realized_pnl"))+realized); t.setdefault("trade_actions",[]).append({"type":"減碼","price":price,"shares":sell,"realized_pnl":realized,"note":note or "手動減碼","date":taiwan_now()}); break
    save_track(data); return redirect(url_for("index"))


@app.route("/untrack/<symbol>")
def untrack(symbol):
    save_track([x for x in load_track() if x["symbol"] != symbol]); return redirect(url_for("index"))


@app.route("/close-trade/<symbol>")
def close_trade(symbol):
    tracks=load_track(); logs=load_trade_log(); item=next((x for x in tracks if x["symbol"]==symbol),None)
    if not item: return redirect(url_for("index"))
    logs.append({"symbol":item.get("symbol"),"name":item.get("name"),"entry_price":item.get("price"),"exit_price":item.get("curr"),"shares":item.get("shares",0),"pnl_pct":item.get("pnl"),"realized_pnl":item.get("realized_pnl",0),"unrealized_pnl":item.get("unrealized_pnl",0),"total_pnl":item.get("total_pnl",0),"max_favorable_pct":item.get("max_favorable_pct",0),"max_drawdown_pct":item.get("max_drawdown_pct",0),"profit_giveback_pct":item.get("profit_giveback_pct",0),"execution_quality":item.get("execution_quality","未記錄"),"failure_type":failure_type(item),"entry_date":item.get("date"),"exit_date":today_str(),"level":item.get("level"),"buy_type":item.get("buy_type"),"sector":item.get("sector"),"risk_reward":item.get("risk_reward",0),"risk_reward_group":item.get("risk_reward_group",risk_reward_group(item.get("risk_reward",0))),"market_status":item.get("market_status","未記錄"),"leader_status":item.get("leader_status","未記錄"),"ai_holding_status":item.get("ai_holding_status"),"ai_exit_notice":item.get("ai_exit_notice")})
    save_trade_log(logs); save_track([x for x in tracks if x["symbol"] != symbol]); return redirect(url_for("index"))


def scheduled_scan():
    global is_scanning

    now = datetime.now(TZ)

    # 省資源排程版：只在週一～週五執行全市場掃描
    # weekday(): 0=星期一, 6=星期日
    if now.weekday() >= 5:
        save_scan_status("skip", "今天是假日，省資源模式略過全市場掃描。")
        return

    if is_scanning:
        return

    is_scanning = True

    try:
        scan_market()
        evaluate_signal_database()
        optimize_strategy_weights()
    except Exception as e:
        save_scan_status("error", f"排程掃描失敗：{e}")
        print("排程掃描失敗", e)
    finally:
        is_scanning = False

scheduler = BackgroundScheduler(timezone=TZ)

# =====================================================
# 省資源排程版
# =====================================================
# 週一～週五 16:05：只做一次全市場掃描
# 週六、週日不掃描，避免 Railway 額度浪費。
scheduler.add_job(
    scheduled_scan,
    trigger="cron",
    day_of_week="mon-fri",
    hour=16,
    minute=5,
    id="daily_market_scan",
    replace_existing=True
)

# 週一～週五 09:10：LINE 持股風控提醒
# 不掃全市場，只檢查已追蹤持股。
scheduler.add_job(
    lambda: send_line_notification("open"),
    trigger="cron",
    day_of_week="mon-fri",
    hour=9,
    minute=10,
    id="line_open_watch_0910",
    replace_existing=True
)

# 週一～週五 13:20：LINE 可進場試單提醒
# 不掃全市場，只讀取候選池 / 進場提醒。
scheduler.add_job(
    lambda: send_line_notification("preclose"),
    trigger="cron",
    day_of_week="mon-fri",
    hour=13,
    minute=20,
    id="line_preclose_1320",
    replace_existing=True
)

scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
