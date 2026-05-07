import os
import json
import time
import math
import threading
import requests
import pandas as pd
import yfinance as yf

from io import StringIO
from flask import Flask, render_template, redirect, url_for, request, Response
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# ======================
# 登入保護
# ======================
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


# ======================
# 基本設定
# ======================
TAIWAN_TZ = ZoneInfo("Asia/Taipei")

RESULT_FILE = "scan_results.json"
TRACK_FILE = "track.json"
TRADE_LOG_FILE = "trade_log.json"
STOCK_POOL_FILE = "stock_pool.json"
SCAN_STATUS_FILE = "scan_status.json"
INST_FILE = "institutional_cache.json"
CANDIDATE_FILE = "candidate_pool.json"

FULL_MARKET_MIN_COUNT = 1700
PARTIAL_MARKET_MIN_COUNT = 1000

MAX_ELITE_RESULTS = 3
MAX_S_RESULTS = 10
MAX_A_RESULTS = 10
MAX_SECTOR_RANK = 10
MAX_CANDIDATE_DISPLAY = 20
MAX_ALERT_DISPLAY = 10
MAX_INVALID_DISPLAY = 10

ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "1000000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))

FEE_RATE = 0.001425 * 0.28
TAX_RATE = 0.003
SLIPPAGE_RATE = 0.001

is_scanning = False


# ======================
# 基礎工具
# ======================
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


def safe_float(x):
    try:
        if hasattr(x, "iloc"):
            x = x.iloc[0]
        return float(x)
    except Exception:
        return None


# ======================
# 掃描狀態
# ======================
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


# ======================
# 保底股票池
# ======================
def get_fallback_stock_pool():
    base = {
        "2330.TW": ("台積電", "半導體"),
        "2303.TW": ("聯電", "半導體"),
        "5347.TWO": ("世界", "半導體"),
        "6770.TW": ("力積電", "半導體"),
        "2454.TW": ("聯發科", "IC設計"),
        "3034.TW": ("聯詠", "IC設計"),
        "2379.TW": ("瑞昱", "IC設計"),
        "3661.TW": ("世芯-KY", "IC設計"),
        "3443.TW": ("創意", "IC設計"),
        "3529.TWO": ("力旺", "IC設計"),
        "4966.TWO": ("譜瑞-KY", "IC設計"),
        "5274.TWO": ("信驊", "IC設計"),
        "3711.TW": ("日月光投控", "封測"),
        "6147.TWO": ("頎邦", "封測"),
        "2449.TW": ("京元電子", "封測"),
        "6488.TWO": ("環球晶", "半導體材料"),
        "5483.TWO": ("中美晶", "半導體材料"),
        "2317.TW": ("鴻海", "AI伺服器"),
        "2382.TW": ("廣達", "AI伺服器"),
        "3231.TW": ("緯創", "AI伺服器"),
        "2356.TW": ("英業達", "AI伺服器"),
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


# ======================
# 股票池
# ======================
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

        return market

    except Exception as e:
        print("TWSE OpenAPI 失敗：", e)
        return {}


def parse_tpex_item(item):
    code_keys = [
        "公司代號", "股票代號", "有價證券代號", "證券代號",
        "SecuritiesCompanyCode", "CompanyCode", "Code", "stock_id", "stk_code"
    ]

    name_keys = [
        "公司簡稱", "公司名稱", "股票名稱", "有價證券名稱", "證券簡稱",
        "CompanyName", "Name", "stock_name", "stk_name"
    ]

    industry_keys = [
        "產業別", "產業類別", "IndustryCode", "Industry", "industry"
    ]

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
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

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

        return market

    except Exception as e:
        print(f"ISIN mode={mode} 失敗：", e)
        return {}


def fetch_isin_all_stock_pool():
    listed = fetch_isin_by_mode(2, ".TW", "上市")
    otc = fetch_isin_by_mode(4, ".TWO", "上櫃")

    market = {}
    market.update(listed)
    market.update(otc)

    return market


def get_stock_pool():
    source_log = []

    cache, cache_meta = load_stock_pool_cache()
    cache_count = len(cache) if cache else 0

    if cache_count:
        source_log.append(f"快取：{cache_count}檔")

    market = {}

    twse = fetch_twse_openapi_stock_pool()
    source_log.append(f"TWSE上市：{len(twse)}檔")
    market.update(twse)

    tpex = fetch_tpex_openapi_stock_pool()
    source_log.append(f"TPEx上櫃：{len(tpex)}檔")
    market.update(tpex)

    combined_count = len(market)
    source_log.append(f"OpenAPI合計：{combined_count}檔")

    if combined_count < FULL_MARKET_MIN_COUNT:
        isin_all = fetch_isin_all_stock_pool()
        source_log.append(f"ISIN全市場：{len(isin_all)}檔")

        if len(isin_all) > len(market):
            market = isin_all

    current_count = len(market)

    if current_count < FULL_MARKET_MIN_COUNT and cache and cache_count >= FULL_MARKET_MIN_COUNT:
        note = "；".join(source_log) + f"；目前來源不足，改用完整快取 {cache_count} 檔"
        save_scan_status("running", note)
        return cache

    if current_count >= FULL_MARKET_MIN_COUNT:
        note = "；".join(source_log) + f"；採用完整股票池 {current_count} 檔"
        save_stock_pool(market, note)
        save_scan_status("running", note)
        return market

    if cache and cache_count > current_count:
        note = "；".join(source_log) + f"；目前僅 {current_count} 檔，改用較完整快取 {cache_count} 檔"
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


# ======================
# 股價資料
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

    except Exception as e:
        print("下載失敗：", symbol, e)
        return None


# ======================
# 大盤風險開關
# ======================
def get_index():
    def fetch(symbol):
        df = download_stock(symbol, "5d")
        if df is None or df.empty:
            return "-"
        price = safe_float(df["Close"].iloc[-1])
        return round(price, 2) if price else "-"

    return fetch("^TWII"), fetch("^TWOII")


def analyze_index(symbol):
    df = download_stock(symbol, "1y")

    if df is None or len(df) < 120:
        return {
            "ok": False,
            "price": None,
            "above_ma20": False,
            "above_ma60": False,
            "ma20_gt_ma60": False,
            "trend_score": 0
        }

    close = df["Close"]
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()

    last = close.iloc[-1]

    trend_score = 0

    if last > ma20.iloc[-1]:
        trend_score += 10

    if last > ma60.iloc[-1]:
        trend_score += 10

    if ma20.iloc[-1] > ma60.iloc[-1]:
        trend_score += 10

    if ma20.iloc[-1] > ma60.iloc[-1] > ma120.iloc[-1]:
        trend_score += 20

    return {
        "ok": True,
        "price": round(float(last), 2),
        "above_ma20": bool(last > ma20.iloc[-1]),
        "above_ma60": bool(last > ma60.iloc[-1]),
        "ma20_gt_ma60": bool(ma20.iloc[-1] > ma60.iloc[-1]),
        "trend_score": trend_score
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
            "risk_note": "大盤資料不足，暫不建議積極建立新倉。"
        }

    if twii["above_ma20"] and twii["above_ma60"] and twii["ma20_gt_ma60"] and otc.get("above_ma20"):
        return {
            "market_status": "強多市場",
            "market_score": 25,
            "risk_mode": "積極",
            "risk_switch": "允許新倉",
            "allow_new_positions": True,
            "risk_multiplier": 1.0,
            "risk_note": "加權與櫃買結構偏多，可允許 S/A 級進入今日精選，部位維持正常。"
        }

    if twii["above_ma60"] and twii["ma20_gt_ma60"]:
        return {
            "market_status": "多頭市場",
            "market_score": 15,
            "risk_mode": "正常",
            "risk_switch": "允許新倉",
            "allow_new_positions": True,
            "risk_multiplier": 0.8,
            "risk_note": "大盤中期偏多，但仍需避開過熱與高量轉弱標的，部位略降。"
        }

    if not twii["above_ma20"] and not otc.get("above_ma20", False):
        return {
            "market_status": "轉弱市場",
            "market_score": -25,
            "risk_mode": "防守",
            "risk_switch": "禁止新倉",
            "allow_new_positions": False,
            "risk_multiplier": 0,
            "risk_note": "加權與櫃買皆弱於月線，系統禁止新倉，只保留追蹤與觀察。"
        }

    if not twii["above_ma20"]:
        return {
            "market_status": "盤整偏弱",
            "market_score": -10,
            "risk_mode": "保守",
            "risk_switch": "只允許S級",
            "allow_new_positions": True,
            "risk_multiplier": 0.3,
            "risk_note": "大盤低於月線，僅允許最強 S 級，並大幅降低部位。"
        }

    return {
        "market_status": "盤整市場",
        "market_score": -5,
        "risk_mode": "保守",
        "risk_switch": "減碼觀察",
        "allow_new_positions": True,
        "risk_multiplier": 0.5,
        "risk_note": "大盤盤整，今日精選偏向低風險、不追高、等待回採確認的標的，部位減半。"
    }


# ======================
# 法人籌碼
# ======================
def load_institutional_cache():
    data = read_json_file(INST_FILE, None)

    if data and data.get("date") == today_str():
        return data.get("stocks", {})

    return None


def save_institutional_cache(stocks):
    write_json_file(INST_FILE, {
        "date": today_str(),
        "updated_at": taiwan_now(),
        "stocks": stocks
    })


def fetch_institutional_data():
    cache = load_institutional_cache()

    if cache:
        return cache

    stocks = {}

    try:
        end = datetime.now(TAIWAN_TZ).date()
        start = end - timedelta(days=14)

        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d")
        }

        res = requests.get(url, params=params, timeout=30)
        res.raise_for_status()
        data = res.json()
        rows = data.get("data", [])

        for r in rows:
            stock_id = str(r.get("stock_id", "")).strip()
            investor = str(r.get("name", "") or r.get("institutional_investors", "")).strip()

            buy = safe_float(r.get("buy", 0)) or 0
            sell = safe_float(r.get("sell", 0)) or 0
            net = buy - sell

            if len(stock_id) != 4 or not stock_id.isdigit():
                continue

            item = stocks.setdefault(stock_id, {
                "foreign_net": 0,
                "trust_net": 0,
                "dealer_net": 0,
                "total_net": 0,
                "foreign_days": 0,
                "trust_days": 0,
                "dealer_days": 0
            })

            item["total_net"] += net

            if "Foreign" in investor or "外資" in investor:
                item["foreign_net"] += net
                if net > 0:
                    item["foreign_days"] += 1

            elif "Investment_Trust" in investor or "投信" in investor:
                item["trust_net"] += net
                if net > 0:
                    item["trust_days"] += 1

            elif "Dealer" in investor or "自營" in investor:
                item["dealer_net"] += net
                if net > 0:
                    item["dealer_days"] += 1

        save_institutional_cache(stocks)
        return stocks

    except Exception as e:
        print("法人資料取得失敗：", e)
        save_institutional_cache({})
        return {}


