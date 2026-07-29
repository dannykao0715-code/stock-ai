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

APP_VERSION_NAME = "價值分析_大盤風控_技術進場輔助版_2026-07-29"

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456")
TZ = ZoneInfo("Asia/Taipei")

RESULT_FILE = "scan_results.json"
TRACK_FILE = "track.json"
TRADE_LOG_FILE = "trade_log.json"
STOCK_POOL_FILE = "stock_pool.json"
SCAN_STATUS_FILE = "scan_status.json"
SCHEDULE_HEALTH_FILE = "schedule_health.json"
HEARTBEAT_FILE = "heartbeat.json"
CANDIDATE_FILE = "candidate_pool.json"
LINE_USER_FILE = "line_user.json"
LINE_NOTIFY_LOG_FILE = "line_notify_log.json"
SIGNAL_DATABASE_FILE = "signal_database.json"
STRATEGY_WEIGHTS_FILE = "strategy_weights.json"
OPTIMIZATION_LOG_FILE = "optimization_log.json"
AI_STRATEGY_REPORT_FILE = "ai_strategy_report.json"

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
# 價值分析｜低估重估潛力股模型設定
# =====================================================
VALUE_MODEL_VERSION = "value_repricing_model_v1"
VALUE_SCAN_LIMIT = int(os.getenv("VALUE_SCAN_LIMIT", "350"))               # 每次深入基本面掃描最多檔數，避免Railway卡住
VALUE_SCORE_BUY = float(os.getenv("VALUE_SCORE_BUY", "85"))                # 買進提醒分數
VALUE_SCORE_WATCH = float(os.getenv("VALUE_SCORE_WATCH", "75"))            # 觀察分數
VALUE_MIN_UPSIDE_PCT = float(os.getenv("VALUE_MIN_UPSIDE_PCT", "100"))     # 目標找翻倍空間
VALUE_MIN_UNDERVALUE_SCORE = float(os.getenv("VALUE_MIN_UNDERVALUE_SCORE", "15"))
VALUE_MIN_CATALYST_SCORE = float(os.getenv("VALUE_MIN_CATALYST_SCORE", "15"))
VALUE_MAX_ALERTS = int(os.getenv("VALUE_MAX_ALERTS", "5"))
VALUE_SLEEP = float(os.getenv("VALUE_SLEEP", "0.25"))

# LINE只顯示結論，詳細數據留後台
VALUE_LINE_DETAIL_ENABLED = os.getenv("VALUE_LINE_DETAIL_ENABLED", "0") == "1"
VALUE_REQUIRE_TECH_ENTRY = os.getenv("VALUE_REQUIRE_TECH_ENTRY", "1") == "1"
VALUE_MIN_TECH_SCORE = float(os.getenv("VALUE_MIN_TECH_SCORE", "55"))
VALUE_CAUTION_TECH_SCORE = float(os.getenv("VALUE_CAUTION_TECH_SCORE", "65"))
VALUE_CAUTION_MIN_SCORE = float(os.getenv("VALUE_CAUTION_MIN_SCORE", "90"))
VALUE_CAUTION_MIN_UPSIDE = float(os.getenv("VALUE_CAUTION_MIN_UPSIDE", "150"))

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
ROUGH_SCAN_TOP_N = int(os.getenv("ROUGH_SCAN_TOP_N", "200"))
ROUGH_SCAN_MIN_SCORE = float(os.getenv("ROUGH_SCAN_MIN_SCORE", "15"))
DETAILED_ANALYSIS_SLEEP = float(os.getenv("DETAILED_ANALYSIS_SLEEP", "0.01"))
ROUGH_ANALYSIS_SLEEP = float(os.getenv("ROUGH_ANALYSIS_SLEEP", "0.005"))
DATA_CACHE_DIR = os.getenv("DATA_CACHE_DIR", "data_cache")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_ANALYSIS_ENABLED = os.getenv("OPENAI_ANALYSIS_ENABLED", "1") == "1"
SCAN_TIMEOUT_MINUTES = int(os.getenv("SCAN_TIMEOUT_MINUTES", "90"))
LOOSE_OBSERVATION_ENABLED = os.getenv("LOOSE_OBSERVATION_ENABLED", "1") == "1"
LOOSE_OBSERVATION_LIMIT = int(os.getenv("LOOSE_OBSERVATION_LIMIT", "40"))
HEARTBEAT_STALE_MINUTES = int(os.getenv("HEARTBEAT_STALE_MINUTES", "60"))
SCAN_DATA_STALE_HOURS = int(os.getenv("SCAN_DATA_STALE_HOURS", "36"))
HEARTBEAT_LINE_REPORT_ENABLED = os.getenv("HEARTBEAT_LINE_REPORT_ENABLED", "1") == "1"
HEARTBEAT_HOLDING_CHECK_ENABLED = os.getenv("HEARTBEAT_HOLDING_CHECK_ENABLED", "1") == "1"
HEARTBEAT_HOLDING_PERIOD = os.getenv("HEARTBEAT_HOLDING_PERIOD", "6mo")
REALTIME_QUOTE_ENABLED = os.getenv("REALTIME_QUOTE_ENABLED", "1") == "1"
REALTIME_QUOTE_TIMEOUT = float(os.getenv("REALTIME_QUOTE_TIMEOUT", "6"))

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


