import os, json, pandas as pd, yfinance as yf
import time, requests
from flask import Flask, render_template, request

app = Flask(__name__)

# ===== 設定 =====
SCAN_LIMIT = 50
LINE_TOKEN = "你的LINE_NOTIFY_TOKEN"  # 沒有可留空


# ===== 市場清單 =====
def get_full_market_list():
    try:
        tse = pd.read_html("http://isin.twse.com.tw/isin/C_public.jsp?strMode=2")[0]
        otc = pd.read_html("http://isin.twse.com.tw/isin/C_public.jsp?strMode=4")[0]
        df = pd.concat([tse, otc])
        stocks = df[df[0].str.contains(r'^\d{4}\s', na=False)]

        market_map = {}
        for item in stocks[0]:
            code, name = item.split()[:2]
            market_map[f"{code}.TW"] = name

        return market_map
    except:
        return {"2330.TW": "台積電"}


# ===== 策略 =====
def analyze_stock(hist):
    try:
        close = hist['Close']
        vol = hist['Volume']

        change = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100

        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()

        vol_ratio = vol.iloc[-1] / vol.mean()

        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        rs = gain.rolling(14).mean() / loss.rolling(14).mean()
        rsi = 100 - (100 / (1 + rs))

        signals = []

        if change > 3 and vol_ratio > 1.5 and 50 < rsi.iloc[-1] < 70:
            signals.append("短線強勢")

        if ma5.iloc[-1] > ma20.iloc[-1] and rsi.iloc[-1] > 50:
            signals.append("波段起漲")

        return signals, round(change, 2)

    except:
        return [], 0


# ===== 安全下載（解決 yfinance 爆炸）=====
def safe_download(symbols):
    for i in range(3):
        try:
            data = yf.download(
                symbols,
                period="2mo",
                group_by='ticker',
                threads=False
            )
            if not data.empty:
                return data
        except Exception as e:
            print("retry:", e)
            time.sleep(2)
    return None


# ===== LINE =====
def send_line(msg):
    if not LINE_TOKEN:
        return
    try:
        requests.post(
            "https://notify-api.line.me/api/notify",
            headers={"Authorization": f"Bearer {LINE_TOKEN}"},
            data={"message": msg}
        )
    except:
        pass


# ===== 掃描 =====
def scan_market():
    results = []
    stocks = get_full_market_list()

    symbols = list(stocks.keys())[:SCAN_LIMIT]

    data = safe_download(symbols)

    if data is None:
        print("❌ 抓不到資料")
        return []

    for sym in symbols:
        try:
            if sym not in data:
                continue

            hist = data[sym].dropna()

            if len(hist) < 30:
                continue

            signals, change = analyze_stock(hist)

            if signals:
                results.append({
                    "symbol": sym,
                    "name": stocks[sym],
                    "price": round(hist['Close'].iloc[-1], 2),
                    "change": change,
                    "signals": signals
                })

        except Exception as e:
            print("單支錯誤:", sym, e)

    results = sorted(results, key=lambda x: x['change'], reverse=True)

    # LINE通知
    if results:
        msg = "\n".join([f"{r['name']} +{r['change']}%" for r in results[:5]])
        send_line(f"📈 今日強勢股\n{msg}")

    return results


# ===== 網頁 =====
@app.route('/')
def index():
    recs = []

    if request.args.get('scan') == 'true':
        recs = scan_market()

    return render_template("index.html", recs=recs)


# ===== 啟動 =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