def calc_institutional_score(symbol, inst_data):
    code = symbol.split(".")[0]
    data = inst_data.get(code)

    if not data:
        return {
            "inst_score": 0,
            "inst_signals": ["法人資料暫無"],
            "foreign_days": 0,
            "trust_days": 0,
            "dealer_days": 0,
            "total_net": 0
        }

    score = 0
    signals = []

    foreign_days = data.get("foreign_days", 0)
    trust_days = data.get("trust_days", 0)
    dealer_days = data.get("dealer_days", 0)

    foreign_net = data.get("foreign_net", 0)
    trust_net = data.get("trust_net", 0)
    total_net = data.get("total_net", 0)

    if foreign_days >= 3:
        score += 15
        signals.append("外資連買")

    if trust_days >= 3:
        score += 30
        signals.append("投信連買")

    if trust_days >= 5:
        score += 15
        signals.append("投信連買5日以上")

    if dealer_days >= 3:
        score += 10
        signals.append("自營商偏多")

    if total_net > 0:
        score += 15
        signals.append("三大法人合計買超")

    if foreign_net > 0 and trust_net > 0:
        score += 20
        signals.append("外資投信同步買超")

    if trust_net > 0 and trust_days >= 2:
        score += 10
        signals.append("投信買盤延續")

    if total_net < 0:
        score -= 20
        signals.append("法人合計賣超")

    return {
        "inst_score": score,
        "inst_signals": signals,
        "foreign_days": foreign_days,
        "trust_days": trust_days,
        "dealer_days": dealer_days,
        "total_net": round(total_net, 0)
    }


# ======================
# ATR / 主力資金
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