def parse_taiwan_time(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except Exception:
        return None


def save_scan_status(status, message):
    write_json(SCAN_STATUS_FILE, {
        "status": status,
        "message": message,
        "updated_at": taiwan_now()
    })


def load_scan_status():
    data = read_json(SCAN_STATUS_FILE, {"status": "idle", "message": "尚未掃描", "updated_at": "-"})
    # 如果狀態卡在 running 超過設定時間，自動改成 timeout，避免畫面永遠顯示正在掃描。
    if data.get("status") == "running":
        ts = parse_taiwan_time(data.get("updated_at"))
        if ts:
            minutes = (datetime.now(TZ) - ts).total_seconds() / 60
            if minutes > SCAN_TIMEOUT_MINUTES:
                data = {
                    "status": "timeout",
                    "message": f"掃描逾時已自動解鎖：超過 {SCAN_TIMEOUT_MINUTES} 分鐘未完成，請重新掃描。",
                    "updated_at": taiwan_now()
                }
                write_json(SCAN_STATUS_FILE, data)
    return data


def reset_scan_status(message="已手動重置掃描狀態。"):
    global is_scanning
    is_scanning = False
    save_scan_status("idle", message)

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



def twse_exch_symbol(symbol):
    """
    轉成 TWSE MIS 即時報價用的 ex_ch 格式。
    - 2330.TW  -> tse_2330.tw
    - 8299.TWO -> otc_8299.tw
    - ^TWII    -> tse_t00.tw
    - ^TWOII   -> otc_o00.tw
    """
    if not symbol:
        return None

    s = str(symbol).strip()

    if s in ["^TWII", "TWII", "TAIEX", "tse_t00.tw"]:
        return "tse_t00.tw"

    if s in ["^TWOII", "TWOII", "OTC", "otc_o00.tw"]:
        return "otc_o00.tw"

    if s.endswith(".TW"):
        return f"tse_{s.replace('.TW','')}.tw"

    if s.endswith(".TWO"):
        return f"otc_{s.replace('.TWO','')}.tw"

    # 預設把純數字代號先當上市；如果抓不到再由 fallback 處理。
    if s.isdigit():
        return f"tse_{s}.tw"

    return None


def parse_twse_price(value):
    try:
        if value is None:
            return 0
        s = str(value).replace(",", "").strip()
        if s in ["", "-", "--", "NaN", "nan"]:
            return 0
        return float(s)
    except Exception:
        return 0


def fetch_twse_realtime_quote(symbol):
    """
    輕量即時報價：
    優先給心跳與持股監控使用，不拿來做全市場掃描。
    資料源：TWSE MIS / TPEX 即時報價格式。
    抓不到就回傳 None，後面會 fallback 到 yfinance。
    """
    if not REALTIME_QUOTE_ENABLED:
        return None

    ex_ch = twse_exch_symbol(symbol)
    if not ex_ch:
        return None

    try:
        url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
        params = {
            "ex_ch": ex_ch,
            "json": "1",
            "delay": "0",
            "_": int(time.time() * 1000),
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://mis.twse.com.tw/stock/index.jsp",
            "Accept": "application/json,text/plain,*/*",
        }

        r = requests.get(url, params=params, headers=headers, timeout=REALTIME_QUOTE_TIMEOUT)
        if r.status_code != 200:
            return None

        data = r.json()
        arr = data.get("msgArray", [])
        if not arr:
            return None

        q = arr[0]
        price = parse_twse_price(q.get("z"))

        # 有時候 z 會是 "-"，用最佳買賣或昨收做備援，但要標記不是完整成交價。
        source_note = "TWSE/TPEX即時成交"
        if not price:
            price = parse_twse_price(q.get("a", "").split("_")[0] if q.get("a") else 0)
            source_note = "TWSE/TPEX最佳賣價備援"

        if not price:
            price = parse_twse_price(q.get("b", "").split("_")[0] if q.get("b") else 0)
            source_note = "TWSE/TPEX最佳買價備援"

        if not price:
            price = parse_twse_price(q.get("y"))
            source_note = "TWSE/TPEX昨收備援"

        if not price:
            return None

        return {
            "symbol": symbol,
            "ex_ch": ex_ch,
            "name": q.get("n", ""),
            "price": round2(price),
            "open": round2(parse_twse_price(q.get("o"))),
            "high": round2(parse_twse_price(q.get("h"))),
            "low": round2(parse_twse_price(q.get("l"))),
            "yesterday": round2(parse_twse_price(q.get("y"))),
            "volume": q.get("v", "-"),
            "date": q.get("d", "-"),
            "time": q.get("t", "-"),
            "source": source_note,
            "raw_time": f"{q.get('d','-')} {q.get('t','-')}",
        }

    except Exception as e:
        print("fetch_twse_realtime_quote failed", symbol, e)
        return None


def get_realtime_price(symbol):
    q = fetch_twse_realtime_quote(symbol)
    if q and q.get("price"):
        return q

    # fallback：避免TWSE/TPEX暫時抓不到時整個心跳失效
    df = download_stock(symbol, "5d")
    if df is None or df.empty:
        return None

    return {
        "symbol": symbol,
        "price": round2(df["Close"].iloc[-1]),
        "date": "-",
        "time": "-",
        "source": "yfinance備援",
        "raw_time": "-",
    }



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
    q = get_realtime_price(symbol)
    if q and q.get("price"):
        return q.get("price")
    return "-"

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



def is_strict_entry_candidate(item):
    rr = safe_float(item.get("risk_reward", 0))
    current_status = item.get("current_status", item.get("entry_status", "-"))
    execution_action = item.get("execution_action", "-")
    price = safe_float(item.get("day_close", 0)) or safe_float(item.get("price", 0))
    entry_low = safe_float(item.get("next_entry_low", 0))
    entry_high = safe_float(item.get("next_entry_high", 0))
    no_entry = safe_float(item.get("no_entry_price", 0))

    if current_status == "可觀察進場" and rr >= MIN_RISK_REWARD_ENTRY:
        if execution_action in ["可試單", "可小部位試單", "等待確認"]:
            if price and entry_low and entry_high:
                return entry_low <= price <= entry_high and price > no_entry
            return True

    return False


def loose_observation_reason(item):
    rr = safe_float(item.get("risk_reward", 0))
    current_status = item.get("current_status", item.get("entry_status", "-"))
    execution_action = item.get("execution_action", "-")
    price = safe_float(item.get("day_close", 0)) or safe_float(item.get("price", 0))
    entry_low = safe_float(item.get("next_entry_low", 0))
    entry_high = safe_float(item.get("next_entry_high", 0))
    no_entry = safe_float(item.get("no_entry_price", 0))

    if is_strict_entry_candidate(item):
        return "嚴格C級"

    if current_status in ["跌破取消", "過熱不追", "不列入", "流動性不足", "禁止新倉"]:
        return ""

    if price and no_entry and price < no_entry:
        return "跌破不進，留作失敗/反彈觀察樣本"

    if price and entry_high and price > entry_high:
        return "高於進場區，開高不追但留作觀察樣本"

    if price and entry_low and no_entry and no_entry <= price < entry_low:
        return "未到進場區，等回採觀察樣本"

    if current_status in ["等待回採", "觀察中", "尚未觸發", "可觀察進場"]:
        return f"{current_status}，寬鬆觀察樣本"

    if rr >= 1.2:
        return "風報比接近門檻，寬鬆觀察樣本"

    if item.get("level") in ["S", "A"]:
        return "S/A級候選，寬鬆觀察樣本"

    return ""


def record_loose_observation_signals(candidates):
    """
    寬鬆觀察訊號：
    不推播LINE，不當成可買進，只是為了快速累積資料庫。
    用來比較：嚴格C級 vs 寬鬆觀察，哪一種後續表現更好。
    """
    if not LOOSE_OBSERVATION_ENABLED:
        return 0

    count = 0

    for item in candidates[:LOOSE_OBSERVATION_LIMIT]:
        try:
            if is_strict_entry_candidate(item):
                continue

            reason = loose_observation_reason(item)
            if not reason:
                continue

            price = safe_float(item.get("day_close", 0)) or safe_float(item.get("price", 0))
            if not price:
                # 沒有價格就不記，避免資料失真
                continue

            record_ai_signal(
                "watch",
                item,
                "D",
                "寬鬆觀察",
                price,
                reason,
                extra={
                    "strict_or_loose": "loose_watch",
                    "current_status": item.get("current_status", item.get("entry_status", "-")),
                    "execution_action": item.get("execution_action", "-"),
                    "observe_reason": reason,
                    "confidence": 0,
                    "position_note": "只觀察，不進場",
                },
            )
            count += 1

        except Exception as e:
            print("record loose watch failed", item.get("symbol"), e)

    return count




def evaluate_signal_database():
    """
    更新訊號績效：
    價值分析版主要追蹤買進提醒後 30/60/120/240 個交易日績效。
    仍保留舊版 entry/watch 評估，避免舊資料不能看。
    """
    try:
        db = load_signal_database()
        signals = db.get("signals", [])
        changed = False

        for rec in signals:
            if rec.get("signal_type") not in ["買進提醒", "停利提醒", "賣出提醒", "entry", "watch"]:
                continue

            symbol = rec.get("symbol")
            price = safe_float(rec.get("price", 0))
            if not symbol or not price:
                continue

            try:
                signal_date = pd.to_datetime(rec.get("date"))
            except Exception:
                continue

            df = download_stock(symbol, "2y")
            if df is None or df.empty:
                continue

            after = df[df.index.normalize() >= signal_date.normalize()]
            if after.empty:
                continue

            result = rec.get("result", {})

            for days in [30, 60, 120, 240]:
                if len(after) >= days:
                    window = after.head(days)
                    close_px = safe_float(window["Close"].iloc[-1])
                    high_px = safe_float(window["High"].max())
                    low_px = safe_float(window["Low"].min())
                    result[f"return_{days}d_pct"] = round2(pct(close_px, price))
                    result[f"max_up_{days}d_pct"] = round2(pct(high_px, price))
                    result[f"max_down_{days}d_pct"] = round2(pct(low_px, price))
                    result[f"close_{days}d"] = round2(close_px)

            # 舊版短天期欄位也保留
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

            if len(after) >= 240:
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

    def rows(t):
        return [x for x in signals if x.get("signal_type") == t]

    buy_rows = rows("買進提醒")
    take_profit_rows = rows("停利提醒")
    sell_rows = rows("賣出提醒")
    old_entry = rows("entry")
    old_watch = rows("watch")

    def done(arr, key):
        return [x for x in arr if x.get("result", {}).get(key) is not None]

    def avg_return(arr, key):
        arr = done(arr, key)
        if not arr:
            return 0
        return round2(sum(safe_float(x.get("result", {}).get(key, 0)) for x in arr) / len(arr))

    def win_rate(arr, key):
        arr = done(arr, key)
        if not arr:
            return 0
        return round2(len([x for x in arr if safe_float(x.get("result", {}).get(key, 0)) > 0]) / len(arr) * 100)

    def block(title, arr):
        return (
            f"{title}\n"
            f"累積：{len(arr)} 筆\n"
            f"30日：{len(done(arr, 'return_30d_pct'))} 筆｜均報 {avg_return(arr, 'return_30d_pct')}%｜勝率 {win_rate(arr, 'return_30d_pct')}%\n"
            f"60日：{len(done(arr, 'return_60d_pct'))} 筆｜均報 {avg_return(arr, 'return_60d_pct')}%｜勝率 {win_rate(arr, 'return_60d_pct')}%\n"
            f"120日：{len(done(arr, 'return_120d_pct'))} 筆｜均報 {avg_return(arr, 'return_120d_pct')}%｜勝率 {win_rate(arr, 'return_120d_pct')}%\n"
            f"240日：{len(done(arr, 'return_240d_pct'))} 筆｜均報 {avg_return(arr, 'return_240d_pct')}%｜勝率 {win_rate(arr, 'return_240d_pct')}%\n"
        )

    def group_perf(arr, key, title):
        mp = {}
        for r in arr:
            name = r.get(key) or "未分類"
            mp.setdefault(name, []).append(r)
        out = [f"\n{title}"]
        if not mp:
            out.append("尚無資料")
            return "\n".join(out)
        for name, a in sorted(mp.items(), key=lambda kv: len(kv[1]), reverse=True)[:20]:
            out.append(
                f"{name}｜樣本 {len(a)}｜120日 {len(done(a, 'return_120d_pct'))} 筆｜"
                f"均報 {avg_return(a, 'return_120d_pct')}%｜勝率 {win_rate(a, 'return_120d_pct')}%"
            )
        return "\n".join(out)

    out = [
        "價值分析｜低估重估潛力股系統績效統計",
        f"版本：{APP_VERSION_NAME}",
        f"模型版本：{VALUE_MODEL_VERSION}",
        "",
        block("買進提醒", buy_rows),
        block("停利提醒", take_profit_rows),
        block("賣出提醒", sell_rows),
        block("舊版進場 entry", old_entry),
        block("舊版觀察 watch", old_watch),
        "說明：",
        "買進提醒：系統判斷低估轉強且具備重估空間，只提醒可分批買進。",
        "停利提醒：估值偏高或潛在空間不足，提醒分批停利。",
        "賣出提醒：基本面轉弱或原始買進邏輯破壞，提醒減碼或出場。",
        group_perf(buy_rows, "level", "依等級統計"),
        group_perf(buy_rows, "industry", "依產業統計"),
        group_perf(buy_rows, "upside_bucket", "依潛在空間統計"),
        group_perf(buy_rows, "undervalue_bucket", "依低估程度統計"),
    ]
    return "\n".join(out)

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
            "【價值分析買進提醒】\n"
            f"大盤：{scan.get('value_market_status', scan.get('market_status','-'))}｜{scan.get('value_market_policy','-')}\n"
            "目前沒有符合買進條件的低估重估股。\n"
            "系統會持續掃描，沒機會就不通知。"
        )

    rows = [f"【價值分析買進提醒】\n大盤：{scan.get('value_market_status', scan.get('market_status','-'))}｜{scan.get('value_market_policy','-')}"]

    for a in alerts[:VALUE_MAX_ALERTS]:
        rows.append(
            f"\n股票：{a.get('name','-')} {short_symbol(a.get('symbol',''))}\n"
            f"判斷：低估轉強，具備重估空間\n"
            f"建議：可分批買進"
        )
        if VALUE_LINE_DETAIL_ENABLED:
            rows.append(
                f"分數：{a.get('score','-')}｜合理價：{a.get('fair_value','-')}｜潛在空間：{a.get('upside_pct','-')}%"
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
        "【價值分析持股監控】",
        "只在持股出現停利或賣出條件時提醒。"
    ]

    if not tracks:
        rows.append("\n目前沒有追蹤中的持股。")
        return "\n".join(rows)

    action_rows = []
    silent_count = 0

    for t in tracks:
        try:
            chk = value_sell_check_for_holding(t)
            item = chk.get("item", {})
            if chk.get("alert"):
                signal_type = chk.get("type", "賣出提醒")
                record_value_signal(signal_type, item, chk.get("reason", ""))

                if signal_type == "停利提醒":
                    action_rows.append(
                        f"\n【停利提醒】\n"
                        f"股票：{item.get('name', t.get('name','-'))} {short_symbol(t.get('symbol',''))}\n"
                        f"判斷：估值偏高，潛在空間不足\n"
                        f"建議：分批停利"
                    )
                else:
                    action_rows.append(
                        f"\n【賣出提醒】\n"
                        f"股票：{item.get('name', t.get('name','-'))} {short_symbol(t.get('symbol',''))}\n"
                        f"判斷：基本面轉弱 / 原始邏輯破壞\n"
                        f"建議：減碼或出場"
                    )
            else:
                silent_count += 1
        except Exception as e:
            print("value holding check failed", t.get("symbol"), e)
            silent_count += 1

    if action_rows:
        rows.extend(action_rows)
        if silent_count:
            rows.append(f"\n其餘 {silent_count} 檔尚未觸發賣出或停利條件。")
    else:
        rows.append("\n目前沒有持股需要處理。")

    return "\n".join(rows)


def format_line_preclose_decision():
    # 13:20 不再做短線技術進場，改成價值分析買進提醒。
    return format_line_entry_alerts()

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








def load_schedule_health():
    return read_json(SCHEDULE_HEALTH_FILE, {
        "updated_at": "-",
        "jobs": {}
    })


def save_schedule_health(data):
    data["updated_at"] = taiwan_now()
    write_json(SCHEDULE_HEALTH_FILE, data)


