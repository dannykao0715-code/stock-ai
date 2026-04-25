import os, json, pandas as pd, yfinance as yf
from flask import Flask, render_template, redirect, url_for, request
from datetime import datetime

app = Flask(__name__)
DB_FILE = 'sim_trading.json'

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

def diagnose_stock(symbol, buy_price, curr_price):
    """
    收盤診斷邏輯：根據損益與技術面(模擬)給予建議
    """
    pnl = (curr_price - buy_price) / buy_price * 100
    
    # 診斷邏輯：停損設定為 -7%，停利建議為 +15%
    if pnl <= -7:
        return {"status": "危險", "advice": "立即停損", "color": "text-danger", "alert": True}
    elif pnl >= 15:
        return {"status": "注意", "advice": "分批停利", "color": "text-warning", "alert": True}
    elif pnl < 0:
        return {"status": "持平", "advice": "觀察支撐", "color": "text-secondary", "alert": False}
    else:
        return {"status": "良好", "advice": "繼續持有", "color": "text-success", "alert": False}

@app.route('/')
def index():
    market = {"twii": "---", "otc": "---"}
    try:
        market['twii'] = f"{yf.Ticker('^TWII').fast_info['last_price']:,.0f}"
        market['otc'] = f"{yf.Ticker('^TWOII').fast_info['last_price']:.2f}"
    except: pass

    trades = []
    total_pnl = 0
    alerts = [] # 用於存放需要跳通知的股票
    
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            trade_data = json.load(f)
            for t in trade_data:
                try:
                    curr = yf.Ticker(t['symbol']).fast_info['last_price']
                    p = (curr - t['buy_price']) / t['buy_price'] * 100
                    total_pnl += p
                    
                    # 執行收盤診斷
                    diag = diagnose_stock(t['symbol'], t['buy_price'], curr)
                    if diag['alert']:
                        alerts.append(f"{t['name']} ({t['symbol']}): {diag['advice']}")
                        
                    trades.append({**t, "curr_p": round(curr, 2), "pnl": round(p, 2), "diag": diag})
                except: pass

    recs = []
    if request.args.get('scan') == 'true':
        all_stocks = get_full_market_list()
        count = 0
        for sym, name in all_stocks.items():
            if count > 100: break 
            try:
                tk = yf.Ticker(sym)
                hist = tk.history(period="5d")
                if len(hist) < 2: continue
                change = ((hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0]) * 100
                if change > 3:
                    recs.append({"symbol": sym, "name": name, "price": round(hist['Close'].iloc[-1], 2), "change": round(change, 2)})
                count += 1
            except: continue
            
    return render_template('index.html', market=market, trades=trades, recs=recs, total_pnl=round(total_pnl, 2), alerts=alerts)

# ... (track / untrack 路由保持不變)
