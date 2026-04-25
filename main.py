import os, requests, json
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime
from bs4 import BeautifulSoup

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 1. 個股名稱對照表 ---
STOCK_NAMES = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科", 
    "2303.TW": "聯電", "2382.TW": "廣達", "3231.TW": "緯創",
    "2603.TW": "長榮", "2609.TW": "陽明", "1802.TW": "台玻",
    "0050.TW": "元大台灣50", "2409.TW": "友達", "3481.TW": "群創",
    "2337.TW": "旺宏", "2344.TW": "華邦電", "2367.TW": "燿華",
    "2313.TW": "華通", "4958.TW": "臻鼎-KY", "1513.TW": "中興電"
}

def get_name(s): return STOCK_NAMES.get(s, s.split('.')[0])

# --- 2. 追蹤名單存取 ---
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, 'r') as f: return json.load(f)
        except: return []
    return []

def save_watchlist(data):
    with open(WATCHLIST_FILE, 'w') as f: json.dump(data, f)

# --- 3. 核心分析邏輯 ---
def analyze_inertia(symbol):
    try:
        data = yf.download(symbol, period="10y", interval="1d", progress=False)
        if data.empty or len(data) < 500: return None
        close_prices = data['Close'].ffill().values.flatten()
        current = close_prices[-20:]
        def norm(arr):
            std = np.std(arr); return (arr - np.mean(arr)) / (std + 1e-9) if std != 0 else arr * 0
        current_norm = norm(current)
        best_score = -1
        best_date = ""
        for i in range(0, len(close_prices) - 60, 10):
            past = close_prices[i : i+20]
            score = np.corrcoef(current_norm, norm(past))[0, 1]
            if not np.isnan(score) and score > best_score:
                best_score = score
                best_date = data.index[i].strftime('%Y-%m-%d')
        
        final_score = round(best_score * 100, 2)
        # 買賣建議邏輯
        advice = "🔥 強勢佈局" if final_score >= 95 else "📈 分批買進" if final_score >= 85 else "🔎 觀察等待"
        
        return {"symbol": symbol, "name": get_name(symbol), "score": final_score, "history_date": best_date, "advice": advice}
    except: return None

# --- 4. 網頁路由 ---
@app.route('/')
def index():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 抓取大盤
    try:
        tw_idx = round(yf.Ticker("^TWII").fast_info['last_price'], 2)
        otc_idx = round(yf.Ticker("^TWOII").fast_info['last_price'], 2)
    except: tw_idx, otc_idx = "N/A", "N/A"
    
    # 推薦個股 (自動爬蟲前 10 檔)
    try:
        url = "https://tw.stock.yahoo.com/rank/volume?exchange=TAI"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        top_symbols = [a.get('href').split('/')[-1].split('.')[0] + ".TW" for a in soup.select('a[href*="/quote/"]')[:15]]
    except: top_symbols = ["2330.TW", "2317.TW", "1802.TW", "0050.TW"]

    recommendations = []
    for s in list(dict.fromkeys(top_symbols))[:10]:
        analysis = analyze_inertia(s)
        if analysis and analysis['score'] > 70: recommendations.append(analysis)
    
    # 實戰追蹤損益計算
    watchlist = load_watchlist()
    tracked_results = []
    for item in watchlist:
        try:
            curr_p = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
            profit = round(((curr_p - item['buy_price']) / item['buy_price']) * 100, 2)
            tracked_results.append({**item, "name": get_name(item['symbol']), "curr_p": curr_p, "profit": profit})
        except: pass

    return render_template('index.html', current_time=now, tw_idx=tw_idx, otc_idx=otc_idx, 
                           recommendations=recommendations, tracked_results=tracked_results)

@app.route('/add_track/<symbol>')
def add_track(symbol):
    try:
        price = round(yf.Ticker(symbol).fast_info['last_price'], 2)
        watchlist = load_watchlist()
        if not any(d['symbol'] == symbol for d in watchlist):
            watchlist.append({"symbol": symbol, "buy_price": price, "date": datetime.now().strftime("%Y-%m-%d")})
            save_watchlist(watchlist)
    except: pass
    return redirect(url_for('index'))

@app.route('/clear_watchlist')
def clear_watchlist():
    save_watchlist([])
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