def calc_main_force(df):
    close = df["Close"]
    open_ = df["Open"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    money = close * volume

    ma_money_5 = money.rolling(5).mean()
    ma_money_20 = money.rolling(20).mean()

    up_day = close > open_
    strong_up = (close > open_) & ((close - open_) / open_ * 100 > 2)
    near_high = ((high - close) / (high - low + 0.0001)) < 0.25

    main_buy_days = ((up_day) & (money > ma_money_20 * 1.3)).tail(10).sum()
    strong_buy_days = ((strong_up) & (near_high) & (money > ma_money_20 * 1.5)).tail(10).sum()

    money_ratio = safe_float(ma_money_5.iloc[-1] / ma_money_20.iloc[-1])

    if not money_ratio:
        money_ratio = 0

    close_5 = close.tail(5)
    volume_5 = volume.tail(5)

    price_up = close_5.iloc[-1] > close_5.iloc[0]
    volume_up = volume_5.iloc[-1] > volume_5.mean()

    main_score = 0
    main_signals = []

    if money_ratio > 1.1:
        main_score += 10
        main_signals.append("資金微幅增溫")

    if money_ratio > 1.2:
        main_score += 15
        main_signals.append("資金增溫")

    if money_ratio > 1.6:
        main_score += 25
        main_signals.append("資金明顯放大")

    if main_buy_days >= 2:
        main_score += 15
        main_signals.append("疑似主力承接")

    if main_buy_days >= 3:
        main_score += 20
        main_signals.append("疑似主力連續承接")

    if strong_buy_days >= 1:
        main_score += 15
        main_signals.append("強勢買盤出現")

    if strong_buy_days >= 2:
        main_score += 25
        main_signals.append("強勢買盤進場")

    if price_up and volume_up:
        main_score += 15
        main_signals.append("價漲量增")

    if close.iloc[-1] < close.iloc[-2] and volume.iloc[-1] > volume.rolling(20).mean().iloc[-1] * 1.5:
        main_score -= 20
        main_signals.append("高量下跌警訊")

    return {
        "main_score": round(main_score, 1),
        "main_signals": main_signals,
        "money_ratio": round(float(money_ratio), 2),
        "main_buy_days": int(main_buy_days),
        "strong_buy_days": int(strong_buy_days)
    }


# ======================
# 雞蛋理論 / K棒 / 突破回採 / 波段
# ======================
def analyze_egg_position(price, low_60, high_60):
    if high_60 <= low_60:
        return {
            "egg_zone": "無法判斷",
            "egg_score": 0,
            "egg_position_pct": 0,
            "egg_note": "區間資料不足。"
        }

    pct = (price - low_60) / (high_60 - low_60) * 100

    if pct <= 35:
        return {
            "egg_zone": "蛋黃區",
            "egg_score": 25,
            "egg_position_pct": round(pct, 2),
            "egg_note": "低位區，適合觀察低位啟動與突破回採。"
        }

    if pct <= 70:
        return {
            "egg_zone": "蛋白區",
            "egg_score": 15,
            "egg_position_pct": round(pct, 2),
            "egg_note": "中位區，適合波段主升與拉回不破。"
        }

    if pct <= 90:
        return {
            "egg_zone": "蛋殼區",
            "egg_score": -5,
            "egg_position_pct": round(pct, 2),
            "egg_note": "高位區，追高風險提高，需等回採。"
        }

    return {
        "egg_zone": "蛋殼過熱區",
        "egg_score": -25,
        "egg_position_pct": round(pct, 2),
        "egg_note": "高位過熱，容易震盪或假突破，不建議追高。"
    }


def analyze_candle_pattern(df):
    if df is None or len(df) < 5:
        return {
            "candle_signal": "資料不足",
            "candle_score": 0,
            "candle_note": "K棒資料不足。"
        }

    o = safe_float(df["Open"].iloc[-1])
    h = safe_float(df["High"].iloc[-1])
    l = safe_float(df["Low"].iloc[-1])
    c = safe_float(df["Close"].iloc[-1])

    po = safe_float(df["Open"].iloc[-2])
    pc = safe_float(df["Close"].iloc[-2])

    if not all([o, h, l, c, po, pc]):
        return {
            "candle_signal": "資料不足",
            "candle_score": 0,
            "candle_note": "K棒資料不足。"
        }

    body = abs(c - o)
    rng = max(h - l, 0.0001)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l

    is_red = c > o
    is_black = c < o
    is_big_red = is_red and body / rng >= 0.55 and (c - o) / o * 100 >= 2
    is_big_black = is_black and body / rng >= 0.55 and (o - c) / o * 100 >= 2
    is_long_upper = upper_shadow / rng >= 0.45
    is_long_lower = lower_shadow / rng >= 0.45
    is_engulf_red = is_red and pc < po and c > po and o < pc
    is_engulf_black = is_black and pc > po and c < po and o > pc

    if is_engulf_red:
        return {
            "candle_signal": "紅K吞噬",
            "candle_score": 25,
            "candle_note": "轉強K棒，代表買盤反攻。"
        }

    if is_big_red and c >= h - rng * 0.25:
        return {
            "candle_signal": "帶量長紅K",
            "candle_score": 20,
            "candle_note": "收盤靠近高點，短線買盤積極。"
        }

    if is_long_lower and is_red:
        return {
            "candle_signal": "下影支撐紅K",
            "candle_score": 18,
            "candle_note": "盤中殺低後拉回，支撐買盤出現。"
        }

    if is_long_lower:
        return {
            "candle_signal": "下影支撐K",
            "candle_score": 10,
            "candle_note": "支撐區有承接，但需隔日轉強確認。"
        }

    if is_engulf_black:
        return {
            "candle_signal": "黑K吞噬",
            "candle_score": -25,
            "candle_note": "轉弱K棒，需小心假突破。"
        }

    if is_big_black:
        return {
            "candle_signal": "長黑K",
            "candle_score": -25,
            "candle_note": "賣壓明顯，短線不宜進場。"
        }

    if is_long_upper:
        return {
            "candle_signal": "長上影K",
            "candle_score": -15,
            "candle_note": "上方賣壓明顯，容易形成假突破。"
        }

    return {
        "candle_signal": "中性K",
        "candle_score": 0,
        "candle_note": "K棒沒有明顯轉強或轉弱訊號。"
    }


def analyze_breakout_pullback(df):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    if len(df) < 80:
        return {
            "prev_high_20": 0,
            "prev_high_60": 0,
            "breakout_level": 0,
            "breakout_state": "資料不足",
            "breakout_score": 0,
            "pullback_low": 0,
            "breakout_note": "資料不足。"
        }

    prev_high_20 = safe_float(high.rolling(20).max().iloc[-2])
    prev_high_60 = safe_float(high.rolling(60).max().iloc[-2])
    price = safe_float(close.iloc[-1])

    breakout_level = max(prev_high_20 or 0, prev_high_60 or 0)

    if breakout_level <= 0 or not price:
        return {
            "prev_high_20": 0,
            "prev_high_60": 0,
            "breakout_level": 0,
            "breakout_state": "資料不足",
            "breakout_score": 0,
            "pullback_low": 0,
            "breakout_note": "前高資料不足。"
        }

    recent = df.tail(8)
    recent_close = recent["Close"]
    recent_low = recent["Low"]

    broke_days = recent_close[recent_close > breakout_level * 1.003]

    has_breakout = len(broke_days) > 0
    has_pullback = bool((recent_low <= breakout_level * 1.015).any())
    pullback_not_broken = bool((recent_close >= breakout_level * 0.995).tail(3).any())
    fake_breakout = has_breakout and price < breakout_level * 0.995
    pullback_low = safe_float(recent_low.min()) or 0

    if fake_breakout:
        return {
            "prev_high_20": round(prev_high_20, 2),
            "prev_high_60": round(prev_high_60, 2),
            "breakout_level": round(breakout_level, 2),
            "breakout_state": "假突破取消",
            "breakout_score": -35,
            "pullback_low": round(pullback_low, 2),
            "breakout_note": "突破後又跌回前高下方，視為假突破。"
        }

    if has_breakout and has_pullback and pullback_not_broken and price > breakout_level:
        return {
            "prev_high_20": round(prev_high_20, 2),
            "prev_high_60": round(prev_high_60, 2),
            "breakout_level": round(breakout_level, 2),
            "breakout_state": "突破回採不破",
            "breakout_score": 35,
            "pullback_low": round(pullback_low, 2),
            "breakout_note": "已突破前高，且回採前高附近不破，符合壓力轉支撐。"
        }

    if has_breakout:
        return {
            "prev_high_20": round(prev_high_20, 2),
            "prev_high_60": round(prev_high_60, 2),
            "breakout_level": round(breakout_level, 2),
            "breakout_state": "突破完成等回採",
            "breakout_score": 12,
            "pullback_low": round(pullback_low, 2),
            "breakout_note": "已突破前高，但尚未完成回採確認，不建議追高。"
        }

    if price >= breakout_level * 0.97:
        return {
            "prev_high_20": round(prev_high_20, 2),
            "prev_high_60": round(prev_high_60, 2),
            "breakout_level": round(breakout_level, 2),
            "breakout_state": "接近前高壓力",
            "breakout_score": 5,
            "pullback_low": round(pullback_low, 2),
            "breakout_note": "接近前高壓力區，等待突破後回採確認。"
        }

    return {
        "prev_high_20": round(prev_high_20, 2),
        "prev_high_60": round(prev_high_60, 2),
        "breakout_level": round(breakout_level, 2),
        "breakout_state": "尚未突破",
        "breakout_score": 0,
        "pullback_low": round(pullback_low, 2),
        "breakout_note": "尚未突破前高，仍屬觀察階段。"
    }


def analyze_wave_stage(item):
    egg_zone = item.get("egg_zone", "")
    breakout_state = item.get("breakout_state", "")
    candle_score = item.get("candle_score", 0)
    ma20_distance = item.get("ma20_distance", 0)
    change_20d = item.get("change_20d", 0)
    warnings = item.get("warnings", [])

    if "跌破月線" in warnings or "高量下跌警訊" in item.get("main_signals", []):
        return {
            "wave_stage": "修正段",
            "wave_score": -25,
            "wave_note": "跌破月線或出現高量下跌，波段進入修正。"
        }

    if breakout_state == "突破回採不破":
        return {
            "wave_stage": "疑似第三段主升起點",
            "wave_score": 30,
            "wave_note": "突破前高後回採不破，具備主升段啟動條件。"
        }

    if breakout_state == "突破完成等回採":
        return {
            "wave_stage": "第一段突破後等待第二段回採",
            "wave_score": 10,
            "wave_note": "已突破但尚未回採確認，不宜追高。"
        }

    if egg_zone in ["蛋黃區", "蛋白區"] and candle_score > 0 and change_20d > 5:
        return {
            "wave_stage": "初升段",
            "wave_score": 15,
            "wave_note": "位階仍不算高，且K棒轉強，疑似初升段。"
        }

    if egg_zone in ["蛋殼區", "蛋殼過熱區"] or ma20_distance > 12:
        return {
            "wave_stage": "末升或高位震盪",
            "wave_score": -15,
            "wave_note": "位階偏高或距離月線偏遠，追高風險增加。"
        }

    return {
        "wave_stage": "整理觀察段",
        "wave_score": 0,
        "wave_note": "尚未出現明確主升或修正訊號。"
    }


# ======================
# 個股分析
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

    ma20_distance = (price - ma20.iloc[-1]) / ma20.iloc[-1] * 100
    ma60_distance = (price - ma60.iloc[-1]) / ma60.iloc[-1] * 100

    is_break_20 = price > high_20
    is_break_60 = price > high_60
    is_above_ma20 = price > ma20.iloc[-1]
    is_above_ma60 = price > ma60.iloc[-1]
    is_ma_bull = ma20.iloc[-1] > ma60.iloc[-1] > ma120.iloc[-1]
    is_near_ma20 = -2 <= ma20_distance <= 6
    is_near_ma60 = -2 <= ma60_distance <= 8
    is_low_start_zone = 5 <= ((price - low_60) / low_60 * 100) <= 45
    is_volume_warm = vma5.iloc[-1] > vma20.iloc[-1] * 1.2
    is_volume_strong = vma5.iloc[-1] > vma20.iloc[-1] * 1.6

    signals = []
    warnings = []
    score = 0

    if is_above_ma20:
        signals.append("站上月線")
        score += 10

    if is_above_ma60:
        signals.append("站上季線")
        score += 10

    if ma20.iloc[-1] > ma60.iloc[-1]:
        signals.append("月線大於季線")
        score += 10

    if is_ma_bull:
        signals.append("中長期多頭排列")
        score += 25

    spread = abs(ma20.iloc[-1] - ma60.iloc[-1]) / ma60.iloc[-1]

    if spread < 0.06 and is_above_ma20:
        signals.append("均線收斂後轉強")
        score += 15

    if is_break_20:
        signals.append("突破20日高點")
        score += 15

    if is_break_60:
        signals.append("突破60日高點")
        score += 20

    if is_volume_warm:
        signals.append("量能增溫")
        score += 15

    if is_volume_strong:
        signals.append("主力放量")
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

    if is_low_start_zone:
        signals.append("低中位啟動區")
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

    if ma60_distance > 25:
        warnings.append("距離季線過遠")
        score -= 20

    if price < ma20.iloc[-1]:
        warnings.append("跌破月線")
        score -= 25

    atr_pct = 0

    if atr_now and price:
        atr_pct = atr_now / price * 100

    if atr_pct > 10:
        warnings.append("波動過大")
        score -= 15

    main_force = calc_main_force(df)
    egg = analyze_egg_position(price, safe_float(low_60), safe_float(high_60))
    candle = analyze_candle_pattern(df)
    breakout = analyze_breakout_pullback(df)

    score += egg["egg_score"]
    score += candle["candle_score"]
    score += breakout["breakout_score"]

    stop_loss = None
    take_profit_1 = None
    take_profit_2 = None

    if atr_now:
        stop_loss = round(price - atr_now * 2, 2)
        take_profit_1 = round(price + atr_now * 3, 2)
        take_profit_2 = round(price + atr_now * 5, 2)

    result = {
        "price": round(price, 2),
        "technical_score": round(score, 1),
        "main_score": main_force["main_score"],
        "main_signals": main_force["main_signals"],
        "money_ratio": main_force["money_ratio"],
        "main_buy_days": main_force["main_buy_days"],
        "strong_buy_days": main_force["strong_buy_days"],
        "change_5d": round(float(change_5d), 2),
        "change_20d": round(float(change_20d), 2),
        "change_60d": round(float(change_60d), 2),
        "ma20_distance": round(float(ma20_distance), 2),
        "ma60_distance": round(float(ma60_distance), 2),
        "is_break_20": bool(is_break_20),
        "is_break_60": bool(is_break_60),
        "is_above_ma20": bool(is_above_ma20),
        "is_above_ma60": bool(is_above_ma60),
        "is_ma_bull": bool(is_ma_bull),
        "is_near_ma20": bool(is_near_ma20),
        "is_near_ma60": bool(is_near_ma60),
        "is_low_start_zone": bool(is_low_start_zone),
        "is_volume_warm": bool(is_volume_warm),
        "is_volume_strong": bool(is_volume_strong),
        "signals": signals,
        "warnings": warnings,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "atr_pct": round(float(atr_pct), 2),
        "atr": round(float(atr_now), 2) if atr_now else 0,
        "ma5": round(float(ma5.iloc[-1]), 2),
        "ma20": round(float(ma20.iloc[-1]), 2),
        "ma60": round(float(ma60.iloc[-1]), 2),
        "latest_high": round(float(high.iloc[-1]), 2),
        "latest_low": round(float(low.iloc[-1]), 2),
    }

    result.update(egg)
    result.update(candle)
    result.update(breakout)

    wave = analyze_wave_stage(result)
    result.update(wave)
    result["technical_score"] = round(result["technical_score"] + wave["wave_score"], 1)

    return result


# ======================
# 族群強度
# ======================
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

    for sector, keywords in groups.items():
        for k in keywords:
            if k in name:
                return sector

    return "其他"


def calc_sector_scores(items):
    sector_map = {}

    for x in items:
        sector = x["sector"]
        sector_map.setdefault(sector, []).append(x)

    sector_scores = {}

    for sector, arr in sector_map.items():
        if not arr:
            continue

        avg_5d = sum(x["change_5d"] for x in arr) / len(arr)
        avg_20d = sum(x["change_20d"] for x in arr) / len(arr)
        avg_main = sum(x["main_score"] for x in arr) / len(arr)
        avg_inst = sum(x["inst_score"] for x in arr) / len(arr)
        strong_count = len([x for x in arr if x["technical_score"] >= 60])
        strong_ratio = strong_count / len(arr)

        score = 0

        if avg_5d > 2:
            score += 10

        if avg_5d > 5:
            score += 10

        if avg_20d > 5:
            score += 10

        if avg_20d > 12:
            score += 10

        if strong_ratio >= 0.25:
            score += 10

        if strong_ratio >= 0.4:
            score += 15

        if avg_main >= 35:
            score += 10

        if avg_inst >= 15:
            score += 10

        sector_scores[sector] = {
            "sector": sector,
            "sector_score": score,
            "sector_avg_5d": round(avg_5d, 2),
            "sector_avg_20d": round(avg_20d, 2),
            "sector_avg_main": round(avg_main, 2),
            "sector_avg_inst": round(avg_inst, 2),
            "sector_strong_ratio": round(strong_ratio * 100, 1),
            "sector_stock_count": len(arr)
        }

    return sector_scores


def build_sector_rankings(sector_scores):
    rows = list(sector_scores.values())
    rows = sorted(rows, key=lambda x: x["sector_score"], reverse=True)

    ranked = []

    for i, row in enumerate(rows[:MAX_SECTOR_RANK], start=1):
        copied = dict(row)
        copied["rank"] = i
        ranked.append(copied)

    return ranked


# ======================
# 進場狀態與交易計畫
# ======================
def determine_buy_type_and_entry_status(item):
    warnings = item.get("warnings", [])
    main_signals = item.get("main_signals", [])
    breakout_state = item.get("breakout_state", "")
    score = item.get("score", 0)

    if "跌破月線" in warnings:
        return {
            "buy_type": "弱勢取消型",
            "entry_status": "跌破取消",
            "entry_reason": "股價已跌破月線，短線結構轉弱，不建議新進。"
        }

    if breakout_state == "假突破取消":
        return {
            "buy_type": "假突破型",
            "entry_status": "跌破取消",
            "entry_reason": "突破後跌回前高下方，視為假突破，取消候選。"
        }

    is_hot = (
        "距離月線過遠" in warnings or
        "距離季線過遠" in warnings or
        "5日漲幅過熱" in warnings or
        "20日漲幅過熱" in warnings or
        "高量下跌警訊" in main_signals or
        item.get("egg_zone") == "蛋殼過熱區" or
        item.get("candle_score", 0) < -20
    )

    if is_hot:
        return {
            "buy_type": "過熱觀察型",
            "entry_status": "過熱不追",
            "entry_reason": "分數雖高，但距離均線過遠、位階過熱或K棒轉弱，不建議追高。"
        }

    if breakout_state == "突破回採不破":
        if item.get("candle_score", 0) > 0 or item.get("main_score", 0) >= 40:
            return {
                "buy_type": "突破回採型",
                "entry_status": "可觀察進場",
                "entry_reason": "已突破前高且回採不破，並出現K棒或資金轉強，屬高品質進場型態。"
            }

        return {
            "buy_type": "突破回採型",
            "entry_status": "等轉強K",
            "entry_reason": "已回採前高不破，但仍需紅K、過高或量能增溫確認。"
        }

    if breakout_state == "突破完成等回採":
        return {
            "buy_type": "突破型",
            "entry_status": "等回採",
            "entry_reason": "已突破前高，但不建議追高，等待回採前高不破。"
        }

    if item.get("is_low_start_zone") and item.get("is_volume_warm") and item.get("main_score", 0) >= 35:
        return {
            "buy_type": "低位啟動型",
            "entry_status": "等突破",
            "entry_reason": "位階仍不高且量能轉強，等待突破前高後回採確認。"
        }

    if item.get("is_near_ma20") and item.get("is_above_ma60") and item.get("technical_score", 0) >= 50:
        return {
            "buy_type": "拉回型",
            "entry_status": "可觀察進場",
            "entry_reason": "股價接近月線且仍在季線之上，屬拉回不破的觀察買點。"
        }

    if item.get("is_ma_bull") and item.get("main_score", 0) >= 60 and item.get("money_ratio", 0) >= 1.2:
        if item.get("ma20_distance", 99) <= 10:
            return {
                "buy_type": "強勢續攻型",
                "entry_status": "可觀察進場",
                "entry_reason": "中長期多頭排列且主力資金仍強，屬強勢續攻型。"
            }

        return {
            "buy_type": "強勢續攻型",
            "entry_status": "等拉回",
            "entry_reason": "趨勢強但距離月線偏遠，等回測月線或量縮整理較安全。"
        }

    if score >= 170:
        return {
            "buy_type": "趨勢觀察型",
            "entry_status": "等突破",
            "entry_reason": "綜合條件不差，但尚未出現突破回採或明確拉回確認。"
        }

    return {
        "buy_type": "觀察型",
        "entry_status": "僅列觀察",
        "entry_reason": "條件有部分轉強，但尚未達明確進場型態。"
    }


def calc_trade_plan(item):
    price = item.get("price", 0)
    atr = item.get("atr", 0)
    breakout_level = item.get("breakout_level", 0)
    pullback_low = item.get("pullback_low", 0)
    latest_high = item.get("latest_high", 0)
    ma20 = item.get("ma20", 0)
    buy_type = item.get("buy_type", "")
    entry_status = item.get("entry_status", "")

    if not price or not atr:
        return {
            "entry_price": 0,
            "confirm_price": 0,
            "initial_stop": 0,
            "trailing_stop": 0,
            "target_price": 0,
            "risk_reward": 0,
            "entry_check_score": 0,
            "entry_triggered": False,
            "trade_plan_note": "價格或ATR資料不足，無法建立交易計畫。"
        }

    if buy_type == "突破回採型":
        entry_price = round(max(latest_high, breakout_level * 1.003), 2)
        confirm_price = round(entry_price * 1.005, 2)
        initial_stop = round(
            min(
                breakout_level * 0.99,
                pullback_low * 0.99 if pullback_low else breakout_level * 0.99
            ),
            2
        )
        trailing_stop = round(price - atr * 2.5, 2)
        target_price = round(entry_price + atr * 3.5, 2)
        note = "突破前高後回採不破，進場點以再突破回採後高點為主。"

    elif buy_type == "低位啟動型":
        entry_price = round(breakout_level * 1.003, 2)
        confirm_price = round(breakout_level * 1.01, 2)
        initial_stop = round(max(ma20 * 0.98, entry_price - atr * 2), 2)
        trailing_stop = round(price - atr * 2.5, 2)
        target_price = round(entry_price + atr * 3, 2)
        note = "低位啟動不追，等待突破前高後再觀察。"

    elif buy_type == "拉回型":
        entry_price = round(max(price * 1.005, latest_high), 2)
        confirm_price = round(entry_price * 1.005, 2)
        initial_stop = round(min(ma20 * 0.98, entry_price - atr * 1.5), 2)
        trailing_stop = round(price - atr * 2, 2)
        target_price = round(entry_price + atr * 2.5, 2)
        note = "拉回型重點在支撐不破後重新轉強，不追高。"

    elif buy_type == "強勢續攻型":
        entry_price = round(max(price * 1.003, latest_high), 2)
        confirm_price = round(entry_price * 1.005, 2)
        initial_stop = round(max(ma20 * 0.98, entry_price - atr * 2), 2)
        trailing_stop = round(price - atr * 2.5, 2)
        target_price = round(entry_price + atr * 3, 2)
        note = "強勢續攻型需控管距離均線過遠風險。"

    elif buy_type == "突破型":
        entry_price = round(breakout_level * 1.003, 2)
        confirm_price = round(breakout_level * 1.01, 2)
        initial_stop = round(max(ma20 * 0.98, entry_price - atr * 2), 2)
        trailing_stop = round(price - atr * 2.5, 2)
        target_price = round(entry_price + atr * 3, 2)
        note = "突破不立即追，優先等回採支撐不破。"

    else:
        entry_price = round(max(price * 1.005, latest_high), 2)
        confirm_price = round(entry_price * 1.005, 2)
        initial_stop = round(price - atr * 2, 2)
        trailing_stop = round(price - atr * 2.5, 2)
        target_price = round(entry_price + atr * 2.5, 2)
        note = "觀察型標的，僅作為候選，不建議主動進場。"

    risk = max(entry_price - initial_stop, 0.01)
    reward = max(target_price - entry_price, 0.01)
    risk_reward = round(reward / risk, 2)

    entry_check_score = 0

    if entry_status == "可觀察進場":
        entry_check_score += 30

    if item.get("breakout_state") == "突破回採不破":
        entry_check_score += 25

    if item.get("candle_score", 0) > 0:
        entry_check_score += 15

    if item.get("wave_score", 0) > 0:
        entry_check_score += 15

    if item.get("egg_zone") in ["蛋黃區", "蛋白區"]:
        entry_check_score += 10

    if risk_reward >= 2:
        entry_check_score += 10

    if item.get("main_score", 0) >= 50:
        entry_check_score += 10

    if item.get("entry_status") in ["過熱不追", "跌破取消", "禁止新倉"]:
        entry_check_score = max(entry_check_score - 40, 0)

    entry_triggered = (
        entry_status == "可觀察進場"
        and price >= entry_price
        and risk_reward >= 1.5
        and entry_check_score >= 70
    )

    return {
        "entry_price": round(entry_price, 2),
        "confirm_price": round(confirm_price, 2),
        "initial_stop": round(initial_stop, 2),
        "trailing_stop": round(trailing_stop, 2),
        "target_price": round(target_price, 2),
        "risk_reward": risk_reward,
        "entry_check_score": entry_check_score,
        "entry_triggered": entry_triggered,
        "trade_plan_note": note
    }


def calc_position_sizing(item, market_info):
    price = item.get("entry_price") or item.get("price")
    stop_loss = item.get("initial_stop") or item.get("stop_loss")

    risk_multiplier = market_info.get("risk_multiplier", 0)
    adjusted_risk_pct = RISK_PER_TRADE * risk_multiplier
    adjusted_risk_amount = ACCOUNT_SIZE * adjusted_risk_pct

    if not price or not stop_loss or price <= stop_loss or adjusted_risk_amount <= 0:
        return {
            "risk_amount": round(ACCOUNT_SIZE * RISK_PER_TRADE, 0),
            "adjusted_risk_amount": round(adjusted_risk_amount, 0),
            "risk_multiplier": risk_multiplier,
            "adjusted_risk_pct": round(adjusted_risk_pct * 100, 2),
            "risk_per_share": 0,
            "suggest_shares": 0,
            "suggest_lots": 0,
            "position_value": 0,
            "position_note": "市場風險偏高或停損價資料不足，暫不建議建立部位。"
        }

    risk_per_share = price - stop_loss
    suggest_shares = math.floor(adjusted_risk_amount / risk_per_share)
    suggest_lots = math.floor(suggest_shares / 1000)
    position_value = suggest_shares * price

    if suggest_lots <= 0:
        note = "停損距離較大或股價較高，依市場調整後風險限制不足一張。"
    elif suggest_lots >= 5:
        note = "建議部位偏大，仍應分批建立，不宜一次滿倉。"
    else:
        note = "已依大盤狀態調整單筆風險後估算部位。"

    return {
        "risk_amount": round(ACCOUNT_SIZE * RISK_PER_TRADE, 0),
        "adjusted_risk_amount": round(adjusted_risk_amount, 0),
        "risk_multiplier": risk_multiplier,
        "adjusted_risk_pct": round(adjusted_risk_pct * 100, 2),
        "risk_per_share": round(risk_per_share, 2),
        "suggest_shares": suggest_shares,
        "suggest_lots": suggest_lots,
        "position_value": round(position_value, 0),
        "position_note": note
    }


# ======================
# 突破回採策略本體回測
# ======================
def breakout_pullback_strategy_backtest(df):
    if df is None or len(df) < 160:
        return {
            "bt_count": 0,
            "bt_winrate": 0,
            "bt_avg_return": 0,
            "bt_expectancy": 0,
            "bt_avg_win": 0,
            "bt_avg_loss": 0,
            "bt_max_drawdown": 0,
            "bt_profit_factor": 0,
            "bt_note": "資料不足。"
        }

    trades = []
    equity = 100.0
    equity_curve = [equity]

    for i in range(80, len(df) - 25):
        past = df.iloc[:i].copy()

        high_20 = safe_float(past["High"].rolling(20).max().iloc[-1])
        high_60 = safe_float(past["High"].rolling(60).max().iloc[-1])

        if not high_20 or not high_60:
            continue

        breakout_level = max(high_20, high_60)

        close_i = safe_float(df["Close"].iloc[i])
        if not close_i:
            continue

        # 第一階段：突破前高
        if close_i <= breakout_level * 1.003:
            continue

        pullback_confirm_idx = None
        pullback_low = None

        # 第二階段：8日內回採前高附近且不破
        for j in range(i + 1, min(i + 9, len(df) - 10)):
            low_j = safe_float(df["Low"].iloc[j])
            close_j = safe_float(df["Close"].iloc[j])
            open_j = safe_float(df["Open"].iloc[j])
            high_j = safe_float(df["High"].iloc[j])
            high_prev = safe_float(df["High"].iloc[j - 1])

            if not all([low_j, close_j, open_j, high_j, high_prev]):
                continue

            if close_j < breakout_level * 0.995:
                pullback_confirm_idx = None
                break

            touched_support = low_j <= breakout_level * 1.015
            turn_strong = close_j > open_j or close_j > high_prev

            if touched_support and close_j >= breakout_level * 0.995 and turn_strong:
                pullback_confirm_idx = j
                pullback_low = low_j
                break

        if pullback_confirm_idx is None:
            continue

        entry_idx = pullback_confirm_idx + 1
        if entry_idx >= len(df):
            continue

        entry_open = safe_float(df["Open"].iloc[entry_idx])
        if not entry_open:
            continue

        atr_series = calc_atr(df.iloc[:entry_idx])
        atr_now = safe_float(atr_series.iloc[-1])

        if not atr_now:
            continue

        entry_price = entry_open * (1 + SLIPPAGE_RATE)
        initial_stop = min(breakout_level * 0.99, pullback_low * 0.99)
        highest_after_entry = entry_price
        exit_price = safe_float(df["Close"].iloc[min(entry_idx + 20, len(df) - 1)])

        for k in range(entry_idx, min(entry_idx + 25, len(df))):
            day_high = safe_float(df["High"].iloc[k])
            day_low = safe_float(df["Low"].iloc[k])
            day_close = safe_float(df["Close"].iloc[k])

            if not all([day_high, day_low, day_close]):
                continue

            highest_after_entry = max(highest_after_entry, day_high)
            dynamic_trailing_stop = highest_after_entry - atr_now * 2.5

            if day_low <= initial_stop:
                exit_price = initial_stop * (1 - SLIPPAGE_RATE)
                break

            if day_low <= dynamic_trailing_stop and day_close > entry_price:
                exit_price = dynamic_trailing_stop * (1 - SLIPPAGE_RATE)
                break

            ma5 = safe_float(df["Close"].iloc[:k + 1].rolling(5).mean().iloc[-1])
            if k > entry_idx + 5 and ma5 and day_close < ma5:
                exit_price = day_close * (1 - SLIPPAGE_RATE)
                break

        gross_return = (exit_price - entry_price) / entry_price * 100
        cost = (FEE_RATE * 2 + TAX_RATE) * 100
        net_return = gross_return - cost

        trades.append(net_return)
        equity *= (1 + net_return / 100)
        equity_curve.append(equity)

    if not trades:
        return {
            "bt_count": 0,
            "bt_winrate": 0,
            "bt_avg_return": 0,
            "bt_expectancy": 0,
            "bt_avg_win": 0,
            "bt_avg_loss": 0,
            "bt_max_drawdown": 0,
            "bt_profit_factor": 0,
            "bt_note": "近一年未出現足夠的突破回採交易樣本。"
        }

    wins = [x for x in trades if x > 0]
    losses = [x for x in trades if x <= 0]

    winrate = len(wins) / len(trades) * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    avg_return = sum(trades) / len(trades)

    expectancy = (winrate / 100 * avg_win) - ((100 - winrate) / 100 * avg_loss)

    total_win = sum(wins) if wins else 0
    total_loss = abs(sum(losses)) if losses else 0
    profit_factor = total_win / total_loss if total_loss else 0

    peak = equity_curve[0]
    max_dd = 0

    for value in equity_curve:
        if value > peak:
            peak = value

        dd = (value - peak) / peak * 100

        if dd < max_dd:
            max_dd = dd

    return {
        "bt_count": len(trades),
        "bt_winrate": round(winrate, 2),
        "bt_avg_return": round(avg_return, 2),
        "bt_expectancy": round(expectancy, 2),
        "bt_avg_win": round(avg_win, 2),
        "bt_avg_loss": round(avg_loss, 2),
        "bt_max_drawdown": round(max_dd, 2),
        "bt_profit_factor": round(profit_factor, 2),
        "bt_note": "此回測使用突破前高、回採不破、轉強後隔日進場的策略本體。"
    }


# ======================
# 分級，只保留 S / A
# ======================
def classify_stock(item):
    total_score = item["score"]
    main_score = item["main_score"]
    inst_score = item["inst_score"]
    sector_score = item["sector_score"]
    money_ratio = item["money_ratio"]

    warnings = item.get("warnings", [])
    main_signals = item.get("main_signals", [])

    is_invalid = (
        "跌破月線" in warnings or
        "高量下跌警訊" in main_signals or
        item.get("breakout_state") == "假突破取消" or
        item.get("entry_status") in ["跌破取消", "過熱不追"] or
        item.get("egg_zone") == "蛋殼過熱區" or
        item.get("candle_score", 0) <= -20
    )

    if is_invalid:
        return None

    if (
        total_score >= 220 and
        main_score >= 60 and
        inst_score >= 20 and
        sector_score >= 20 and
        money_ratio >= 1.2 and
        item.get("entry_check_score", 0) >= 60
    ):
        return "S"

    if (
        total_score >= 175 and
        main_score >= 35 and
        sector_score >= 10 and
        money_ratio >= 1.1
    ):
        return "A"

    return None


def build_elite_results(s_results, a_results, market_info):
    if not market_info.get("allow_new_positions"):
        return []

    pool = []

    for item in s_results:
        copied = dict(item)
        copied["elite_reason"] = "S級優先，已通過分數、資金、法人、族群、K棒、位階與風控篩選。"
        pool.append(copied)

    if market_info.get("risk_switch") != "只允許S級":
        for item in a_results:
            copied = dict(item)
            copied["elite_reason"] = "A級補選，適合等待突破回採、拉回或轉強確認。"
            pool.append(copied)

    def elite_score(x):
        bonus = 0

        if x.get("entry_status") == "可觀察進場":
            bonus += 20
        elif x.get("entry_status") in ["等回採", "等突破", "等轉強K"]:
            bonus += 8

        if x.get("breakout_state") == "突破回採不破":
            bonus += 20

        if x.get("candle_score", 0) > 0:
            bonus += 10

        if x.get("wave_score", 0) > 0:
            bonus += 10

        if x.get("egg_zone") in ["蛋黃區", "蛋白區"]:
            bonus += 8

        if x.get("bt_expectancy", 0) > 0:
            bonus += 10

        if x.get("bt_winrate", 0) >= 50:
            bonus += 8

        if x.get("trust_days", 0) >= 3:
            bonus += 8

        if x.get("sector_rank", 999) <= 5:
            bonus += 10

        if x.get("risk_reward", 0) >= 2:
            bonus += 10

        if x.get("entry_triggered"):
            bonus += 15

        return x.get("score", 0) + bonus

    sorted_pool = sorted(pool, key=elite_score, reverse=True)

    filtered = []
    sector_count = {}

    for x in sorted_pool:
        if x.get("entry_status") in ["過熱不追", "跌破取消", "禁止新倉"]:
            continue

        if "高量下跌警訊" in x.get("main_signals", []):
            continue

        if x.get("candle_score", 0) <= -20:
            continue

        sector = x.get("sector", "其他")
        current_sector_count = sector_count.get(sector, 0)

        # 同族群集中風險限制：今日精選最多 3 檔，同族群最多 2 檔
        if current_sector_count >= 2:
            continue

        filtered.append(x)
        sector_count[sector] = current_sector_count + 1

        if len(filtered) >= MAX_ELITE_RESULTS:
            break

    return filtered


# ======================
# 候選追蹤池
# ======================
def load_candidate_pool():
    return read_json_file(CANDIDATE_FILE, {
        "updated_at": "-",
        "candidates": {},
        "entry_alerts": [],
        "invalid_alerts": []
    })


def save_candidate_pool(data):
    write_json_file(CANDIDATE_FILE, data)


def candidate_status_is_watchable(status):
    return status in ["等突破", "等回採", "等轉強K", "可觀察進場", "等拉回"]


def candidate_status_is_invalid(status):
    return status in ["跌破取消", "過熱不追", "禁止新倉"]


def update_candidate_pool(all_ranked_items):
    old_data = load_candidate_pool()
    old_candidates = old_data.get("candidates", {})

    now = taiwan_now()
    today = today_str()

    new_candidates = {}
    entry_alerts = []
    invalid_alerts = []

    current_map = {x["symbol"]: x for x in all_ranked_items}

    # 先處理今日符合候選資格的股票
    for item in all_ranked_items:
        symbol = item["symbol"]
        status = item.get("entry_status", "-")

        if not candidate_status_is_watchable(status):
            continue

        old = old_candidates.get(symbol, {})
        previous_status = old.get("current_status", "-")

        first_seen = old.get("first_seen", today)
        highest_score = max(old.get("highest_score", 0), item.get("score", 0))

        is_entry_alert = (
            previous_status in ["等突破", "等回採", "等轉強K", "等拉回", "僅列觀察"]
            and status == "可觀察進場"
        ) or item.get("entry_triggered", False)

        candidate = {
            "symbol": symbol,
            "name": item.get("name", ""),
            "sector": item.get("sector", "-"),
            "level": item.get("level", "-"),
            "first_seen": first_seen,
            "last_seen": today,
            "previous_status": previous_status,
            "current_status": status,
            "buy_type": item.get("buy_type", "-"),
            "score": item.get("score", 0),
            "highest_score": highest_score,
            "entry_price": item.get("entry_price", 0),
            "confirm_price": item.get("confirm_price", 0),
            "initial_stop": item.get("initial_stop", 0),
            "trailing_stop": item.get("trailing_stop", 0),
            "risk_reward": item.get("risk_reward", 0),
            "entry_check_score": item.get("entry_check_score", 0),
            "entry_triggered": item.get("entry_triggered", False),
            "breakout_state": item.get("breakout_state", "-"),
            "egg_zone": item.get("egg_zone", "-"),
            "wave_stage": item.get("wave_stage", "-"),
            "candle_signal": item.get("candle_signal", "-"),
            "reason": item.get("entry_reason", "-"),
            "updated_at": now
        }

        new_candidates[symbol] = candidate

        if is_entry_alert:
            alert = dict(candidate)
            alert["alert_type"] = "今日進場提醒"
            alert["alert_note"] = "候選股狀態已轉為可觀察進場，請人工確認量價與支撐後再決策。"
            entry_alerts.append(alert)

    # 再處理舊候選失效
    for symbol, old in old_candidates.items():
        current_item = current_map.get(symbol)

        if not current_item:
            invalid = dict(old)
            invalid["current_status"] = "候選失效"
            invalid["alert_type"] = "候選失效"
            invalid["alert_note"] = "今日未再符合 S/A 或候選條件，暫停觀察。"
            invalid["updated_at"] = now
            invalid_alerts.append(invalid)
            continue

        status = current_item.get("entry_status", "-")
        if candidate_status_is_invalid(status):
            invalid = dict(old)
            invalid["current_status"] = status
            invalid["alert_type"] = "候選失效"
            invalid["alert_note"] = current_item.get("entry_reason", "條件轉弱，候選失效。")
            invalid["updated_at"] = now
            invalid_alerts.append(invalid)

    # 候選池排序
    sorted_candidates = dict(
        sorted(
            new_candidates.items(),
            key=lambda kv: (
                1 if kv[1].get("current_status") == "可觀察進場" else 0,
                kv[1].get("score", 0),
                kv[1].get("entry_check_score", 0)
            ),
            reverse=True
        )
    )

    entry_alerts = sorted(
        entry_alerts,
        key=lambda x: (x.get("score", 0), x.get("entry_check_score", 0)),
        reverse=True
    )[:MAX_ALERT_DISPLAY]

    invalid_alerts = invalid_alerts[:MAX_INVALID_DISPLAY]

    data = {
        "updated_at": now,
        "candidates": sorted_candidates,
        "entry_alerts": entry_alerts,
        "invalid_alerts": invalid_alerts
    }

    save_candidate_pool(data)
    return data


# ======================
# 交易紀錄
# ======================
def load_track():
    return read_json_file(TRACK_FILE, [])


def save_track(data):
    write_json_file(TRACK_FILE, data)


def load_trade_log():
    return read_json_file(TRADE_LOG_FILE, [])


def save_trade_log(data):
    write_json_file(TRADE_LOG_FILE, data)


def calc_track_stats(tracks):
    valid = [t for t in tracks if t.get("pnl") != "-"]

    if not valid:
        return 0, 0

    wins = [t for t in valid if t["pnl"] > 0]
    avg = sum(t["pnl"] for t in valid) / len(valid)

    return round(len(wins) / len(valid) * 100, 2), round(avg, 2)


def calc_trade_log_stats(logs):
    closed = [x for x in logs if x.get("pnl_pct") is not None]

    if not closed:
        return {
            "trade_count": 0,
            "trade_winrate": 0,
            "trade_avg_return": 0,
            "best_buy_type": "-",
            "best_level": "-"
        }

    wins = [x for x in closed if x["pnl_pct"] > 0]
    avg = sum(x["pnl_pct"] for x in closed) / len(closed)

    by_type = {}
    by_level = {}

    for x in closed:
        buy_type = x.get("buy_type", "-")
        level = x.get("level", "-")

        by_type.setdefault(buy_type, []).append(x["pnl_pct"])
        by_level.setdefault(level, []).append(x["pnl_pct"])

    best_buy_type = "-"
    best_buy_avg = -999

    for k, vals in by_type.items():
        v = sum(vals) / len(vals)

        if v > best_buy_avg:
            best_buy_avg = v
            best_buy_type = k

    best_level = "-"
    best_level_avg = -999

    for k, vals in by_level.items():
        v = sum(vals) / len(vals)

        if v > best_level_avg:
            best_level_avg = v
            best_level = k

    return {
        "trade_count": len(closed),
        "trade_winrate": round(len(wins) / len(closed) * 100, 2),
        "trade_avg_return": round(avg, 2),
        "best_buy_type": best_buy_type,
        "best_level": best_level
    }


# ======================
# 掃描結果
# ======================
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
        "a_results": [],
        "sector_rankings": [],
        "candidate_count": 0,
        "entry_alerts": [],
        "invalid_alerts": [],
        "candidate_pool": []
    })