def record_schedule_health(job_name, status="ok", message=""):
    data = load_schedule_health()
    jobs = data.get("jobs", {})
    jobs[job_name] = {
        "status": status,
        "message": message,
        "last_run": taiwan_now()
    }
    data["jobs"] = jobs
    save_schedule_health(data)


def schedule_health_text():
    data = load_schedule_health()
    rows = ["排程健康檢查", f"更新時間：{data.get('updated_at','-')}"]
    jobs = data.get("jobs", {})
    for key in ["market_heartbeat", "daily_market_scan_1605", "line_open_watch_0910", "line_preclose_1320"]:
        j = jobs.get(key, {})
        rows.append(f"{key}｜{j.get('status','尚未執行')}｜{j.get('last_run','-')}｜{j.get('message','')}")
    return "\n".join(rows)


def run_line_open_watch_job():
    try:
        ok, info = send_line_notification("open")
        record_schedule_health("line_open_watch_0910", "ok" if ok else "error", str(info)[:200])
    except Exception as e:
        record_schedule_health("line_open_watch_0910", "error", str(e))


def run_line_preclose_job():
    try:
        ok, info = send_line_notification("preclose")
        record_schedule_health("line_preclose_1320", "ok" if ok else "error", str(info)[:200])
    except Exception as e:
        record_schedule_health("line_preclose_1320", "error", str(e))



def load_heartbeat_status():
    return read_json(HEARTBEAT_FILE, {
        "status": "unknown",
        "updated_at": "-",
        "message": "尚未執行心跳檢查",
        "warnings": [],
        "twii": "-",
        "otc": "-",
        "last_scan_time": "-",
        "scan_status": "-",
        "line_ready": False,
    })


def save_heartbeat_status(data):
    data["updated_at"] = taiwan_now()
    write_json(HEARTBEAT_FILE, data)


def heartbeat_status_label(status):
    if status == "ok":
        return "正常"
    if status == "warning":
        return "警告"
    if status == "danger":
        return "失效"
    return "未知"



def heartbeat_holding_check():
    """
    開盤期間持股輕量監控：
    只檢查「已加入追蹤」的股票，不掃全市場。
    持股現價優先使用 TWSE/TPEX 即時報價；抓不到才退回 yfinance。
    """
    tracks = load_track()
    if not tracks:
        return {
            "count": 0,
            "summary": "目前沒有追蹤持股。",
            "rows": [],
            "warning_count": 0,
            "data_source": "無持股",
        }

    updated = []
    rows = []
    warning_count = 0
    source_counter = {}

    for t in tracks:
        quote = None

        try:
            # 先用日K管理持股區間、移動停利與支撐
            df = download_stock(t.get("symbol"), HEARTBEAT_HOLDING_PERIOD)
            t = manage_holding(t, df)

            # 再用即時報價覆蓋目前價與損益
            quote = get_realtime_price(t.get("symbol"))
            if quote and quote.get("price"):
                curr = safe_float(quote.get("price"))
                entry = safe_float(t.get("price"))
                t["curr"] = curr
                t["pnl"] = round2(pct(curr, entry)) if entry else t.get("pnl", 0)
                t["realtime_source"] = quote.get("source", "-")
                t["realtime_time"] = quote.get("raw_time", "-")
                source_counter[t["realtime_source"]] = source_counter.get(t["realtime_source"], 0) + 1

        except Exception as e:
            t["heartbeat_error"] = str(e)

        level, action, icon, note = classify_holding_line_action(t)

        if level in ["A", "B"]:
            warning_count += 1

        updated.append(t)
        rows.append({
            "symbol": t.get("symbol"),
            "short_symbol": short_symbol(t.get("symbol", "")),
            "name": t.get("name", "-"),
            "level": level,
            "action": action,
            "icon": icon,
            "note": note,
            "curr": t.get("curr", "-"),
            "cost": t.get("price", "-"),
            "pnl": t.get("pnl", "-"),
            "stop": t.get("practical_stop", "-"),
            "support": t.get("support_price", "-"),
            "trail_range": t.get("trail_range", "-"),
            "source": t.get("realtime_source", "-"),
            "quote_time": t.get("realtime_time", "-"),
        })

    save_track(updated)

    src_text = "、".join([f"{k}:{v}" for k, v in source_counter.items()]) if source_counter else "無即時資料"
    summary = f"已檢查 {len(rows)} 檔持股，其中 {warning_count} 檔需注意。資料源：{src_text}"
    return {
        "count": len(rows),
        "summary": summary,
        "rows": rows,
        "warning_count": warning_count,
        "data_source": src_text,
    }

def format_heartbeat_line_report(h, holding_check):
    status = h.get("status", "-")
    status_label = h.get("status_label", status)

    icon = "✅"
    if status == "warning":
        icon = "⚠️"
    elif status == "danger":
        icon = "🚨"

    rows = [
        f"{icon} 系統心跳回報｜{status_label}",
        f"時間：{h.get('updated_at', taiwan_now())}",
        f"加權：{h.get('twii','-')}｜櫃買：{h.get('otc','-')}",
        f"資料源：{h.get('market_data_source','-')}",
        f"報價時間：{h.get('market_quote_time','-')}",
        f"掃描：{h.get('scan_status','-')}｜上次：{h.get('last_scan_time','-')}",
        f"LINE：{'正常' if h.get('line_ready') else '未綁定'}",
    ]

    if h.get("warnings"):
        rows.append("異常：" + "；".join(h.get("warnings", []))[:300])
    else:
        rows.append("狀態：目前未偵測到系統卡住或資料異常。")

    rows.append("")
    rows.append("📌 持股監控")
    rows.append(holding_check.get("summary", "目前沒有持股資料。"))

    for r in holding_check.get("rows", [])[:8]:
        rows.append(
            f"\n{r.get('icon','')} {r.get('name','-')} {r.get('short_symbol','')}"
            f"\n建議：{r.get('action','-')}｜級別：{r.get('level','-')}"
            f"\n現價：{r.get('curr','-')}｜成本：{r.get('cost','-')}｜損益：{r.get('pnl','-')}%"
            f"\n停損：{r.get('stop','-')}｜支撐：{r.get('support','-')}"
            f"\n資料源：{r.get('source','-')}｜時間：{r.get('quote_time','-')}"
            f"\n說明：{r.get('note','-')}"
        )

    rows.append("")
    rows.append("註：心跳只檢查大盤與已追蹤持股，不掃全市場。")
    return "\n".join(rows)


def maybe_push_heartbeat_line(h, holding_check, manual=False):
    if not HEARTBEAT_LINE_REPORT_ENABLED:
        return False, "HEARTBEAT_LINE_REPORT_ENABLED=0"

    if not get_line_token() or not get_line_user_id():
        return False, "LINE token 或 user id 未設定"

    text = format_heartbeat_line_report(h, holding_check)
    ok, info = push_line_message(text)

    logs = read_json(LINE_NOTIFY_LOG_FILE, [])
    logs.append({
        "mode": "heartbeat",
        "ok": ok,
        "info": info,
        "time": taiwan_now(),
        "manual": manual,
    })
    write_json(LINE_NOTIFY_LOG_FILE, logs[-100:])

    return ok, info


def market_heartbeat_check(manual=False):
    """
    輕量心跳檢查：
    不掃全市場，只抓大盤/櫃買、檢查掃描狀態、LINE綁定與資料新鮮度。
    若有已加入追蹤的持股，會同步做持股輕量檢查並用LINE回報。
    """
    warnings = []
    status_level = "ok"

    twii_quote = get_realtime_price("^TWII")
    otc_quote = get_realtime_price("^TWOII")
    twii = twii_quote.get("price") if twii_quote else "-"
    otc = otc_quote.get("price") if otc_quote else "-"
    market_data_source = f"加權:{twii_quote.get('source','-') if twii_quote else '-'}｜櫃買:{otc_quote.get('source','-') if otc_quote else '-'}"
    market_quote_time = f"加權:{twii_quote.get('raw_time','-') if twii_quote else '-'}｜櫃買:{otc_quote.get('raw_time','-') if otc_quote else '-'}"

    if twii == "-" or otc == "-":
        warnings.append("大盤或櫃買指數抓取失敗，資料源可能異常。")
        status_level = "warning"

    scan_status = load_scan_status()
    scan = load_scan_results()
    scan_time = scan.get("updated_at", "-")
    scan_ts = parse_taiwan_time(scan_time)

    if scan_status.get("status") == "running":
        ts = parse_taiwan_time(scan_status.get("updated_at"))
        if ts:
            minutes = (datetime.now(TZ) - ts).total_seconds() / 60
            if minutes > SCAN_TIMEOUT_MINUTES:
                warnings.append(f"掃描狀態卡在 running 超過 {SCAN_TIMEOUT_MINUTES} 分鐘。")
                status_level = "danger"
        else:
            warnings.append("掃描狀態為 running，但時間格式異常。")
            status_level = "warning"

    if not scan_ts:
        warnings.append("尚未有有效的全市場掃描時間。")
        status_level = "warning"
    else:
        hours = (datetime.now(TZ) - scan_ts).total_seconds() / 3600
        if hours > SCAN_DATA_STALE_HOURS:
            warnings.append(f"全市場掃描資料已超過 {SCAN_DATA_STALE_HOURS} 小時未更新。")
            status_level = "warning" if status_level != "danger" else status_level

    if not get_line_user_id():
        warnings.append("LINE User ID 尚未綁定，推播會失效。")
        status_level = "warning" if status_level != "danger" else status_level

    holding_check = {
        "count": 0,
        "summary": "持股心跳檢查未啟用。",
        "rows": [],
        "warning_count": 0,
    }

    if HEARTBEAT_HOLDING_CHECK_ENABLED:
        holding_check = heartbeat_holding_check()
        if holding_check.get("warning_count", 0) > 0 and status_level == "ok":
            status_level = "warning"

    if not warnings:
        message = "系統心跳正常：網站可執行、大盤資料可抓取、掃描狀態無卡住。"
    else:
        message = "；".join(warnings)

    data = {
        "status": status_level,
        "status_label": heartbeat_status_label(status_level),
        "message": message,
        "warnings": warnings,
        "twii": twii,
        "otc": otc,
        "market_data_source": market_data_source,
        "market_quote_time": market_quote_time,
        "last_scan_time": scan_time,
        "scan_status": scan_status.get("status", "-"),
        "scan_message": scan_status.get("message", "-"),
        "line_ready": bool(get_line_user_id()),
        "manual": manual,
        "rough_scan_top_n": ROUGH_SCAN_TOP_N,
        "loose_observation_limit": LOOSE_OBSERVATION_LIMIT,
        "holding_check": holding_check,
        "heartbeat_line_enabled": HEARTBEAT_LINE_REPORT_ENABLED,
        "heartbeat_holding_enabled": HEARTBEAT_HOLDING_CHECK_ENABLED,
    }

    save_heartbeat_status(data)
    record_schedule_health("market_heartbeat", status_level, message[:200])

    ok, info = maybe_push_heartbeat_line(data, holding_check, manual=manual)
    data["line_push_ok"] = ok
    data["line_push_info"] = str(info)[:300]
    save_heartbeat_status(data)

    return data

