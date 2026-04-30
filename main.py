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
# 網站登入保護
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


TAIWAN_TZ = ZoneInfo("Asia/Taipei")

RESULT_FILE = "scan_results.json"
TRACK_FILE = "track.json"
TRADE_LOG_FILE = "trade_log.json"
STOCK_POOL_FILE = "stock_pool.json"
SCAN_STATUS_FILE = "scan_status.json"
INST_FILE = "institutional_cache.json"

FULL_MARKET_MIN_COUNT = 1700
PARTIAL_MARKET_MIN_COUNT = 1000

MAX_ELITE_RESULTS = 5
MAX_S_RESULTS = 10
MAX_A_RESULTS = 10
MAX_B_RESULTS = 10
MAX_HOT_RESULTS = 10
MAX_SECTOR_RANK = 10

ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "1000000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))

FEE_RATE = 0.001425 * 0.28
TAX_RATE = 0.003
SLIPPAGE_RATE = 0.001

is_scanning = False


# ======================
# 時間
# ======================
def taiwan_now():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")


# ======================
# JSON 工具
# ======================
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
        "2337.TW": ("旺宏", "記憶體"),
        "2408.TW": ("南亞科", "記憶體"),
        "6488.TWO": ("環球晶", "半導體材料"),
        "5483.TWO": ("中美晶", "半導體材料"),
        "4763.TW": ("材料-KY", "半導體材料"),
        "2317.TW": ("鴻海", "AI伺服器"),
        "2382.TW": ("廣達", "AI伺服器"),
        "3231.TW": ("緯創", "AI伺服器"),
        "2356.TW": ("英業達", "AI伺服器"),
        "6669.TW": ("緯穎", "AI伺服器"),
        "2308.TW": ("台達電", "電源"),
        "2357.TW": ("華碩", "電腦"),
        "2376.TW": ("技嘉", "電腦"),
        "3017.TW": ("奇鋐", "散熱"),
        "3324.TWO": ("雙鴻", "散熱"),
        "3653.TW": ("健策", "散熱"),
        "8996.TWO": ("高力", "散熱"),
        "2345.TW": ("智邦", "網通"),
        "3008.TW": ("大立光", "光學"),
        "3406.TW": ("玉晶光", "光學"),
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
        "6472.TW": ("保瑞", "生技")
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
        print("使用快取股票池，共", len(stocks), "檔")
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

        print("TWSE 上市股票：", len(market))
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

            print("TPEx 嘗試：", url, len(temp), "檔")

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

    print("ISIN 上市股票：", len(listed))
    print("ISIN 上櫃股票：", len(otc))
    print("ISIN 全市場股票：", len(market))

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