# ======================
# 全市場掃描
# ======================
def scan_market():
    save_scan_status("running", "正在建立全市場股票池，請稍後重新整理。")
    print("開始掃描：", taiwan_now())

    stocks = get_stock_pool()
    inst_data = fetch_institutional_data()
    market_info = get_market_status()

    market_status = market_info["market_status"]
    market_score = market_info["market_score"]
    risk_mode = market_info["risk_mode"]

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

            inst = calc_institutional_score(symbol, inst_data)

            item = {
                "symbol": symbol,
                "name": name,
                "industry": industry,
                "sector": sector,
                "df": df,

                "price": result["price"],
                "technical_score": result["technical_score"],
                "main_score": result["main_score"],
                "inst_score": inst["inst_score"],

                "main_signals": result["main_signals"],
                "inst_signals": inst["inst_signals"],

                "money_ratio": result["money_ratio"],
                "main_buy_days": result["main_buy_days"],
                "strong_buy_days": result["strong_buy_days"],

                "foreign_days": inst["foreign_days"],
                "trust_days": inst["trust_days"],
                "dealer_days": inst["dealer_days"],
                "total_net": inst["total_net"],

                "change_5d": result["change_5d"],
                "change_20d": result["change_20d"],
                "change_60d": result["change_60d"],
                "ma20_distance": result["ma20_distance"],
                "ma60_distance": result["ma60_distance"],

                "is_break_20": result["is_break_20"],
                "is_break_60": result["is_break_60"],
                "is_above_ma20": result["is_above_ma20"],
                "is_above_ma60": result["is_above_ma60"],
                "is_ma_bull": result["is_ma_bull"],
                "is_near_ma20": result["is_near_ma20"],
                "is_near_ma60": result["is_near_ma60"],
                "is_low_start_zone": result["is_low_start_zone"],
                "is_volume_warm": result["is_volume_warm"],
                "is_volume_strong": result["is_volume_strong"],

                "signals": result["signals"],
                "warnings": result["warnings"],

                "stop_loss": result["stop_loss"],
                "take_profit_1": result["take_profit_1"],
                "take_profit_2": result["take_profit_2"],
                "atr_pct": result["atr_pct"],
                "atr": result["atr"],
                "ma5": result["ma5"],
                "ma20": result["ma20"],
                "ma60": result["ma60"],
                "latest_high": result["latest_high"],
                "latest_low": result["latest_low"],

                "egg_zone": result["egg_zone"],
                "egg_score": result["egg_score"],
                "egg_position_pct": result["egg_position_pct"],
                "egg_note": result["egg_note"],

                "candle_signal": result["candle_signal"],
                "candle_score": result["candle_score"],
                "candle_note": result["candle_note"],

                "prev_high_20": result["prev_high_20"],
                "prev_high_60": result["prev_high_60"],
                "breakout_level": result["breakout_level"],
                "breakout_state": result["breakout_state"],
                "breakout_score": result["breakout_score"],
                "pullback_low": result["pullback_low"],
                "breakout_note": result["breakout_note"],

                "wave_stage": result["wave_stage"],
                "wave_score": result["wave_score"],
                "wave_note": result["wave_note"],

                "market_status": market_status,
                "risk_mode": risk_mode
            }

            analyzed.append(item)

            if i % 100 == 0:
                save_scan_status("running", f"正在掃描全市場：{i}/{total}")

            time.sleep(0.03)

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
            "sector_avg_inst": 0,
            "sector_strong_ratio": 0,
            "sector_stock_count": 0
        })

        item["sector_score"] = sector_data["sector_score"]
        item["sector_avg_5d"] = sector_data["sector_avg_5d"]
        item["sector_avg_20d"] = sector_data["sector_avg_20d"]
        item["sector_avg_main"] = sector_data["sector_avg_main"]
        item["sector_avg_inst"] = sector_data["sector_avg_inst"]
        item["sector_strong_ratio"] = sector_data["sector_strong_ratio"]
        item["sector_stock_count"] = sector_data["sector_stock_count"]
        item["sector_rank"] = sector_rank_map.get(item["sector"], 999)

        item["score"] = round(
            item["technical_score"] +
            item["main_score"] +
            item["inst_score"] +
            item["sector_score"] +
            market_score,
            1
        )

        item.update(determine_buy_type_and_entry_status(item))
        item.update(calc_trade_plan(item))
        item.update(calc_position_sizing(item, market_info))
        item.update(breakout_pullback_strategy_backtest(item["df"]))

        level = classify_stock(item)

        if not level:
            continue

        item["level"] = level

        if not market_info.get("allow_new_positions"):
            item["entry_status"] = "禁止新倉"
            item["entry_reason"] = market_info.get("risk_note", "市場風險偏高，禁止新倉。")

        item.pop("df", None)

        if level == "S":
            s_results.append(item)
        elif level == "A":
            a_results.append(item)

    s_results = sorted(s_results, key=lambda x: x["score"], reverse=True)
    a_results = sorted(a_results, key=lambda x: x["score"], reverse=True)

    elite_results = build_elite_results(s_results, a_results, market_info)

    all_ranked_items = s_results + a_results
    candidate_data = update_candidate_pool(all_ranked_items)

    candidate_pool_list = list(candidate_data.get("candidates", {}).values())[:MAX_CANDIDATE_DISPLAY]
    entry_alerts = candidate_data.get("entry_alerts", [])
    invalid_alerts = candidate_data.get("invalid_alerts", [])

    data = {
        "updated_at": taiwan_now(),
        "market_status": market_status,
        "market_score": market_score,
        "risk_mode": risk_mode,
        "risk_switch": market_info["risk_switch"],
        "allow_new_positions": market_info["allow_new_positions"],
        "risk_note": market_info["risk_note"],
        "risk_multiplier": market_info.get("risk_multiplier", 0),
        "stock_pool_count": total,

        "elite_count": len(elite_results),
        "s_count": len(s_results),
        "a_count": len(a_results),

        "elite_results": elite_results,
        "s_results": s_results[:MAX_S_RESULTS],
        "a_results": a_results[:MAX_A_RESULTS],
        "sector_rankings": sector_rankings,

        "candidate_count": len(candidate_pool_list),
        "candidate_pool": candidate_pool_list,
        "entry_alerts": entry_alerts,
        "invalid_alerts": invalid_alerts
    }

    save_scan_results(data)

    save_scan_status(
        "done",
        f"掃描完成：股票池 {total} 檔，今日精選 {len(elite_results)} 檔，S級 {len(s_results)} 檔，A級 {len(a_results)} 檔，候選 {len(candidate_pool_list)} 檔，進場提醒 {len(entry_alerts)} 檔。"
    )


