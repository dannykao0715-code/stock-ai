import os, json, pandas as pd, yfinance as yf
import schedule, time, requests, threading
from flask import Flask, render_template, request
from datetime import datetime

app = Flask(__name__)

# ====== 設定 ======
LINE_TOKEN = "你的LINE_NOTIFY_TOKEN"  # ← 一定要填
SCAN_LIMIT = 100  # 掃描股票數量（先不要太大）


# ====== 市場清單 ======
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


# ====== 策略 ======
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

        # 🔥 短線
        if change > 3 and vol_ratio > 1.5 and 50 < rsi.iloc[-1] < 70:
            signals.append("短線強勢")

        # 📈 波段
        if ma5.iloc[-1] > ma20.iloc[-1] and rsi.iloc[-1] > 50:
            signals.append("波段起漲")

        return signals, round(change, 2)

    except:
        return [], 0


# ====== LINE 通知 ======
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


# ====== 掃描市場 ======
def scan_market():
    results = []
    stocks = get_full_market_list()

    symbols = list(stocks.keys())[:SCAN_LIMIT]

    data = yf.download(symbols, period="2mo", group_by='ticker')

    for sym in symbols:
        try:
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
        except:
            continue

    results = sorted(results, key=lambda x: x['change'], reverse=True)

    # 🔔 LINE通知（前5名）
    if results:
        msg = "\n".join([f"{r['name']} +{r['change']}%" for r in results[:5]])
        send_line(f"📈 今日強勢股\n{msg}")

    return results


# ====== 回測 ======
def backtest(symbol):
    try:
        hist = yf.download(symbol, period="6mo")

        wins = 0
        total = 0

        for i in range(20, len(hist) - 5):
            sub = hist.iloc[:i]
            signals, _ = analyze_stock(sub)

            if signals:
                total += 1
                future = hist['Close'].iloc[i + 5]
                now = hist['Close'].iloc[i]

                if future > now:
                    wins += 1

        return round(wins / total * 100, 2) if total > 0 else 0

    except:
        return 0


# ====== 排程（每天14:00） ======
def scheduler_job():
    print("執行每日掃描:", datetime.now())
    scan_market()


def run_scheduler():
    schedule.every().day.at("14:00").do(scheduler_job)
    while True:
        schedule.run_pending()
        time.sleep(1)


threading.Thread(target=run_scheduler, daemon=True).start()


# ====== 網頁 ======
@app.route('/')
def index():
    recs = []

    if request.args.get('scan') == 'true':
        recs = scan_market()

        # 加勝率（前10檔）
        for r in recs[:10]:
            r['winrate'] = backtest(r['symbol'])

    return render_template("index.html", recs=recs)


# ====== 啟動 ======
if __name__ == "__main__":
    app.run(debug=True)
