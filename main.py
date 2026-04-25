import os, json, pandas as pd, yfinance as yf
from flask import Flask, render_template, redirect, url_for, request
from datetime import datetime

app = Flask(__name__)
DB_FILE = 'sim_trading.json'


# 📊 取得全市場清單
def get_full_market_list():
    try:
        tse_url = "http://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
        otc_url = "http://isin.twse.com.tw/isin/C_public.jsp?strMode=4"

        tse_df = pd.read_html(tse_url)[0]
        otc_df = pd.read_html(otc_url)[0]

        full_df = pd.concat([tse_df, otc_df])
        stocks = full_df[full_df[0].str.contains(r'^\d{4}\s', na=False)]

        market_map = {}
        for item in stocks[0]:
            parts = item.split()
            code, name = parts[0], parts[1]
            suffix = ".TW" if len(code) == 4 else ".TWO"
            market_map[f"{code}{suffix}"] = name

        return market_map
    except:
        return {"2330.TW": "台積電"}


# 🧠 短線＋波段策略
def analyze_stock(hist):
    try:
        close = hist['Close']
        vol = hist['Volume']

        # 漲幅（5日）
        change = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100

        # 均線
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()

        # 成交量放大
        vol_ratio = vol.iloc[-1] / vol.mean()

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        rs = gain.rolling(14).mean() / loss.rolling(14).mean()
        rsi = 100 - (100 / (1 + rs))

        signals = []

        # 🔥 短線策略
        if change > 3 and vol_ratio > 1.5 and 50 < rsi.iloc[-1] < 70:
            signals.append("短線強勢")

        # 📈 波段策略
        if ma5.iloc[-1] > ma20.iloc[-1] and rsi.iloc[-1] > 50:
            signals.append("波段起漲")

        return signals, round(change, 2)

    except:
        return [], 0


# 📉 診斷
def diagnose_stock(buy_price, curr_price):
    pnl = (curr_price - buy_price) / buy_price * 100

    if pnl <= -7:
        return {"status": "危險", "advice": "立即停損", "color": "text-danger", "alert": True}
    elif pnl >= 15:
        return {"status": "注意", "advice": "分批停利", "color": "text-warning", "alert": True}
    elif pnl < 0:
        return {"status": "持平", "advice": "觀察支撐", "color": "text-secondary", "alert": False}
    else:
        return {"status": "良好", "advice": "續抱觀察", "color": "text-success", "alert": False}


@app.route('/')
def index():
    # 📊 大盤
    market = {"twii": "---", "otc": "---"}
    try:
        market['twii'] = f"{yf.Ticker('^TWII').fast_info['last_price']:,.0f}"
        market['otc'] = f"{yf.Ticker('^TWOII').fast_info['last_price']:.2f}"
    except:
        pass

    # 📉 模擬交易
    trades = []
    alerts = []
    total_cost = 0
    total_value = 0

    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            trade_data = json.load(f)

            for t in trade_data:
                try:
                    curr = yf.Ticker(t['symbol']).fast_info['last_price']
                    total_cost += t['buy_price']
                    total_value += curr

                    pnl = (curr - t['buy_price']) / t['buy_price'] * 100
                    diag = diagnose_stock(t['buy_price'], curr)

                    if diag['alert']:
                        alerts.append(f"{t['name']}：{diag['advice']}")

                    trades.append({
                        **t,
                        "curr_p": round(curr, 2),
                        "pnl": round(pnl, 2),
                        "diag": diag
                    })
                except:
                    continue

    total_pnl = round((total_value - total_cost) / total_cost * 100, 2) if total_cost > 0 else 0

    # 🔍 掃描
    recs = []

    if request.args.get('scan') == 'true':
        all_stocks = get_full_market_list()

        symbols = list(all_stocks.keys())[:100]
        data = yf.download(symbols, period="1mo", group_by='ticker')

        for sym in symbols:
            try:
                hist = data[sym].dropna()
                if len(hist) < 20:
                    continue

                signals, change = analyze_stock(hist)

                if signals:
                    recs.append({
                        "symbol": sym,
                        "name": all_stocks[sym],
                        "price": round(hist['Close'].iloc[-1], 2),
                        "change": change,
                        "signals": signals
                    })
            except:
                continue

        # 排序
        recs = sorted(recs, key=lambda x: x['change'], reverse=True)

    return render_template('index.html',
                           market=market,
                           trades=trades,
                           recs=recs,
                           total_pnl=total_pnl,
                           alerts=alerts)


@app.route('/track/<symbol>/<name>/<float:price>')
def track(symbol, name, price):
    data = []

    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

    if not any(x['symbol'] == symbol for x in data):
        data.append({
            "symbol": symbol,
            "name": name,
            "buy_price": price,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M")
        })

        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    return redirect(url_for('index'))


@app.route('/untrack/<symbol>')
def untrack(symbol):
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            data = [x for x in json.load(f) if x['symbol'] != symbol]

        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    return redirect(url_for('index'))


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