# ======================
# 首頁
# ======================
@app.route("/")
def index():
    scan_data = load_scan_results()
    scan_status_data = load_scan_status()

    twii, otc = get_index()
    tracks = load_track()
    trade_logs = load_trade_log()

    tracks_changed = False

    for t in tracks:
        df = download_stock(t["symbol"], "1y")

        try:
            if df is None or df.empty:
                raise ValueError("no price data")

            curr = safe_float(df["Close"].iloc[-1])
            atr_series = calc_atr(df)
            atr_now = safe_float(atr_series.iloc[-1])

            entry_date = t.get("date", today_str())
            entry_dt = pd.to_datetime(entry_date, errors="coerce")

            if pd.notna(entry_dt):
                after_entry = df[df.index >= entry_dt]
            else:
                after_entry = df.tail(30)

            if after_entry.empty:
                after_entry = df.tail(30)

            highest_since_entry = safe_float(after_entry["High"].max()) or curr
            old_highest = t.get("highest_since_entry", 0)

            if highest_since_entry and highest_since_entry > old_highest:
                t["highest_since_entry"] = round(highest_since_entry, 2)
                tracks_changed = True

            if atr_now and t.get("highest_since_entry"):
                dynamic_trailing_stop = round(t["highest_since_entry"] - atr_now * 2.5, 2)
                old_dynamic_stop = t.get("dynamic_trailing_stop", 0)

                if dynamic_trailing_stop > old_dynamic_stop:
                    t["dynamic_trailing_stop"] = dynamic_trailing_stop
                    tracks_changed = True
            else:
                dynamic_trailing_stop = t.get("dynamic_trailing_stop", t.get("trailing_stop", 0))

            pnl = (curr - t["price"]) / t["price"] * 100

            t["curr"] = round(curr, 2)
            t["pnl"] = round(pnl, 2)

            if t.get("initial_stop") and curr <= t["initial_stop"]:
                t["signal"] = "停損"
            elif dynamic_trailing_stop and curr <= dynamic_trailing_stop and pnl > 0:
                t["signal"] = "移動停利"
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

    if tracks_changed:
        save_track(tracks)

    winrate, avg = calc_track_stats(tracks)
    trade_stats = calc_trade_log_stats(trade_logs)

    return render_template(
        "index.html",
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

        elite_results=scan_data.get("elite_results", []),
        s_results=scan_data.get("s_results", []),
        a_results=scan_data.get("a_results", []),
        sector_rankings=scan_data.get("sector_rankings", []),

        candidate_count=scan_data.get("candidate_count", 0),
        candidate_pool=scan_data.get("candidate_pool", []),
        entry_alerts=scan_data.get("entry_alerts", []),
        invalid_alerts=scan_data.get("invalid_alerts", []),

        scan_status=scan_status_data.get("status", "idle"),
        scan_message=scan_status_data.get("message", "尚未掃描"),
        scan_status_time=scan_status_data.get("updated_at", "-"),

        tracks=tracks,
        trade_logs=trade_logs[-10:],
        winrate=winrate,
        avg=avg,
        trade_stats=trade_stats,
        account_size=ACCOUNT_SIZE,
        risk_per_trade=round(RISK_PER_TRADE * 100, 2),
        now=taiwan_now()
    )