def heartbeat_summary_text():
    h = load_heartbeat_status()
    hc = h.get("holding_check", {})
    rows = [
        "系統心跳監控",
        f"狀態：{h.get('status_label', h.get('status','-'))}",
        f"更新時間：{h.get('updated_at','-')}",
        f"加權指數：{h.get('twii','-')}",
        f"櫃買指數：{h.get('otc','-')}",
        f"資料源：{h.get('market_data_source','-')}",
        f"報價時間：{h.get('market_quote_time','-')}",
        f"上次全市場掃描：{h.get('last_scan_time','-')}",
        f"掃描狀態：{h.get('scan_status','-')}｜{h.get('scan_message','-')}",
        f"LINE綁定：{'正常' if h.get('line_ready') else '未綁定'}",
        f"LINE心跳回報：{'開啟' if h.get('heartbeat_line_enabled') else '關閉'}",
        f"持股心跳檢查：{'開啟' if h.get('heartbeat_holding_enabled') else '關閉'}",
        f"輕量設定：細算 {h.get('rough_scan_top_n','-')} 檔｜寬鬆觀察 {h.get('loose_observation_limit','-')} 檔",
        "",
        f"說明：{h.get('message','-')}",
        "",
        "持股檢查：",
        f"{hc.get('summary','尚未檢查')}",
    ]

    for r in hc.get("rows", [])[:20]:
        rows.append(
            f"{r.get('name','-')} {r.get('short_symbol','')}｜"
            f"{r.get('action','-')}｜現價 {r.get('curr','-')}｜損益 {r.get('pnl','-')}%｜{r.get('source','-')}"
        )

    rows.append("")
    rows.append(f"最後LINE推播：{'成功' if h.get('line_push_ok') else '未成功/未推播'}｜{h.get('line_push_info','')}")
    return "\n".join(rows)

def load_ai_strategy_report():
    return read_json(AI_STRATEGY_REPORT_FILE, {
        "updated_at": "-",
        "status": "尚未產生",
        "model": OPENAI_MODEL,
        "report": "目前尚未產生 OpenAI 策略分析報告。",
        "samples": 0,
    })


def save_ai_strategy_report(data):
    data["updated_at"] = taiwan_now()
    write_json(AI_STRATEGY_REPORT_FILE, data)


def extract_openai_text(resp_json):
    if not isinstance(resp_json, dict):
        return ""

    if resp_json.get("output_text"):
        return resp_json.get("output_text", "")

    texts = []

    for item in resp_json.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict):
                if content.get("text"):
                    texts.append(content.get("text"))
                elif content.get("type") == "output_text" and content.get("text"):
                    texts.append(content.get("text"))

    return "\n".join(texts).strip()


def compact_signal_rows(limit=120):
    db = load_signal_database()
    signals = db.get("signals", [])
    entries = [x for x in signals if x.get("signal_type") == "entry"]
    rows = []

    for x in entries[-limit:]:
        result = x.get("result", {})
        rows.append({
            "date": x.get("date"),
            "symbol": x.get("short_symbol", x.get("symbol")),
            "name": x.get("name"),
            "level": x.get("level"),
            "buy_type": x.get("buy_type"),
            "sector": x.get("sector"),
            "risk_reward": x.get("risk_reward"),
            "confidence": x.get("confidence"),
            "price": x.get("price"),
            "entry_low": x.get("entry_low"),
            "entry_high": x.get("entry_high"),
            "stop": x.get("stop"),
            "market_status": x.get("market_status"),
            "return_3d_pct": result.get("return_3d_pct"),
            "return_5d_pct": result.get("return_5d_pct"),
            "return_10d_pct": result.get("return_10d_pct"),
            "max_up_5d_pct": result.get("max_up_5d_pct"),
            "max_down_5d_pct": result.get("max_down_5d_pct"),
            "hit_stop": result.get("hit_stop"),
            "hit_target": result.get("hit_target"),
        })

    return rows


def build_ai_strategy_payload():
    db = load_signal_database()
    signals = db.get("signals", [])
    entries = [x for x in signals if x.get("signal_type") == "entry"]
    report = {
        "version": APP_VERSION_NAME,
        "generated_at": taiwan_now(),
        "signal_summary": signal_summary_text(),
        "optimization_summary": optimization_summary_text(),
        "strategy_weights": load_strategy_weights(),
        "latest_entry_signals": compact_signal_rows(120),
        "total_entry_signals": len(entries),
        "instruction": (
            "請站在中立、保守、風控優先的角度分析這套台股AI交易助理。"
            "重點判斷：目前樣本是否足夠、哪些買點/族群/風報比/等級看起來較有效、哪些應降權重、"
            "是否有過度擬合風險、下一階段應該觀察什麼。"
            "請不要保證獲利，不要叫使用者重倉。"
        ),
    }
    return report


def generate_openai_strategy_report(manual=False):
    if not OPENAI_ANALYSIS_ENABLED:
        data = {
            "status": "disabled",
            "model": OPENAI_MODEL,
            "samples": 0,
            "report": "OPENAI_ANALYSIS_ENABLED 未開啟，目前不產生 OpenAI 分析報告。",
        }
        save_ai_strategy_report(data)
        return data

    payload = build_ai_strategy_payload()
    samples = safe_int(payload.get("total_entry_signals", 0))

    if not OPENAI_API_KEY:
        data = {
            "status": "missing_api_key",
            "model": OPENAI_MODEL,
            "samples": samples,
            "report": (
                "尚未設定 OPENAI_API_KEY。\n"
                "請到 Railway Variables 新增 OPENAI_API_KEY 後重新部署。\n"
                "設定完成後，系統會在每日盤後自動產生策略分析報告。"
            ),
        }
        save_ai_strategy_report(data)
        return data

    system_prompt = (
        "你是一位保守、重視風險控管與統計驗證的台股策略分析師。"
        "你只負責分析資料與提出可驗證的優化建議，不提供保證獲利承諾。"
        "請用繁體中文輸出，格式清楚，適合使用者之後貼給ChatGPT再進一步討論。"
    )

    user_prompt = (
        "以下是我的AI交易助理系統累積資料，請產出一份策略分析報告。\n\n"
        "請分成：\n"
        "1. 目前樣本數與可信度\n"
        "2. 目前看起來較有效的邏輯\n"
        "3. 目前看起來較弱或應降低權重的邏輯\n"
        "4. 停損、停利、風報比觀察\n"
        "5. 是否有過度擬合風險\n"
        "6. 建議下一階段優化方向\n"
        "7. 給使用者的保守結論\n\n"
        "資料如下：\n"
        + json.dumps(payload, ensure_ascii=False, default=str)[:60000]
    )

    try:
        res = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_output_tokens": 2200,
            },
            timeout=60,
        )

        if res.status_code >= 400:
            data = {
                "status": "error",
                "model": OPENAI_MODEL,
                "samples": samples,
                "report": f"OpenAI API 呼叫失敗：HTTP {res.status_code}\n{res.text[:1000]}",
            }
            save_ai_strategy_report(data)
            return data

        resp_json = res.json()
        text = extract_openai_text(resp_json)

        if not text:
            text = "OpenAI API 有回應，但沒有解析到文字內容。請檢查回傳格式。"

        data = {
            "status": "ok",
            "model": OPENAI_MODEL,
            "samples": samples,
            "manual": manual,
            "report": text,
        }
        save_ai_strategy_report(data)
        return data

    except Exception as e:
        data = {
            "status": "error",
            "model": OPENAI_MODEL,
            "samples": samples,
            "report": f"產生 OpenAI 策略分析報告失敗：{e}",
        }
        save_ai_strategy_report(data)
        return data




@app.route("/ai-report")
def ai_report():
    data = load_ai_strategy_report()
    text = (
        f"OpenAI策略分析報告\n"
        f"狀態：{data.get('status','-')}\n"
        f"模型：{data.get('model','-')}\n"
        f"樣本數：{data.get('samples','-')}\n"
        f"更新時間：{data.get('updated_at','-')}\n"
        f"\n{data.get('report','')}"
    )
    return Response(text, mimetype="text/plain; charset=utf-8")


@app.route("/ai-report-json")
def ai_report_json():
    return load_ai_strategy_report()


@app.route("/generate-ai-report")
def generate_ai_report_route():
    generate_openai_strategy_report(manual=True)
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
        "openai_analysis": OPENAI_ANALYSIS_ENABLED,
        "openai_ready": bool(OPENAI_API_KEY),
        "openai_model": OPENAI_MODEL,
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



@app.route("/loose-signal-summary")
def loose_signal_summary():
    return Response(signal_summary_text(), mimetype="text/plain; charset=utf-8")


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




def yf_info(symbol):
    """
    從 yfinance 抓基本面資料。
    注意：台股基本面資料可能不完整，所以所有欄位都要允許缺值。
    """
    try:
        return yf.Ticker(symbol).info or {}
    except Exception as e:
        print("yf_info failed", symbol, e)
        return {}


def value_bucket_upside(upside):
    u = safe_float(upside, 0)
    if u >= 200:
        return "潛在空間>200%"
    if u >= 150:
        return "潛在空間150~200%"
    if u >= 100:
        return "潛在空間100~150%"
    if u >= 50:
        return "潛在空間50~100%"
    return "潛在空間不足"


def undervalue_bucket(pe, fair_pe):
    pe = safe_float(pe, 0)
    fair_pe = safe_float(fair_pe, 0)
    if not pe or not fair_pe:
        return "估值資料不足"
    ratio = pe / fair_pe
    if ratio <= 0.55:
        return "明顯低估"
    if ratio <= 0.75:
        return "低估"
    if ratio <= 1:
        return "合理偏低"
    if ratio <= 1.25:
        return "合理偏高"
    return "偏貴"


def estimate_fair_pe(info, industry=""):
    """
    估算合理本益比，不追求精準，只做相對估值。
    產業與獲利能力較強給較高估值，財務/成長弱則降低。
    """
    gross = safe_float(info.get("grossMargins", 0)) * 100
    opm = safe_float(info.get("operatingMargins", 0)) * 100
    roe = safe_float(info.get("returnOnEquity", 0)) * 100
    revenue_growth = safe_float(info.get("revenueGrowth", 0)) * 100
    earnings_growth = safe_float(info.get("earningsGrowth", 0)) * 100

    pe = 12
    if gross >= 35:
        pe += 5
    if opm >= 15:
        pe += 4
    if roe >= 15:
        pe += 4
    if revenue_growth >= 20:
        pe += 4
    if earnings_growth >= 25:
        pe += 5

    hot_keywords = ["半導體", "AI", "伺服", "散熱", "電源", "電子", "光電", "通訊", "資訊", "雲端", "車用", "航太", "材料"]
    if any(k in str(industry) for k in hot_keywords):
        pe += 3

    return max(8, min(pe, 35))