def safe_float(x):
    try:
        if hasattr(x, "iloc"):
            x = x.iloc[0]
        return float(x)
    except Exception:
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
            "risk_note": "大盤資料不足，暫不建議積極建立新倉。"
        }

    score = twii["trend_score"] + int(otc.get("trend_score", 0) * 0.6)

    if twii["above_ma20"] and twii["above_ma60"] and twii["ma20_gt_ma60"] and otc.get("above_ma20"):
        return {
            "market_status": "強多市場",
            "market_score": 25,
            "risk_mode": "積極",
            "risk_switch": "允許新倉",
            "allow_new_positions": True,
            "risk_note": "加權與櫃買結構偏多，可允許 S/A 級進入今日精選。"
        }

    if twii["above_ma60"] and twii["ma20_gt_ma60"]:
        return {
            "market_status": "多頭市場",
            "market_score": 15,
            "risk_mode": "正常",
            "risk_switch": "允許新倉",
            "allow_new_positions": True,
            "risk_note": "大盤中期偏多，但仍需避開過熱與高量轉弱標的。"
        }

    if not twii["above_ma20"] and not otc.get("above_ma20", False):
        return {
            "market_status": "轉弱市場",
            "market_score": -25,
            "risk_mode": "防守",
            "risk_switch": "禁止新倉",
            "allow_new_positions": False,
            "risk_note": "加權與櫃買皆弱於月線，系統禁止新倉，只保留觀察與追蹤。"
        }

    if not twii["above_ma20"]:
        return {
            "market_status": "盤整偏弱",
            "market_score": -10,
            "risk_mode": "保守",
            "risk_switch": "只允許S級",
            "allow_new_positions": True,
            "risk_note": "大盤低於月線，僅允許最強 S 級，並降低部位。"
        }

    return {
        "market_status": "盤整市場",
        "market_score": -5,
        "risk_mode": "保守",
        "risk_switch": "減碼觀察",
        "allow_new_positions": True,
        "risk_note": "大盤盤整，今日精選會更偏向低風險與不追高標的。"
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
# 技術與主力資金
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

    if close.iloc[-1] > close.rolling(20).max().iloc[-2] and money.iloc[-1] > ma_money_20.iloc[-1] * 1.3:
        main_score += 25
        main_signals.append("帶量突破")

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
        score += 20

    if is_break_60:
        signals.append("突破60日高點")
        score += 25

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

    stop_loss = None
    take_profit_1 = None
    take_profit_2 = None

    if atr_now:
        stop_loss = round(price - atr_now * 2, 2)
        take_profit_1 = round(price + atr_now * 3, 2)
        take_profit_2 = round(price + atr_now * 5, 2)

    return {
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
        "atr_pct": round(float(atr_pct), 2)
    }


# ======================
# 族群強度與輪動排行
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
# 買點與進場狀態
# ======================
def determine_buy_type_and_entry_status(item):
    warnings = item.get("warnings", [])
    main_signals = item.get("main_signals", [])
    level = item.get("level", "")
    score = item.get("score", 0)

    is_hot = (
        level == "HOT" or
        "距離月線過遠" in warnings or
        "距離季線過遠" in warnings or
        "5日漲幅過熱" in warnings or
        "20日漲幅過熱" in warnings or
        "高量下跌警訊" in main_signals
    )

    if "跌破月線" in warnings:
        return {
            "buy_type": "弱勢取消型",
            "entry_status": "跌破取消",
            "entry_reason": "股價已跌破月線，短線結構轉弱，不建議新進。"
        }

    if is_hot:
        return {
            "buy_type": "過熱觀察型",
            "entry_status": "過熱不追",
            "entry_reason": "分數雖高，但距離均線過遠或有過熱警訊，建議等拉回轉穩。"
        }

    if item.get("is_break_60") and item.get("is_volume_warm") and item.get("money_ratio", 0) >= 1.2:
        if item.get("ma20_distance", 99) <= 8:
            return {
                "buy_type": "突破型",
                "entry_status": "可觀察進場",
                "entry_reason": "帶量突破60日高點且距離月線未過遠，屬於較積極的突破買點。"
            }
        return {
            "buy_type": "突破型",
            "entry_status": "等拉回",
            "entry_reason": "雖然突破，但距離月線偏遠，建議等量縮回測不破再觀察。"
        }

    if item.get("is_break_20") and item.get("is_volume_warm"):
        if item.get("ma20_distance", 99) <= 8:
            return {
                "buy_type": "突破型",
                "entry_status": "可觀察進場",
                "entry_reason": "突破20日高點並伴隨量能增溫，可列為短線突破觀察。"
            }
        return {
            "buy_type": "突破型",
            "entry_status": "等拉回",
            "entry_reason": "突破後距離月線偏遠，追高風險較高。"
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

    if item.get("is_near_ma20") and item.get("is_above_ma60") and item.get("technical_score", 0) >= 50:
        return {
            "buy_type": "拉回型",
            "entry_status": "可觀察進場",
            "entry_reason": "股價接近月線且仍在季線之上，屬拉回不破的觀察買點。"
        }

    if item.get("is_low_start_zone") and item.get("is_volume_warm") and item.get("main_score", 0) >= 35:
        return {
            "buy_type": "低位啟動型",
            "entry_status": "等突破",
            "entry_reason": "位階仍不算高且量能轉強，但建議等突破20日高點確認。"
        }

    if level in ["S", "A"] and score >= 170:
        return {
            "buy_type": "趨勢觀察型",
            "entry_status": "等突破",
            "entry_reason": "綜合條件不差，但尚未出現明確突破或拉回確認訊號。"
        }

    return {
        "buy_type": "觀察型",
        "entry_status": "僅列觀察",
        "entry_reason": "條件有部分轉強，但尚未達明確進場型態。"
    }


# ======================
# 資金控管
# ======================
def calc_position_sizing(item):
    price = item.get("price")
    stop_loss = item.get("stop_loss")

    if not price or not stop_loss or price <= stop_loss:
        return {
            "risk_amount": round(ACCOUNT_SIZE * RISK_PER_TRADE, 0),
            "risk_per_share": 0,
            "suggest_shares": 0,
            "suggest_lots": 0,
            "position_value": 0,
            "position_note": "停損價資料不足，無法估算部位。"
        }

    risk_amount = ACCOUNT_SIZE * RISK_PER_TRADE
    risk_per_share = price - stop_loss
    suggest_shares = math.floor(risk_amount / risk_per_share)

    suggest_lots = math.floor(suggest_shares / 1000)
    position_value = suggest_shares * price

    if suggest_lots <= 0:
        note = "停損距離較大或股價較高，依單筆風險限制不足一張。"
    elif suggest_lots >= 5:
        note = "建議部位偏大，仍應分批建立，不宜一次滿倉。"
    else:
        note = "依帳戶風險與停損距離估算的建議部位。"

    return {
        "risk_amount": round(risk_amount, 0),
        "risk_per_share": round(risk_per_share, 2),
        "suggest_shares": suggest_shares,
        "suggest_lots": suggest_lots,
        "position_value": round(position_value, 0),
        "position_note": note
    }


# ======================
# 真實交易回測
# ======================
def realistic_backtest(df):
    if df is None or len(df) < 220:
        return {
            "bt_count": 0,
            "bt_winrate": 0,
            "bt_avg_return": 0,
            "bt_expectancy": 0,
            "bt_avg_win": 0,
            "bt_avg_loss": 0,
            "bt_max_drawdown": 0,
            "bt_profit_factor": 0
        }

    trades = []
    equity = 100.0
    equity_curve = [equity]

    for i in range(140, len(df) - 25, 5):
        sample = df.iloc[:i].copy()
        r = analyze_stock(sample)

        if not r:
            continue

        simple_score = r["technical_score"] + r["main_score"]

        if simple_score < 120:
            continue

        entry_idx = i + 1
        if entry_idx >= len(df):
            continue

        entry_open = safe_float(df["Open"].iloc[entry_idx])
        if not entry_open:
            continue

        atr = calc_atr(df.iloc[:i])
        atr_now = safe_float(atr.iloc[-1])
        if not atr_now:
            continue

        entry_price = entry_open * (1 + SLIPPAGE_RATE)
        stop_price = entry_price - atr_now * 2
        take_price = entry_price + atr_now * 3

        exit_price = safe_float(df["Close"].iloc[min(entry_idx + 20, len(df) - 1)])
        exit_reason = "時間出場"

        for j in range(entry_idx, min(entry_idx + 20, len(df))):
            day_low = safe_float(df["Low"].iloc[j])
            day_high = safe_float(df["High"].iloc[j])
            day_close = safe_float(df["Close"].iloc[j])

            ma5 = df["Close"].iloc[:j + 1].rolling(5).mean().iloc[-1]

            if day_low is not None and day_low <= stop_price:
                exit_price = stop_price * (1 - SLIPPAGE_RATE)
                exit_reason = "停損"
                break

            if day_high is not None and day_high >= take_price:
                exit_price = take_price * (1 - SLIPPAGE_RATE)
                exit_reason = "停利"
                break

            if j > entry_idx + 5 and day_close is not None and day_close < ma5:
                exit_price = day_close * (1 - SLIPPAGE_RATE)
                exit_reason = "跌破5日線"
                break

        gross_return = (exit_price - entry_price) / entry_price * 100
        cost = (FEE_RATE * 2 + TAX_RATE) * 100
        net_return = gross_return - cost

        trades.append({
            "return": net_return,
            "reason": exit_reason
        })

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
            "bt_profit_factor": 0
        }

    returns = [x["return"] for x in trades]
    wins = [x for x in returns if x > 0]
    losses = [x for x in returns if x <= 0]

    winrate = len(wins) / len(returns) * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    avg_return = sum(returns) / len(returns)

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
        "bt_count": len(returns),
        "bt_winrate": round(winrate, 2),
        "bt_avg_return": round(avg_return, 2),
        "bt_expectancy": round(expectancy, 2),
        "bt_avg_win": round(avg_win, 2),
        "bt_avg_loss": round(avg_loss, 2),
        "bt_max_drawdown": round(max_dd, 2),
        "bt_profit_factor": round(profit_factor, 2)
    }