# ======================
# 路由
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


@app.route("/track/<symbol>/<name>/<price>/<stop_loss>/<take1>/<take2>")
def track(symbol, name, price, stop_loss, take1, take2):
    data = load_track()
    exists = any(x["symbol"] == symbol for x in data)

    scan_data = load_scan_results()
    all_items = (
        scan_data.get("elite_results", []) +
        scan_data.get("s_results", []) +
        scan_data.get("a_results", [])
    )

    source_item = next((x for x in all_items if x["symbol"] == symbol), {})

    if not exists:
        entry_price = float(price)

        data.append({
            "symbol": symbol,
            "name": name,
            "price": entry_price,
            "stop_loss": float(stop_loss),
            "take_profit_1": float(take1),
            "take_profit_2": float(take2),
            "date": today_str(),
            "level": source_item.get("level", "-"),
            "buy_type": source_item.get("buy_type", "-"),
            "entry_status": source_item.get("entry_status", "-"),
            "entry_price": source_item.get("entry_price", 0),
            "confirm_price": source_item.get("confirm_price", 0),
            "initial_stop": source_item.get("initial_stop", float(stop_loss)),
            "trailing_stop": source_item.get("trailing_stop", 0),
            "dynamic_trailing_stop": source_item.get("trailing_stop", 0),
            "highest_since_entry": entry_price,
            "risk_reward": source_item.get("risk_reward", 0),
            "entry_check_score": source_item.get("entry_check_score", 0),
            "egg_zone": source_item.get("egg_zone", "-"),
            "wave_stage": source_item.get("wave_stage", "-"),
            "candle_signal": source_item.get("candle_signal", "-"),
            "breakout_state": source_item.get("breakout_state", "-"),
            "score": source_item.get("score", 0),
            "sector": source_item.get("sector", "-")
        })

    save_track(data)

    return redirect(url_for("index"))