def score_financial_safety(info):
    score = 0
    notes = []

    debt = safe_float(info.get("debtToEquity", 0))
    current_ratio = safe_float(info.get("currentRatio", 0))
    op_cash = safe_float(info.get("operatingCashflow", 0))
    free_cash = safe_float(info.get("freeCashflow", 0))
    total_cash = safe_float(info.get("totalCash", 0))
    total_debt = safe_float(info.get("totalDebt", 0))

    if debt and debt < 80:
        score += 4
        notes.append("負債合理")
    elif debt and debt > 150:
        score -= 4
        notes.append("負債偏高")
    else:
        score += 2

    if current_ratio and current_ratio >= 1.5:
        score += 3
        notes.append("流動性尚可")
    elif current_ratio and current_ratio < 1:
        score -= 3
        notes.append("流動比率偏低")
    else:
        score += 1

    if op_cash and op_cash > 0:
        score += 4
        notes.append("營業現金流為正")
    else:
        score -= 3
        notes.append("營業現金流不足")

    if free_cash and free_cash > 0:
        score += 2
    if total_cash and total_debt and total_cash > total_debt * 0.5:
        score += 2

    return max(0, min(15, round2(score))), notes


def score_profit_quality(info):
    score = 0
    notes = []

    eps = safe_float(info.get("trailingEps", 0))
    gross = safe_float(info.get("grossMargins", 0)) * 100
    opm = safe_float(info.get("operatingMargins", 0)) * 100
    npm = safe_float(info.get("profitMargins", 0)) * 100
    roe = safe_float(info.get("returnOnEquity", 0)) * 100
    earnings_growth = safe_float(info.get("earningsGrowth", 0)) * 100

    if eps > 0:
        score += 3
        notes.append("EPS為正")
    if earnings_growth >= 25:
        score += 4
        notes.append("EPS成長強")
    elif earnings_growth >= 10:
        score += 2

    if gross >= 35:
        score += 3
        notes.append("毛利率高")
    elif gross >= 20:
        score += 2

    if opm >= 15:
        score += 3
        notes.append("營益率佳")
    elif opm >= 8:
        score += 2

    if npm >= 10:
        score += 1
    if roe >= 15:
        score += 2
        notes.append("ROE佳")
    elif roe >= 10:
        score += 1

    return max(0, min(15, round2(score))), notes


def score_undervaluation(info, price, industry=""):
    score = 0
    notes = []

    pe = safe_float(info.get("trailingPE", 0)) or safe_float(info.get("forwardPE", 0))
    pb = safe_float(info.get("priceToBook", 0))
    eps = safe_float(info.get("trailingEps", 0))
    earnings_growth = safe_float(info.get("earningsGrowth", 0)) * 100
    fair_pe = estimate_fair_pe(info, industry)

    if eps <= 0 or price <= 0:
        return 0, "估值資料不足", 0, 0, 0, ["EPS或股價資料不足"]

    fair_value = round2(eps * fair_pe)
    upside = round2(pct(fair_value, price))

    if pe:
        if pe <= fair_pe * 0.55:
            score += 8
            notes.append("本益比明顯低於合理區")
        elif pe <= fair_pe * 0.75:
            score += 6
            notes.append("本益比偏低")
        elif pe <= fair_pe:
            score += 4
            notes.append("估值合理偏低")
        elif pe > fair_pe * 1.5:
            score -= 5
            notes.append("本益比偏高")

    if pb and pb <= 1.5:
        score += 3
        notes.append("股價淨值比偏低")
    elif pb and pb > 5:
        score -= 2
        notes.append("股價淨值比偏高")

    if upside >= 150:
        score += 7
        notes.append("潛在空間很大")
    elif upside >= 100:
        score += 5
        notes.append("潛在空間達翻倍")
    elif upside >= 50:
        score += 3
    else:
        score -= 3
        notes.append("潛在空間不足")

    if pe and earnings_growth > 0:
        peg = pe / earnings_growth
        if peg < 1:
            score += 2
            notes.append("PEG合理")
        elif peg > 2:
            score -= 2
            notes.append("PEG偏高")

    return max(0, min(20, round2(score))), undervalue_bucket(pe, fair_pe), fair_pe, fair_value, upside, notes


def score_catalyst(info):
    score = 0
    notes = []

    revenue_growth = safe_float(info.get("revenueGrowth", 0)) * 100
    earnings_growth = safe_float(info.get("earningsGrowth", 0)) * 100
    gross = safe_float(info.get("grossMargins", 0)) * 100
    opm = safe_float(info.get("operatingMargins", 0)) * 100

    if revenue_growth >= 30:
        score += 6
        notes.append("營收成長強")
    elif revenue_growth >= 15:
        score += 4
        notes.append("營收成長")
    elif revenue_growth > 0:
        score += 2
    else:
        score -= 3
        notes.append("營收未成長")

    if earnings_growth >= 50:
        score += 7
        notes.append("EPS成長強")
    elif earnings_growth >= 20:
        score += 5
        notes.append("EPS成長")
    elif earnings_growth > 0:
        score += 2

    if gross >= 30 and opm >= 10:
        score += 3
        notes.append("獲利結構支持重估")

    # yfinance不一定有月營收/法說資訊，這裡先用財報成長代理催化劑
    if revenue_growth > 10 and earnings_growth > 10:
        score += 4
        notes.append("營收與獲利同步轉強")

    return max(0, min(20, round2(score))), notes


def score_industry_trend(industry, sector):
    text = f"{industry} {sector}"
    hot = {
        "半導體": 10, "Semiconductor": 10,
        "AI": 10, "伺服": 9, "Server": 9,
        "散熱": 8, "電源": 8, "高速": 8,
        "電子": 7, "Technology": 7,
        "通訊": 7, "車用": 7,
        "航太": 7, "材料": 6,
        "醫療": 6, "Healthcare": 6,
    }
    best = 4
    for k, s in hot.items():
        if k in text:
            best = max(best, s)
    return min(10, best)


def score_moat(info, industry=""):
    score = 0
    notes = []

    gross = safe_float(info.get("grossMargins", 0)) * 100
    opm = safe_float(info.get("operatingMargins", 0)) * 100
    market_cap = safe_float(info.get("marketCap", 0))
    roe = safe_float(info.get("returnOnEquity", 0)) * 100

    if gross >= 40:
        score += 3
        notes.append("高毛利，可能具備定價權")
    elif gross >= 25:
        score += 2

    if opm >= 15:
        score += 2
        notes.append("營益率佳")
    if roe >= 15:
        score += 2
        notes.append("資本效率佳")

    if market_cap >= 50_000_000_000:
        score += 2
        notes.append("具一定市場地位")
    elif market_cap >= 10_000_000_000:
        score += 1

    # 文字無法精準判斷護城河，保守評分
    if any(k in str(industry) for k in ["半導體", "材料", "醫療", "特殊", "伺服"]):
        score += 1

    return max(0, min(10, round2(score))), notes


def score_funding(info, df):
    # 不再做技術分析，只用成交值/市值流動性當資料可信度與資金認同輔助
    score = 2
    notes = ["資金面僅輔助，不作為主策略"]
    try:
        if df is not None and len(df) >= 20:
            avg_amt = safe_float((df["Close"] * df["Volume"]).rolling(20).mean().iloc[-1])
            if avg_amt >= 100_000_000:
                score += 3
                notes.append("成交金額足夠")
            elif avg_amt >= 30_000_000:
                score += 2
            elif avg_amt < 5_000_000:
                score -= 2
                notes.append("流動性不足")
    except Exception:
        pass
    return max(0, min(5, round2(score))), notes


def score_governance(info):
    # yfinance沒有台灣公司治理評鑑，先用保守中性分數；重大風險需後續接公開資訊觀測站
    return 3, ["公司治理資料需後續接公開資訊觀測站"]


def value_risk_deductions(info):
    deduct = 0
    notes = []

    pe = safe_float(info.get("trailingPE", 0)) or safe_float(info.get("forwardPE", 0))
    eps = safe_float(info.get("trailingEps", 0))
    revenue_growth = safe_float(info.get("revenueGrowth", 0)) * 100
    earnings_growth = safe_float(info.get("earningsGrowth", 0)) * 100
    gross = safe_float(info.get("grossMargins", 0)) * 100
    op_cash = safe_float(info.get("operatingCashflow", 0))

    if eps <= 0:
        deduct += 15
        notes.append("EPS非正數")
    if revenue_growth < -10:
        deduct += 8
        notes.append("營收明顯衰退")
    if earnings_growth < -10:
        deduct += 8
        notes.append("EPS成長轉弱")
    if gross and gross < 10:
        deduct += 5
        notes.append("毛利率偏低")
    if pe and pe > 60 and earnings_growth < 30:
        deduct += 10
        notes.append("估值高但成長不足")
    if op_cash and op_cash < 0:
        deduct += 8
        notes.append("營業現金流為負")

    return min(30, deduct), notes



def value_index_regime(symbol):
    try:
        df = download_stock(symbol, "1y")
        if df is None or len(df) < 220:
            return {"status": "資料不足", "score": 0, "note": "指數資料不足"}
        c, h, l = df["Close"], df["High"], df["Low"]
        price = safe_float(c.iloc[-1])
        ma20 = safe_float(c.rolling(20).mean().iloc[-1])
        ma60 = safe_float(c.rolling(60).mean().iloc[-1])
        ma120 = safe_float(c.rolling(120).mean().iloc[-1])
        ma200 = safe_float(c.rolling(200).mean().iloc[-1])
        high60_prev = safe_float(h.iloc[-61:-1].max())
        low60_prev = safe_float(l.iloc[-61:-1].min())
        ch20 = pct(c.iloc[-1], c.iloc[-20])
        ch60 = pct(c.iloc[-1], c.iloc[-60])
        score, notes = 0, []
        if price > ma20 > ma60 > ma120:
            score += 35; notes.append("短中期多頭排列")
        elif price > ma60 and ma20 > ma60:
            score += 20; notes.append("中期偏多")
        elif price < ma60:
            score -= 20; notes.append("跌破季線")
        if ma200 and price > ma200:
            score += 15; notes.append("站上年線")
        elif ma200 and price < ma200:
            score -= 20; notes.append("跌破年線")
        if high60_prev and price > high60_prev:
            score += 15; notes.append("突破60日高點")
        if low60_prev and price < low60_prev:
            score -= 25; notes.append("跌破60日低點")
        if ch20 <= -8:
            score -= 15; notes.append("20日跌幅偏大")
        if ch60 <= -15:
            score -= 20; notes.append("60日跌幅偏大")
        if ch20 >= 5 and ch60 >= 8:
            score += 10; notes.append("動能偏多")
        if score >= 45: status = "強多"
        elif score >= 20: status = "多頭回檔"
        elif score >= -10: status = "盤整震盪"
        elif score >= -35: status = "轉弱"
        else: status = "空頭"
        return {"status": status, "score": round2(score), "note": "、".join(notes[:5]) if notes else "無明顯訊號"}
    except Exception as e:
        print("value_index_regime failed", symbol, e)
        return {"status": "資料不足", "score": 0, "note": str(e)}