# ======================
# 分級
# ======================
def classify_stock(item):
    total_score = item["score"]
    main_score = item["main_score"]
    inst_score = item["inst_score"]
    sector_score = item["sector_score"]
    money_ratio = item["money_ratio"]
    change_5d = item["change_5d"]
    change_20d = item["change_20d"]
    atr_pct = item["atr_pct"]
    warnings = item["warnings"]
    main_signals = item["main_signals"]

    is_overheated = (
        change_5d > 22 or
        change_20d > 45 or
        atr_pct > 10 or
        "5日漲幅過熱" in warnings or
        "20日漲幅過熱" in warnings or
        "距離月線過遠" in warnings or
        "距離季線過遠" in warnings or
        "高量下跌警訊" in main_signals
    )

    if is_overheated and total_score >= 150:
        return "HOT"

    if (
        total_score >= 210 and
        main_score >= 60 and
        inst_score >= 20 and
        sector_score >= 20 and
        money_ratio >= 1.25
    ):
        return "S"

    if (
        total_score >= 170 and
        main_score >= 35 and
        sector_score >= 10 and
        money_ratio >= 1.1
    ):
        return "A"

    if (
        total_score >= 135 and
        (main_score >= 20 or inst_score >= 15 or sector_score >= 15)
    ):
        return "B"

    return None