@app.route("/track-candidate/<symbol>")
def track_candidate(symbol):
    candidate_data = load_candidate_pool()
    candidates = candidate_data.get("candidates", {})
    item = candidates.get(symbol)

    if not item:
        return redirect(url_for("index"))

    data = load_track()
    exists = any(x["symbol"] == symbol for x in data)

    if not exists:
        entry_price = item.get("entry_price") or 0

        data.append({
            "symbol": symbol,
            "name": item.get("name", symbol),
            "price": float(entry_price) if entry_price else 0,
            "stop_loss": float(item.get("initial_stop", 0)),
            "take_profit_1": 0,
            "take_profit_2": 0,
            "date": today_str(),
            "level": item.get("level", "-"),
            "buy_type": item.get("buy_type", "-"),
            "entry_status": item.get("current_status", "-"),
            "entry_price": item.get("entry_price", 0),
            "confirm_price": item.get("confirm_price", 0),
            "initial_stop": item.get("initial_stop", 0),
            "trailing_stop": item.get("trailing_stop", 0),
            "dynamic_trailing_stop": item.get("trailing_stop", 0),
            "highest_since_entry": float(entry_price) if entry_price else 0,
            "risk_reward": item.get("risk_reward", 0),
            "entry_check_score": item.get("entry_check_score", 0),
            "egg_zone": item.get("egg_zone", "-"),
            "wave_stage": item.get("wave_stage", "-"),
            "candle_signal": item.get("candle_signal", "-"),
            "breakout_state": item.get("breakout_state", "-"),
            "score": item.get("score", 0),
            "sector": item.get("sector", "-")
        })

    save_track(data)
    return redirect(url_for("index"))