def value_market_regime():
    twii = value_index_regime("^TWII")
    otc = value_index_regime("^TWOII")
    score = safe_float(twii.get("score", 0)) + safe_float(otc.get("score", 0)) * 0.45
    if score >= 55:
        status, policy, allow, mult, note = "強多", "正常買進", True, 1.0, "大盤強勢，價值股若符合條件可正常分批。"
    elif score >= 25:
        status, policy, allow, mult, note = "多頭回檔", "保守買進", True, 0.5, "大盤仍偏多但有修正，僅推高分低估股。"
    elif score >= -10:
        status, policy, allow, mult, note = "盤整震盪", "只觀察", False, 0.0, "大盤震盪，不主動推買進，只收集觀察。"
    elif score >= -35:
        status, policy, allow, mult, note = "轉弱", "停止買進", False, 0.0, "大盤轉弱，停止買進提醒，只做持股監控。"
    else:
        status, policy, allow, mult, note = "空頭", "停止買進", False, 0.0, "空頭環境，只提醒停利/賣出，不提醒買進。"
    return {
        "value_market_status": status, "value_market_policy": policy,
        "value_market_score": round2(score), "value_allow_buy": allow,
        "value_position_multiplier": mult, "value_market_note": note,
        "value_twii_status": twii.get("status", "-"), "value_otc_status": otc.get("status", "-"),
        "value_twii_note": twii.get("note", "-"), "value_otc_note": otc.get("note", "-"),
    }


def technical_entry_auxiliary(df, price=0):
    out = {
        "technical_entry_score": 0, "technical_entry_status": "資料不足",
        "technical_entry_pass": False, "technical_entry_notes": [],
        "entry_zone_low": "-", "entry_zone_high": "-",
        "near_pressure_price": "-", "near_pressure_distance_pct": "-",
        "support_reference": "-",
    }
    try:
        if df is None or len(df) < 220:
            return out
        c, h, l, o, v = df["Close"], df["High"], df["Low"], df["Open"], df["Volume"]
        price = safe_float(price) or safe_float(c.iloc[-1])
        op, hi, lo, vol = safe_float(o.iloc[-1]), safe_float(h.iloc[-1]), safe_float(l.iloc[-1]), safe_float(v.iloc[-1])
        ma20 = safe_float(c.rolling(20).mean().iloc[-1])
        ma60 = safe_float(c.rolling(60).mean().iloc[-1])
        ma120 = safe_float(c.rolling(120).mean().iloc[-1])
        ma200 = safe_float(c.rolling(200).mean().iloc[-1])
        avg_vol20 = safe_float(v.rolling(20).mean().iloc[-1])
        high60_prev = safe_float(h.iloc[-61:-1].max())
        high120_prev = safe_float(h.iloc[-121:-1].max())
        ch20 = pct(c.iloc[-1], c.iloc[-20])
        score, notes = 0, []
        if price > ma20 > ma60:
            score += 18; notes.append("站上20/60日均線")
        elif price > ma60:
            score += 10; notes.append("站上60日均線")
        elif price < ma60:
            score -= 18; notes.append("跌破60日均線")
        if ma200 and price > ma200:
            score += 10; notes.append("站上年線")
        elif ma200 and price < ma200:
            score -= 15; notes.append("跌破年線")
        if ch20 > 25:
            score -= 18; notes.append("20日漲幅過熱")
        elif ch20 < -20:
            score -= 10; notes.append("短期跌勢過強")
        elif -8 <= ch20 <= 12:
            score += 8; notes.append("短期位置尚可")
        pressures = [x for x in [high60_prev, high120_prev] if x and x > price]
        near_pressure = min(pressures) if pressures else 0
        if near_pressure:
            dist = round2(pct(near_pressure, price))
            out["near_pressure_price"] = round2(near_pressure)
            out["near_pressure_distance_pct"] = dist
            if dist < 5:
                score -= 15; notes.append("前方壓力小於5%")
            elif dist >= 10:
                score += 8; notes.append("上方空間尚可")
        else:
            score += 10; notes.append("短中期前方壓力較少")
        supports = [x for x in [ma20, ma60, ma120, ma200] if x and x < price]
        support = max(supports) if supports else 0
        if support:
            support_dist = pct(price, support)
            out["support_reference"] = round2(support)
            if support_dist <= 5:
                score += 12; notes.append("靠近支撐，利於分批")
            elif support_dist > 15:
                score -= 10; notes.append("距支撐過遠")
        full = max(hi - lo, 0.000001)
        upper_shadow = hi - max(price, op)
        if upper_shadow / full > 0.45:
            score -= 12; notes.append("長上影遇壓")
        elif price > op:
            score += 6; notes.append("紅K收盤")
        vol_ratio = vol / avg_vol20 if avg_vol20 else 0
        if 0.8 <= vol_ratio <= 2.5:
            score += 6; notes.append("量能健康")
        elif vol_ratio > 4:
            score -= 8; notes.append("爆量波動偏大")
        entry_low = round2(max(support, price * 0.97)) if support else round2(price * 0.97)
        entry_high = round2(price * 1.03)
        if score >= VALUE_CAUTION_TECH_SCORE:
            status, passed = "進場條件佳", True
        elif score >= VALUE_MIN_TECH_SCORE:
            status, passed = "可保守分批", True
        elif score >= 35:
            status, passed = "等待更好進場點", False
        else:
            status, passed = "技術面偏弱，暫不買", False
        out.update({
            "technical_entry_score": round2(score), "technical_entry_status": status,
            "technical_entry_pass": passed, "technical_entry_notes": notes[:8],
            "entry_zone_low": entry_low, "entry_zone_high": entry_high,
        })
        return out
    except Exception as e:
        print("technical_entry_auxiliary failed", e)
        out["technical_entry_notes"] = [str(e)]
        return out


def analyze_value_stock(symbol, name="-", industry="-", df=None):
    """
    價值分析模型：
    不使用K棒/突破/均線做買賣核心。
    用低估、財務安全、獲利品質、重估催化劑、產業趨勢、護城河判斷。
    """
    info = yf_info(symbol)
    if not info:
        return None

    if df is None:
        df = download_stock(symbol, "1y")

    price = 0
    try:
        price = safe_float(info.get("currentPrice", 0)) or safe_float(info.get("regularMarketPrice", 0))
        if not price and df is not None and not df.empty:
            price = safe_float(df["Close"].iloc[-1])
    except Exception:
        price = 0

    if not price:
        return None

    info_industry = info.get("industry") or industry or "-"
    info_sector = info.get("sector") or "-"

    financial_safety_score, safety_notes = score_financial_safety(info)
    profit_quality_score, profit_notes = score_profit_quality(info)
    undervalue_score, undervalue_status, fair_pe, fair_value, upside_pct, undervalue_notes = score_undervaluation(info, price, info_industry)
    catalyst_score, catalyst_notes = score_catalyst(info)
    industry_score = score_industry_trend(info_industry, info_sector)
    moat_score, moat_notes = score_moat(info, info_industry)
    funding_score, funding_notes = score_funding(info, df)
    governance_score, governance_notes = score_governance(info)
    risk_deduct, risk_notes = value_risk_deductions(info)

    total = round2(
        financial_safety_score
        + profit_quality_score
        + undervalue_score
        + catalyst_score
        + industry_score
        + moat_score
        + funding_score
        + governance_score
        - risk_deduct
    )

    eps = safe_float(info.get("trailingEps", 0))
    pe = safe_float(info.get("trailingPE", 0)) or safe_float(info.get("forwardPE", 0))
    pb = safe_float(info.get("priceToBook", 0))
    revenue_growth = round2(safe_float(info.get("revenueGrowth", 0)) * 100)
    earnings_growth = round2(safe_float(info.get("earningsGrowth", 0)) * 100)
    gross_margin = round2(safe_float(info.get("grossMargins", 0)) * 100)
    operating_margin = round2(safe_float(info.get("operatingMargins", 0)) * 100)
    roe = round2(safe_float(info.get("returnOnEquity", 0)) * 100)
    market_cap = safe_float(info.get("marketCap", 0))

    tech = technical_entry_auxiliary(df, price) if VALUE_REQUIRE_TECH_ENTRY else {
        "technical_entry_score": 100,
        "technical_entry_status": "未啟用",
        "technical_entry_pass": True,
        "technical_entry_notes": [],
        "entry_zone_low": round2(price),
        "entry_zone_high": round2(price * 1.03),
        "near_pressure_price": "-",
        "near_pressure_distance_pct": "-",
        "support_reference": "-",
    }

    hard_exclude = False
    exclude_reasons = []

    if eps <= 0:
        hard_exclude = True
        exclude_reasons.append("EPS非正數")
    if financial_safety_score < 5:
        hard_exclude = True
        exclude_reasons.append("財務安全分數過低")
    if profit_quality_score < 5:
        hard_exclude = True
        exclude_reasons.append("獲利品質分數過低")
    if risk_deduct >= 20:
        hard_exclude = True
        exclude_reasons.append("風險扣分過高")

    value_buy_candidate = (
        total >= VALUE_SCORE_BUY
        and financial_safety_score >= 10
        and profit_quality_score >= 10
        and undervalue_score >= VALUE_MIN_UNDERVALUE_SCORE
        and catalyst_score >= VALUE_MIN_CATALYST_SCORE
        and upside_pct >= VALUE_MIN_UPSIDE_PCT
        and price < fair_value
    )

    if hard_exclude:
        action = "排除"
        level = "D"
    elif value_buy_candidate and tech.get("technical_entry_pass", False):
        action = "買進提醒"
        level = "S"
    elif value_buy_candidate:
        action = "等待進場點"
        level = "S"
    elif total >= VALUE_SCORE_WATCH:
        action = "只觀察"
        level = "A"
    elif total >= 65:
        action = "只觀察"
        level = "B"
    else:
        action = "排除"
        level = "C"

    notes = []
    notes.extend(safety_notes[:3])
    notes.extend(profit_notes[:3])
    notes.extend(undervalue_notes[:3])
    notes.extend(catalyst_notes[:3])
    notes.extend(moat_notes[:2])
    notes.extend(risk_notes[:4])

    return {
        "symbol": symbol,
        "name": name,
        "industry": info_industry,
        "sector": info_sector,
        "price": round2(price),
        "day_close": round2(price),
        "level": level,
        "action": action,
        "current_status": action,
        "execution_action": action,
        "buy_type": "價值分析",
        "value_model_version": VALUE_MODEL_VERSION,
        "score": total,
        "financial_safety_score": financial_safety_score,
        "profit_quality_score": profit_quality_score,
        "undervalue_score": undervalue_score,
        "catalyst_score": catalyst_score,
        "industry_score": industry_score,
        "moat_score": moat_score,
        "funding_score": funding_score,
        "governance_score": governance_score,
        "risk_deduct": risk_deduct,
        "eps": round2(eps),
        "pe": round2(pe),
        "pb": round2(pb),
        "fair_pe": round2(fair_pe),
        "fair_value": round2(fair_value),
        "fair_value_low": round2(fair_value * 0.8),
        "fair_value_high": round2(fair_value * 1.25),
        "upside_pct": round2(upside_pct),
        "upside_bucket": value_bucket_upside(upside_pct),
        "undervalue_status": undervalue_status,
        "undervalue_bucket": undervalue_status,
        "revenue_growth_pct": revenue_growth,
        "earnings_growth_pct": earnings_growth,
        "gross_margin_pct": gross_margin,
        "operating_margin_pct": operating_margin,
        "roe_pct": roe,
        "market_cap": round2(market_cap),
        "risk_notes": risk_notes,
        "value_notes": notes,
        "exclude_reasons": exclude_reasons,
        "target_price": round2(fair_value),
        "next_entry_low": round2(price),
        "next_entry_high": round2(min(price * 1.03, fair_value)),
        "no_entry_price": round2(price * 0.85),
        "practical_stop": 0,
        "risk_reward": 0,
        "risk_reward_note": "價值分析版不以技術停損為主，依基本面轉弱或估值過高出場",
        "trade_plan_note": "可分批買進；買進後監控基本面、估值與買進理由是否破壞。",
        "ai_next_action": action,
        "technical_entry_score": tech.get("technical_entry_score", 0),
        "technical_entry_status": tech.get("technical_entry_status", "-"),
        "technical_entry_pass": tech.get("technical_entry_pass", False),
        "technical_entry_notes": tech.get("technical_entry_notes", []),
        "entry_zone_low": tech.get("entry_zone_low", "-"),
        "entry_zone_high": tech.get("entry_zone_high", "-"),
        "near_pressure_price": tech.get("near_pressure_price", "-"),
        "near_pressure_distance_pct": tech.get("near_pressure_distance_pct", "-"),
        "support_reference": tech.get("support_reference", "-"),
    }


