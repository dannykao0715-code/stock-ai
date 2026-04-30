import os
import json
import time
import threading
import requests
import pandas as pd
import yfinance as yf

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
STOCK_POOL_FILE = "stock_pool.json"
SCAN_STATUS_FILE = "scan_status.json"
INST_FILE = "institutional_cache.json"

MAX_S_RESULTS = 10
MAX_A_RESULTS = 10
MAX_B_RESULTS = 10
MAX_HOT_RESULTS = 10

is_scanning = False


# ======================
# 時間
# ======================
def taiwan_now():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")


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

        "2409.TW": ("友達", "面板"),
        "3481.TW": ("群創", "面板"),
        "8069.TWO": ("元太", "電子紙"),

        "2881.TW": ("富邦金", "金融"),
        "2882.TW": ("國泰金", "金融"),
        "2886.TW": ("兆豐金", "金融"),
        "2891.TW": ("中信金", "金融"),
        "2884.TW": ("玉山金", "金融"),
        "2885.TW": ("元大金", "金融"),
        "5880.TW": ("合庫金", "金融"),
        "5871.TW": ("中租-KY", "金融"),

        "1301.TW": ("台塑", "塑化"),
        "1303.TW": ("南亞", "塑化"),
        "1326.TW": ("台化", "塑化"),
        "6505.TW": ("台塑化", "塑化"),
        "2002.TW": ("中鋼", "鋼鐵"),
        "2027.TW": ("大成鋼", "鋼鐵"),
        "1605.TW": ("華新", "電線電纜"),

        "2603.TW": ("長榮", "航運"),
        "2609.TW": ("陽明", "航運"),
        "2615.TW": ("萬海", "航運"),
        "2618.TW": ("長榮航", "航空"),
        "2610.TW": ("華航", "航空"),
        "2606.TW": ("裕民", "航運"),

        "6446.TW": ("藥華藥", "生技"),
        "1760.TW": ("寶齡富錦", "生技"),
        "4743.TWO": ("合一", "生技"),
        "4105.TWO": ("東洋", "生技"),
        "6472.TW": ("保瑞", "生技"),

        "2412.TW": ("中華電", "電信"),
        "3045.TW": ("台灣大", "電信"),
        "4904.TW": ("遠傳", "電信"),
        "1216.TW": ("統一", "食品"),
        "2912.TW": ("統一超", "通路"),
        "2207.TW": ("和泰車", "汽車"),

        "1504.TW": ("東元", "重電"),
        "1513.TW": ("中興電", "重電"),
        "1519.TW": ("華城", "重電"),
        "1609.TW": ("大亞", "電線電纜"),
        "1618.TW": ("合機", "電線電纜"),

        "8299.TWO": ("群聯", "記憶體"),
        "3105.TWO": ("穩懋", "砷化鎵"),
        "6187.TWO": ("萬潤", "設備"),
        "3260.TWO": ("威剛", "記憶體"),
        "8086.TWO": ("宏捷科", "砷化鎵")
    }

    return {
        symbol: {
            "name": name,
            "industry": industry
        }
        for symbol, (name, industry) in base.items()
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


# ======================
# 股票池來源
# ======================
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


def fetch_twse_openapi_stock_pool():
    market = {}

    try:
        url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        headers = {"User-Agent": "Mozilla/5.0"}

        res = requests.get(url, headers=headers, timeout=20)
        res.raise_for_status()
        data = res.json()

        for item in data:
            code = item.get("公司代號", "")
            name = item.get("公司簡稱", "") or item.get("公司名稱", "")
            industry = item.get("產業別", "上市")

            symbol, info = normalize_stock_item(code, name, industry, ".TW")
            if symbol:
                market[symbol] = info

        print("TWSE OpenAPI 上市股票：", len(market))
        return market

    except Exception as e:
        print("TWSE OpenAPI 失敗：", e)
        return {}


def fetch_tpex_openapi_stock_pool():
    market = {}

    urls = [
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_company",
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
    ]

    for url in urls:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(url, headers=headers, timeout=20)
            res.raise_for_status()
            data = res.json()

            for item in data:
                code = (
                    item.get("公司代號") or
                    item.get("股票代號") or
                    item.get("SecuritiesCompanyCode") or
                    item.get("CompanyCode") or
                    ""
                )

                name = (
                    item.get("公司簡稱") or
                    item.get("公司名稱") or
                    item.get("股票名稱") or
                    item.get("CompanyName") or
                    ""
                )

                industry = (
                    item.get("產業別") or
                    item.get("IndustryCode") or
                    item.get("Industry") or
                    "上櫃"
                )

                symbol, info = normalize_stock_item(code, name, industry, ".TWO")
                if symbol:
                    market[symbol] = info

            if len(market) > 100:
                print("TPEx OpenAPI 上櫃股票：", len(market))
                return market

        except Exception as e:
            print("TPEx OpenAPI 嘗試失敗：", url, e)

    return market


def fetch_isin_all_stock_pool():
    market = {}

    try:
        sources = [
            ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", ".TW", "上市"),
            ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", ".TWO", "上櫃")
        ]

        for url, suffix, industry in sources:
            tables = pd.read_html(url, encoding="big5")
            df = tables[0]
            df = df[df[0].astype(str).str.contains(r"^\d{4}", na=False)]

            for item in df[0]:
                try:
                    parts = str(item).split()
                    code = parts[0]
                    name = parts[1]

                    symbol, info = normalize_stock_item(code, name, industry, suffix)
                    if symbol:
                        market[symbol] = info
                except Exception:
                    continue

        print("ISIN 全市場股票：", len(market))
        return market

    except Exception as e:
        print("ISIN 全市場失敗：", e)
        return {}


def get_stock_pool():
    market = {}

    twse = fetch_twse_openapi_stock_pool()
    market.update(twse)

    tpex = fetch_tpex_openapi_stock_pool()
    market.update(tpex)

    if len(market) < 1000:
        print("股票池數量不足，改用 ISIN 全市場補抓")
        isin_all = fetch_isin_all_stock_pool()

        if len(isin_all) > len(market):
            market = isin_all

    if len(market) > 1000:
        save_stock_pool(market)
        print("全市場股票池更新成功，共", len(market), "檔")
        return market

    cache = load_stock_pool_cache()
    if cache:
        print("使用快取股票池，共", len(cache), "檔")
        return cache

    print("使用產業龍頭保底股票池")
    return get_fallback_stock_pool()


# ======================
# 資料下載
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
# 指數與大盤
# ======================
def get_index():
    def fetch(symbol):
        df = download_stock(symbol, "5d")
        if df is None or df.empty:
            return "-"
        price = safe_float(df["Close"].iloc[-1])
        return round(price, 2) if price else "-"

    return fetch("^TWII"), fetch("^TWOII")


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
        return "強多市場", 25, "積極"
    elif last > ma60.iloc[-1] and ma20.iloc[-1] > ma60.iloc[-1]:
        return "多頭市場", 15, "正常"
    elif last < ma20.iloc[-1] and ma20.iloc[-1] < ma60.iloc[-1]:
        return "空頭市場", -35, "防守"
    else:
        return "盤整市場", -5, "保守"


# ======================
# 法人籌碼資料
# ======================
def load_institutional_cache():
    if os.path.exists(INST_FILE):
        try:
            with open(INST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("date") == today_str():
                return data.get("stocks", {})
        except Exception:
            pass

    return None


def save_institutional_cache(stocks):
    data = {
        "date": today_str(),
        "updated_at": taiwan_now(),
        "stocks": stocks
    }

    with open(INST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
        if not rows:
            save_institutional_cache({})
            return {}

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
        print("法人資料取得成功：", len(stocks), "檔")
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
    dealer_net = data.get("dealer_net", 0)
    total_net = data.get("total_net", 0)

    if foreign_days >= 3:
        score += 15
        signals.append("外資連買")

    if trust_days >= 3:
        score += 25
        signals.append("投信連買")

    if dealer_days >= 3:
        score += 10
        signals.append("自營商偏多")

    if total_net > 0:
        score += 15
        signals.append("三大法人合計買超")

    if foreign_net > 0 and trust_net > 0:
        score += 15
        signals.append("外資投信同步買超")

    if trust_net > 0 and trust_days >= 2:
        score += 10
        signals.append("投信買盤延續")

    if total_net < 0:
        score -= 15
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
# 技術與主力資金指標
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
        score += 25

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

    if 1 <= change_5d <= 15:
        signals.append("短線動能健康")
        score += 15

    if change_20d > 5:
        signals.append("波段轉強")
        score += 15

    if change_60d > 10:
        signals.append("中期趨勢轉強")
        score += 10

    position_from_low = (price - low_60) / low_60 * 100

    if 5 <= position_from_low <= 45:
        signals.append("低中位啟動區")
        score += 15

    if change_5d > 22:
        warnings.append("5日漲幅過熱")
        score -= 30

    if change_20d > 45:
        warnings.append("20日漲幅過熱")
        score -= 25

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
        "signals": signals,
        "warnings": warnings,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "atr_pct": round(float(atr_pct), 2)
    }


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

        sector_scores[sector] = {
            "sector_score": score,
            "sector_avg_5d": round(avg_5d, 2),
            "sector_avg_20d": round(avg_20d, 2),
            "sector_strong_ratio": round(strong_ratio * 100, 1)
        }

    return sector_scores


# ======================
# 回測績效
# ======================
def quick_backtest(df):
    if df is None or len(df) < 180:
        return {
            "bt_count": 0,
            "bt_winrate": 0,
            "bt_avg_return": 0,
            "bt_expectancy": 0
        }

    trades = []

    for i in range(120, len(df) - 20, 5):
        sample = df.iloc[:i].copy()
        r = analyze_stock(sample)

        if not r:
            continue

        simple_score = r["technical_score"] + r["main_score"]

        if simple_score < 120:
            continue

        entry = safe_float(df["Close"].iloc[i])
        if not entry:
            continue

        atr = calc_atr(df.iloc[:i])
        atr_now = safe_float(atr.iloc[-1])

        if not atr_now:
            continue

        stop = entry - atr_now * 2
        take = entry + atr_now * 3

        future = df.iloc[i:i + 20]
        low_min = safe_float(future["Low"].min())
        high_max = safe_float(future["High"].max())
        exit_price = safe_float(future["Close"].iloc[-1])

        final_price = exit_price

        if low_min is not None and low_min <= stop:
            final_price = stop
        elif high_max is not None and high_max >= take:
            final_price = take

        pnl = (final_price - entry) / entry * 100
        trades.append(pnl)

    if not trades:
        return {
            "bt_count": 0,
            "bt_winrate": 0,
            "bt_avg_return": 0,
            "bt_expectancy": 0
        }

    wins = [x for x in trades if x > 0]
    losses = [x for x in trades if x <= 0]

    winrate = len(wins) / len(trades) * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0

    expectancy = (winrate / 100 * avg_win) - ((100 - winrate) / 100 * avg_loss)

    return {
        "bt_count": len(trades),
        "bt_winrate": round(winrate, 2),
        "bt_avg_return": round(sum(trades) / len(trades), 2),
        "bt_expectancy": round(expectancy, 2)
    }


# ======================
# 分級策略
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
        "s_count": 0,
        "a_count": 0,
        "b_count": 0,
        "hot_count": 0,
        "s_results": [],
        "a_results": [],
        "b_results": [],
        "hot_results": []
    }


# ======================
# 全市場掃描
# ======================
def scan_market():
    save_scan_status("running", "正在背景掃描全市場，請稍後重新整理。")
    print("開始掃描：", taiwan_now())

    stocks = get_stock_pool()
    inst_data = fetch_institutional_data()
    market_status, market_score, risk_mode = get_market_status()

    analyzed = []
    total = len(stocks)

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
                print(f"已掃描 {i}/{total}")

            time.sleep(0.03)

        except Exception as e:
            print("單檔掃描失敗：", symbol, e)
            continue

    sector_scores = calc_sector_scores(analyzed)

    s_results = []
    a_results = []
    b_results = []
    hot_results = []

    for item in analyzed:
        sector_data = sector_scores.get(item["sector"], {
            "sector_score": 0,
            "sector_avg_5d": 0,
            "sector_avg_20d": 0,
            "sector_strong_ratio": 0
        })

        item["sector_score"] = sector_data["sector_score"]
        item["sector_avg_5d"] = sector_data["sector_avg_5d"]
        item["sector_avg_20d"] = sector_data["sector_avg_20d"]
        item["sector_strong_ratio"] = sector_data["sector_strong_ratio"]

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

        bt = quick_backtest(item["df"])
        item.update(bt)

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

    data = {
        "updated_at": taiwan_now(),
        "market_status": market_status,
        "market_score": market_score,
        "risk_mode": risk_mode,
        "stock_pool_count": total,

        "s_count": len(s_results),
        "a_count": len(a_results),
        "b_count": len(b_results),
        "hot_count": len(hot_results),

        "s_results": s_results[:MAX_S_RESULTS],
        "a_results": a_results[:MAX_A_RESULTS],
        "b_results": b_results[:MAX_B_RESULTS],
        "hot_results": hot_results[:MAX_HOT_RESULTS]
    }

    save_scan_results(data)

    save_scan_status(
        "done",
        f"掃描完成：股票池 {total} 檔，S級 {len(s_results)} 檔，A級 {len(a_results)} 檔，B級 {len(b_results)} 檔，過熱 {len(hot_results)} 檔。"
    )

    print(
        "掃描完成：",
        taiwan_now(),
        "股票池", total,
        "S", len(s_results),
        "A", len(a_results),
        "B", len(b_results),
        "HOT", len(hot_results)
    )


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
        twii=twii,
        otc=otc,
        market_status=scan_data.get("market_status", "尚未掃描"),
        market_score=scan_data.get("market_score", 0),
        risk_mode=scan_data.get("risk_mode", "-"),
        scan_updated_at=scan_data.get("updated_at", "尚未掃描"),
        stock_pool_count=scan_data.get("stock_pool_count", 0),

        s_count=scan_data.get("s_count", 0),
        a_count=scan_data.get("a_count", 0),
        b_count=scan_data.get("b_count", 0),
        hot_count=scan_data.get("hot_count", 0),

        s_results=scan_data.get("s_results", []),
        a_results=scan_data.get("a_results", []),
        b_results=scan_data.get("b_results", []),
        hot_results=scan_data.get("hot_results", []),

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