@app.route("/close-trade/<symbol>")
def close_trade(symbol):
    tracks = load_track()
    logs = load_trade_log()

    item = next((x for x in tracks if x["symbol"] == symbol), None)

    if not item:
        return redirect(url_for("index"))

    df = download_stock(symbol, "5d")

    try:
        curr = safe_float(df["Close"].iloc[-1])
    except Exception:
        curr = None

    if not curr:
        return redirect(url_for("index"))

    entry = float(item["price"])
    pnl_pct = (curr - entry) / entry * 100 if entry else 0

    logs.append({
        "symbol": item["symbol"],
        "name": item["name"],
        "entry_price": entry,
        "exit_price": round(curr, 2),
        "pnl_pct": round(pnl_pct, 2),
        "entry_date": item.get("date", "-"),
        "exit_date": today_str(),
        "level": item.get("level", "-"),
        "buy_type": item.get("buy_type", "-"),
        "entry_status": item.get("entry_status", "-"),
        "score": item.get("score", 0),
        "sector": item.get("sector", "-"),
        "egg_zone": item.get("egg_zone", "-"),
        "wave_stage": item.get("wave_stage", "-"),
        "candle_signal": item.get("candle_signal", "-"),
        "breakout_state": item.get("breakout_state", "-"),
        "highest_since_entry": item.get("highest_since_entry", "-"),
        "dynamic_trailing_stop": item.get("dynamic_trailing_stop", "-")
    })

    tracks = [x for x in tracks if x["symbol"] != symbol]

    save_trade_log(logs)
    save_track(tracks)

    return redirect(url_for("index"))


@app.route("/untrack/<symbol>")
def untrack(symbol):
    data = [x for x in load_track() if x["symbol"] != symbol]
    save_track(data)

    return redirect(url_for("index"))


# ======================
# 每天 16:00 自動掃描
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