def value_sell_check_for_holding(track):
    """
    持有監控：
    買進後只檢查基本面轉弱、估值過高、原始邏輯破壞。
    """
    symbol = track.get("symbol")
    name = track.get("name", symbol)
    item = analyze_value_stock(symbol, name=name, industry=track.get("industry", "-"))
    if not item:
        return {"alert": False, "type": "持續持有", "reason": "基本面資料不足，暫不動作", "item": track}

    reasons = []
    alert_type = "持續持有"

    if item.get("risk_deduct", 0) >= 20 or item.get("financial_safety_score", 0) < 5 or item.get("profit_quality_score", 0) < 5:
        alert_type = "賣出提醒"
        reasons.append("基本面轉弱或風險扣分過高")

    if item.get("upside_pct", 0) < 20 and item.get("price", 0) >= item.get("fair_value", 0):
        alert_type = "停利提醒"
        reasons.append("估值偏高，潛在空間不足")

    if item.get("catalyst_score", 0) < 6 and item.get("undervalue_score", 0) < 8:
        alert_type = "賣出提醒"
        reasons.append("原始低估重估邏輯轉弱")

    if not reasons:
        reasons.append("基本面與估值尚未觸發出場條件")

    return {
        "alert": alert_type in ["賣出提醒", "停利提醒"],
        "type": alert_type,
        "reason": "；".join(reasons),
        "item": item,
    }


def record_value_signal(signal_type, item, note=""):
    price = safe_float(item.get("price", 0)) or safe_float(item.get("day_close", 0))
    if not price:
        return

    record_ai_signal(
        signal_type,
        item,
        item.get("level", "-"),
        signal_type,
        price,
        note or "價值分析訊號",
        extra={
            "strategy_version": VALUE_MODEL_VERSION,
            "value_model_version": VALUE_MODEL_VERSION,
            "financial_safety_score": item.get("financial_safety_score", 0),
            "profit_quality_score": item.get("profit_quality_score", 0),
            "undervalue_score": item.get("undervalue_score", 0),
            "catalyst_score": item.get("catalyst_score", 0),
            "industry_score": item.get("industry_score", 0),
            "moat_score": item.get("moat_score", 0),
            "risk_deduct": item.get("risk_deduct", 0),
            "eps": item.get("eps", "-"),
            "pe": item.get("pe", "-"),
            "fair_value": item.get("fair_value", "-"),
            "upside_pct": item.get("upside_pct", "-"),
            "upside_bucket": item.get("upside_bucket", "-"),
            "undervalue_bucket": item.get("undervalue_bucket", "-"),
            "industry": item.get("industry", "-"),
            "revenue_growth_pct": item.get("revenue_growth_pct", "-"),
            "earnings_growth_pct": item.get("earnings_growth_pct", "-"),
            "gross_margin_pct": item.get("gross_margin_pct", "-"),
            "operating_margin_pct": item.get("operating_margin_pct", "-"),
            "roe_pct": item.get("roe_pct", "-"),
            "risk_notes": item.get("risk_notes", []),
            "value_notes": item.get("value_notes", []),
            "technical_entry_score": item.get("technical_entry_score", "-"),
            "technical_entry_status": item.get("technical_entry_status", "-"),
            "near_pressure_distance_pct": item.get("near_pressure_distance_pct", "-"),
            "support_reference": item.get("support_reference", "-"),
        },
    )


def update_value_candidate_pool(items):
    now = taiwan_now()
    today = today_str()
    old = read_json(CANDIDATE_FILE, {"candidates": {}}).get("candidates", {})
    new = {}

    for item in items:
        sym = item.get("symbol")
        if not sym:
            continue
        prev = old.get(sym, {})
        item["first_seen"] = prev.get("first_seen", today)
        item["last_seen"] = today
        item["updated_at"] = now
        item["previous_status"] = prev.get("current_status", "-")
        new[sym] = item

    new = dict(sorted(
        new.items(),
        key=lambda kv: (
            kv[1].get("current_status") == "買進提醒",
            kv[1].get("score", 0),
            kv[1].get("upside_pct", 0),
            kv[1].get("undervalue_score", 0),
        ),
        reverse=True,
    ))

    alerts = [x for x in new.values() if x.get("current_status") == "買進提醒"][:VALUE_MAX_ALERTS]
    data = {"updated_at": now, "candidates": new, "entry_alerts": alerts}
    write_json(CANDIDATE_FILE, data)
    return data


def scan_market():
    save_scan_status("running", "價值分析：正在建立全市場股票池。")

    stocks = get_stock_pool()
    market = market_status()
    value_mkt = value_market_regime()
    total = len(stocks)

    save_scan_status("running", f"股票池建立完成：{total} 檔。大盤：{value_mkt.get('value_market_status')}，開始低估重估潛力股掃描。")

    rough = []
    # 粗篩：先用近一年流動性與價格資料排除明顯不適合者，避免每檔都抓基本面造成卡住。
    for i, (sym, info) in enumerate(stocks.items(), 1):
        try:
            name = info.get("name", sym)
            industry = info.get("industry", "其他")
            df = download_stock(sym, "1y")
            if df is None or len(df) < 60:
                continue

            price = safe_float(df["Close"].iloc[-1])
            avg_amt = safe_float((df["Close"] * df["Volume"]).rolling(20).mean().iloc[-1])
            ch120 = pct(df["Close"].iloc[-1], df["Close"].iloc[-120]) if len(df) >= 120 else 0

            # 價值分析不是技術分析，這裡只用來排除太冷門、資料不足、流動性過差的股票。
            if avg_amt < 3_000_000:
                continue

            rough_score = 0
            if avg_amt >= 30_000_000:
                rough_score += 10
            if price >= 10:
                rough_score += 5
            if ch120 > -40:
                rough_score += 5

            rough.append({
                "symbol": sym,
                "name": name,
                "industry": industry,
                "rough_score": rough_score,
                "avg_amount_20": round2(avg_amt),
            })

            if i % 100 == 0:
                save_scan_status("running", f"價值分析粗篩中：{i}/{total}，通過 {len(rough)} 檔。")

            time.sleep(ROUGH_ANALYSIS_SLEEP)

        except Exception as e:
            print("value rough scan failed", sym, e)

    rough = sorted(rough, key=lambda x: (x.get("rough_score", 0), x.get("avg_amount_20", 0)), reverse=True)
    targets = rough[:VALUE_SCAN_LIMIT]

    save_scan_status("running", f"粗篩完成：通過 {len(rough)} 檔，進入基本面深入分析 {len(targets)} 檔。")

    final = []
    for i, info in enumerate(targets, 1):
        sym = info.get("symbol")
        try:
            item = analyze_value_stock(sym, name=info.get("name", sym), industry=info.get("industry", "-"))
            if not item:
                continue

            item["value_market_status"] = value_mkt.get("value_market_status", "-")
            item["value_market_policy"] = value_mkt.get("value_market_policy", "-")
            item["value_market_note"] = value_mkt.get("value_market_note", "-")

            if item.get("current_status") == "買進提醒":
                if not value_mkt.get("value_allow_buy", False):
                    item["current_status"] = "等待大盤轉強"
                    item["execution_action"] = "大盤非買進環境，只觀察不推買進"
                elif value_mkt.get("value_market_policy") == "保守買進":
                    if not (
                        item.get("score", 0) >= VALUE_CAUTION_MIN_SCORE
                        and item.get("upside_pct", 0) >= VALUE_CAUTION_MIN_UPSIDE
                        and item.get("technical_entry_score", 0) >= VALUE_CAUTION_TECH_SCORE
                    ):
                        item["current_status"] = "等待大盤轉強"
                        item["execution_action"] = "多頭回檔環境，條件未達保守買進門檻"

            if item.get("current_status") in ["買進提醒", "等待進場點", "等待大盤轉強", "只觀察"]:
                final.append(item)
                if item.get("current_status") == "買進提醒":
                    record_value_signal("買進提醒", item, "低估轉強，具備重估空間，且大盤與進場點允許。")

            if i % 25 == 0:
                save_scan_status("running", f"基本面分析中：{i}/{len(targets)}，目前候選 {len(final)} 檔。")

            time.sleep(VALUE_SLEEP)

        except Exception as e:
            print("value detail scan failed", sym, e)

    final = sorted(
        final,
        key=lambda x: (
            x.get("current_status") == "買進提醒",
            x.get("level") == "S",
            x.get("score", 0),
            x.get("upside_pct", 0),
        ),
        reverse=True,
    )

    cand = update_value_candidate_pool(final)
    cand_list = list(cand.get("candidates", {}).values())[:MAX_CANDIDATE_DISPLAY]
    alerts = cand.get("entry_alerts", [])

    status_counts = {}
    for x in final:
        k = x.get("current_status", "未分類")
        status_counts[k] = status_counts.get(k, 0) + 1

    data = {
        "updated_at": taiwan_now(),
        **market,
        **value_mkt,
        "strategy_name": "價值分析｜低估重估潛力股系統",
        "strategy_version": VALUE_MODEL_VERSION,
        "loose_watch_count": status_counts.get("只觀察", 0),
        "loose_observation_enabled": False,
        "rough_scan_top_n": VALUE_SCAN_LIMIT,
        "lightweight_mode": True,
        "resource_saving_scan": True,
        "rough_scan_total": len(rough),
        "detailed_scan_total": len(targets),
        "stock_pool_count": total,
        "s_count": len([x for x in final if x.get("level") == "S"]),
        "a_count": len([x for x in final if x.get("level") == "A"]),
        "sector_rankings": [],
        "candidate_count": len(cand_list),
        "candidate_pool": cand_list,
        "entry_alerts": alerts,
        "value_status_counts": status_counts,
        "strategy_feedback": strategy_feedback(load_trade_log()),
    }

    save_scan_results(data)

    save_scan_status(
        "done",
        f"價值分析完成：全市場 {total} 檔，粗篩 {len(rough)} 檔，深入分析 {len(targets)} 檔，"
        f"買進提醒 {len(alerts)} 檔，等待進場 {status_counts.get('等待進場點', 0)} 檔，等待大盤 {status_counts.get('等待大盤轉強', 0)} 檔，觀察 {status_counts.get('只觀察', 0)} 檔."
    )