def build_elite_results(s_results, a_results, market_info):
    if not market_info.get("allow_new_positions"):
        return []

    pool = []

    for item in s_results:
        copied = dict(item)
        copied["elite_reason"] = "S級優先，分數、資金、法人與族群條件較佳。"
        pool.append(copied)

    if market_info.get("risk_switch") != "只允許S級":
        for item in a_results:
            copied = dict(item)
            copied["elite_reason"] = "A級補選，適合等待突破或拉回。"
            pool.append(copied)

    def elite_score(x):
        bonus = 0

        if x.get("entry_status") == "可觀察進場":
            bonus += 18
        elif x.get("entry_status") == "等拉回":
            bonus += 8
        elif x.get("entry_status") == "等突破":
            bonus += 5

        if x.get("bt_expectancy", 0) > 0:
            bonus += 10
        if x.get("bt_winrate", 0) >= 50:
            bonus += 10
        if x.get("trust_days", 0) >= 3:
            bonus += 8
        if x.get("sector_rank", 999) <= 5:
            bonus += 10

        return x.get("score", 0) + bonus

    filtered = []

    for x in pool:
        if x.get("entry_status") in ["過熱不追", "跌破取消"]:
            continue
        if "高量下跌警訊" in x.get("main_signals", []):
            continue
        filtered.append(x)

    filtered = sorted(filtered, key=elite_score, reverse=True)
    return filtered[:MAX_ELITE_RESULTS]