@app.route("/reset-scan-status")
def reset_scan_status_route():
    reset_scan_status("已手動重置掃描狀態，可以重新掃描。")
    record_schedule_health("manual_reset", "ok", "使用者手動重置掃描狀態")
    return redirect(url_for("index"))


@app.route("/schedule-health")
def schedule_health():
    return Response(schedule_health_text(), mimetype="text/plain; charset=utf-8")


@app.route("/scan-status")
def scan_status_route():
    return load_scan_status()




@app.route("/quote-test/<symbol>")
def quote_test(symbol):
    q = get_realtime_price(symbol)
    return q or {"error": "no quote", "symbol": symbol}


@app.route("/quote-index")
def quote_index():
    return {
        "twii": get_realtime_price("^TWII"),
        "otc": get_realtime_price("^TWOII"),
    }


@app.route("/heartbeat")
def heartbeat_route():
    return market_heartbeat_check(manual=True)


@app.route("/system-health")
def system_health_route():
    return Response(heartbeat_summary_text(), mimetype="text/plain; charset=utf-8")


@app.route("/")
def index():
    scan=load_scan_results(); status=load_scan_status(); tracks=load_track(); logs=load_trade_log(); updated=[]
    for t in tracks:
        updated.append(manage_holding(t, download_stock(t["symbol"],"1y")))
    save_track(updated); dash=strategy_dashboard(logs); feedback=scan.get("strategy_feedback") or strategy_feedback(logs)
    return render_template("index.html", now=taiwan_now(), twii=get_index_price("^TWII"), otc=get_index_price("^TWOII"), market_status=scan.get("market_status","尚未掃描"), market_score=scan.get("market_score",0), risk_mode=scan.get("risk_mode","-"), risk_switch=scan.get("risk_switch","-"), allow_new_positions=scan.get("allow_new_positions",False), risk_note=scan.get("risk_note","-"), risk_multiplier=scan.get("risk_multiplier",0), value_market_status=scan.get("value_market_status","-"), value_market_policy=scan.get("value_market_policy","-"), value_market_score=scan.get("value_market_score","-"), value_market_note=scan.get("value_market_note","-"), value_allow_buy=scan.get("value_allow_buy",False), market_egg_zone=scan.get("market_egg_zone","-"), market_pressure_note=scan.get("market_pressure_note","-"), scan_updated_at=scan.get("updated_at","尚未掃描"), stock_pool_count=scan.get("stock_pool_count",0), s_count=scan.get("s_count",0), a_count=scan.get("a_count",0), candidate_count=scan.get("candidate_count",0), sector_rankings=scan.get("sector_rankings",[]), candidate_pool=scan.get("candidate_pool",[]), entry_alerts=scan.get("entry_alerts",[]), loose_watch_count=scan.get("loose_watch_count",0), scan_status=status.get("status","idle"), scan_message=status.get("message","尚未掃描"), scan_status_time=status.get("updated_at","-"), tracks=updated, trade_logs=logs[-15:], strategy_dashboard=dash, strategy_feedback=feedback, account_size=ACCOUNT_SIZE, risk_per_trade=round2(RISK_PER_TRADE*100), line_token_ready=bool(get_line_token()), line_user_ready=bool(get_line_user_id()), line_enabled=line_enabled(), line_user_id=get_line_user_id(), openai_ready=bool(OPENAI_API_KEY), ai_report=load_ai_strategy_report(), schedule_health=load_schedule_health(), heartbeat=load_heartbeat_status(), app_version=APP_VERSION_NAME, realtime_quote_enabled=REALTIME_QUOTE_ENABLED, heartbeat_line_enabled=HEARTBEAT_LINE_REPORT_ENABLED, heartbeat_holding_enabled=HEARTBEAT_HOLDING_CHECK_ENABLED, rough_scan_top_n=ROUGH_SCAN_TOP_N, loose_observation_limit=LOOSE_OBSERVATION_LIMIT)


@app.route("/scan-now")
@app.route("/scan-now")
def scan_now():
    global is_scanning

    status = load_scan_status()

    # 如果狀態是逾時或錯誤，允許重新送出掃描。
    if is_scanning and status.get("status") == "running":
        save_scan_status("running", "掃描已在背景執行中，請稍候，不要重複按。")
        return redirect(url_for("index"))

    def run():
        global is_scanning
        try:
            is_scanning = True
            save_scan_status("running", "已送出手動掃描任務，正在建立全市場股票池。")
            record_schedule_health("manual_scan", "running", "手動掃描開始")
            scan_market()
            evaluate_signal_database()
            optimize_strategy_weights()
            if OPENAI_ANALYSIS_ENABLED:
                generate_openai_strategy_report()
            record_schedule_health("manual_scan", "ok", "手動掃描完成")
        except Exception as e:
            save_scan_status("error", f"手動掃描失敗：{e}")
            record_schedule_health("manual_scan", "error", str(e))
            print("手動掃描失敗", e)
        finally:
            is_scanning = False

    threading.Thread(target=run, daemon=True).start()
    save_scan_status("running", "手動掃描任務已送出，背景正在執行。")
    return redirect(url_for("index"))

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
    tracks.append({"symbol":symbol,"name":item.get("name",symbol),"level":item.get("level","-"),"sector":item.get("sector","-"),"buy_type":item.get("buy_type","-"),"price":price,"entry_price":price,"shares":qty,"realized_pnl":0,"trade_actions":[{"type":"初始追蹤","price":price,"shares":qty,"note":note or "加入追蹤","date":taiwan_now()}],"note":note or "","support_price":safe_float(item.get("support_price")),"no_entry_price":safe_float(item.get("no_entry_price")),"invalid_price":safe_float(item.get("invalid_price")),"practical_stop":safe_float(item.get("practical_stop")),"initial_stop":safe_float(item.get("practical_stop")),"risk_reward":safe_float(item.get("risk_reward")),"risk_reward_group":item.get("risk_reward_group",risk_reward_group(item.get("risk_reward",0))),"sector_status":item.get("sector_status","-"),"leader_status":item.get("leader_status","-"),"market_status":item.get("market_status","-"),"weekly_trend":item.get("weekly_trend","-"),"daily_signal":item.get("daily_signal","-"),"mtf_status":item.get("mtf_status","-"),"execution_quality":eq,"entry_deviation_pct":round2(pct(price,safe_float(item.get("next_entry_high")))) if item.get("next_entry_high") else 0,"suggest_entry_low":safe_float(item.get("next_entry_low")),"suggest_entry_high":safe_float(item.get("next_entry_high")),"feedback_score":item.get("feedback_score",0),"feedback_notes":item.get("feedback_notes",[]),"value_model_version":item.get("value_model_version","-"),"fair_value":item.get("fair_value",0),"upside_pct":item.get("upside_pct",0),"value_score":item.get("score",0),"buy_reason":"低估轉強，具備重估空間","date":today_str(),"ai_holding_status":"剛加入追蹤","ai_exit_notice":"等待隔日開盤與支撐確認。","highest_since_entry":price,"lowest_since_entry":price,"max_favorable_pct":0,"max_drawdown_pct":0,"profit_giveback_pct":0,"trail_range":"-","trail_zone_name":"AI移動風控區","wave_target_1":0,"wave_target_2":0,"wave_target_3":0})
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
    if now.weekday() >= 5:
        save_scan_status("skip", "今天是假日，省資源模式略過全市場掃描。")
        record_schedule_health("daily_market_scan_1605", "skip", "假日略過掃描")
        return

    status = load_scan_status()

    if is_scanning and status.get("status") == "running":
        record_schedule_health("daily_market_scan_1605", "skip", "已有掃描執行中，略過本次")
        return

    is_scanning = True

    try:
        save_scan_status("running", "16:05排程掃描開始，正在建立全市場股票池。")
        record_schedule_health("daily_market_scan_1605", "running", "排程掃描開始")
        scan_market()
        evaluate_signal_database()
        optimize_strategy_weights()
        if OPENAI_ANALYSIS_ENABLED:
            generate_openai_strategy_report()
        record_schedule_health("daily_market_scan_1605", "ok", "排程掃描完成")
    except Exception as e:
        save_scan_status("error", f"排程掃描失敗：{e}")
        record_schedule_health("daily_market_scan_1605", "error", str(e))
        print("排程掃描失敗", e)
    finally:
        is_scanning = False



scheduler = BackgroundScheduler(timezone=TZ)

# =====================================================
# 輕量穩定監控排程版
# =====================================================
# 開盤期間心跳檢查：每小時回報一次
# 只抓大盤/櫃買與已追蹤持股，不掃全市場
scheduler.add_job(
    market_heartbeat_check,
    trigger="cron",
    day_of_week="mon-fri",
    hour="9-13",
    minute=5,
    id="market_heartbeat_hourly",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
    misfire_grace_time=900
)

# 全市場掃描仍然一天一次，避免吃掉Railway資源
scheduler.add_job(
    scheduled_scan,
    trigger="cron",
    day_of_week="mon-fri",
    hour=16,
    minute=5,
    id="daily_market_scan",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
    misfire_grace_time=1800
)

scheduler.add_job(
    run_line_open_watch_job,
    trigger="cron",
    day_of_week="mon-fri",
    hour=9,
    minute=10,
    id="line_open_watch_0910",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
    misfire_grace_time=1800
)

scheduler.add_job(
    run_line_preclose_job,
    trigger="cron",
    day_of_week="mon-fri",
    hour=13,
    minute=20,
    id="line_preclose_1320",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
    misfire_grace_time=1800
)

scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