# ======================
# 交易日誌與統計
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
        if len(vals) >= 1:
            v = sum(vals) / len(vals)
            if v > best_buy_avg:
                best_buy_avg = v
                best_buy_type = k

    best_level = "-"
    best_level_avg = -999

    for k, vals in by_level.items():
        if len(vals) >= 1:
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
        "b_count": 0,
        "hot_count": 0,
        "elite_results": [],
        "s_results": [],
        "a_results": [],
        "b_results": [],
        "hot_results": [],
        "sector_rankings": []
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
    b_results = []
    hot_results = []

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

        level = classify_stock(item)

        if not level:
            continue

        item["level"] = level
        item.update(determine_buy_type_and_entry_status(item))
        item.update(realistic_backtest(item["df"]))
        item.update(calc_position_sizing(item))

        if not market_info.get("allow_new_positions"):
            item["entry_status"] = "禁止新倉"
            item["entry_reason"] = market_info.get("risk_note", "市場風險偏高，禁止新倉。")

        item.pop("df", None)

        if level == "S":
            s_results.append(item)
        elif level == "A":
            a_results.append(item)
        elif level == "B":
            b_results.append(item)
        elif level == "HOT":
            hot_results.append(item)

    s_results = sorted(s_results, key=lambda x: x["score"], reverse=True)
    a_results = sorted(a_results, key=lambda x: x["score"], reverse=True)
    b_results = sorted(b_results, key=lambda x: x["score"], reverse=True)
    hot_results = sorted(hot_results, key=lambda x: x["score"], reverse=True)

    elite_results = build_elite_results(s_results, a_results, market_info)

    data = {
        "updated_at": taiwan_now(),
        "market_status": market_status,
        "market_score": market_score,
        "risk_mode": risk_mode,
        "risk_switch": market_info["risk_switch"],
        "allow_new_positions": market_info["allow_new_positions"],
        "risk_note": market_info["risk_note"],
        "stock_pool_count": total,

        "elite_count": len(elite_results),
        "s_count": len(s_results),
        "a_count": len(a_results),
        "b_count": len(b_results),
        "hot_count": len(hot_results),

        "elite_results": elite_results,
        "s_results": s_results[:MAX_S_RESULTS],
        "a_results": a_results[:MAX_A_RESULTS],
        "b_results": b_results[:MAX_B_RESULTS],
        "hot_results": hot_results[:MAX_HOT_RESULTS],
        "sector_rankings": sector_rankings
    }

    save_scan_results(data)

    save_scan_status(
        "done",
        f"掃描完成：股票池 {total} 檔，今日精選 {len(elite_results)} 檔，S級 {len(s_results)} 檔，A級 {len(a_results)} 檔，B級 {len(b_results)} 檔，過熱 {len(hot_results)} 檔。"
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
        scan_updated_at=scan_data.get("updated_at", "尚未掃描"),
        stock_pool_count=scan_data.get("stock_pool_count", 0),

        elite_count=scan_data.get("elite_count", 0),
        s_count=scan_data.get("s_count", 0),
        a_count=scan_data.get("a_count", 0),
        b_count=scan_data.get("b_count", 0),
        hot_count=scan_data.get("hot_count", 0),

        elite_results=scan_data.get("elite_results", []),
        s_results=scan_data.get("s_results", []),
        a_results=scan_data.get("a_results", []),
        b_results=scan_data.get("b_results", []),
        hot_results=scan_data.get("hot_results", []),
        sector_rankings=scan_data.get("sector_rankings", []),

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
# 追蹤與結案
# ======================
@app.route("/track/<symbol>/<name>/<price>/<stop_loss>/<take1>/<take2>")
def track(symbol, name, price, stop_loss, take1, take2):
    data = load_track()
    exists = any(x["symbol"] == symbol for x in data)

    scan_data = load_scan_results()
    all_items = (
        scan_data.get("elite_results", []) +
        scan_data.get("s_results", []) +
        scan_data.get("a_results", []) +
        scan_data.get("b_results", []) +
        scan_data.get("hot_results", [])
    )

    source_item = next((x for x in all_items if x["symbol"] == symbol), {})

    if not exists:
        data.append({
            "symbol": symbol,
            "name": name,
            "price": float(price),
            "stop_loss": float(stop_loss),
            "take_profit_1": float(take1),
            "take_profit_2": float(take2),
            "date": today_str(),
            "level": source_item.get("level", "-"),
            "buy_type": source_item.get("buy_type", "-"),
            "entry_status": source_item.get("entry_status", "-"),
            "score": source_item.get("score", 0),
            "sector": source_item.get("sector", "-")
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
    pnl_pct = (curr - entry) / entry * 100

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
        "sector": item.get("sector", "-")
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
