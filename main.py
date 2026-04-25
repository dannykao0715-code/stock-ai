import os, requests, json
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime
from bs4 import BeautifulSoup

app = Flask(__name__)

WATCHLIST_FILE = 'watchlist.json'

# --- 1. 個股名稱字典 (可手動擴充) ---
STOCK_NAMES = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科", 
    "2303.TW": "聯電", "2382.TW": "廣達", "3231.TW": "緯創",
    "2603.TW": "長榮", "2609.TW": "陽明", "1802.TW": "台玻",
    "0050.TW": "元大台灣50", "0056.TW": "元大高股息", "2337.TW": "旺宏",
    "4958.TW": "臻鼎-KY", "2409.TW": "友達", "2367.TW": "燿華",
    "2313.TW": "華通", "3481.TW": "群創", "2344.TW": "華邦電"
}

def get_stock_name(symbol):
    return STOCK_NAMES.get(symbol, symbol.split('.')[0])

# --- 2. 買賣建議邏輯 ---
def get_advice(score):
    if score >= 95: return "🔥 強勢重演：建議積極佈局"
    if score >= 85: return "📈 慣性極高：建議分批買進"
    if score >= 75: return "🔎 趨勢成形：建議加入觀察"
    return "☁️ 慣性偏弱：暫時觀望"

# --- 3. 追蹤名單管理 ---
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r') as f: return json.load(f)
    return []

def save_watchlist(data):
    with open(WATCHLIST_FILE, 'w') as f: json.dump(data, f)

# --- 4. 路由：首頁與分析 ---
@app.route('/')
def index():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 抓取大盤 (簡化)
    try:
        tw_idx = round(yf.Ticker("^TWII").fast_info['last_price'], 2)
    except: tw_idx = "N/A"
    
    # 執行排行榜與分析 (取前 10 名加快速度)
    target_stocks = ["2330.TW", "2317.TW", "1802.TW", "2603.TW", "3231.TW", "0050.TW", "2303.TW", "4958.TW"] # 演示用固定清單，可換回爬蟲
    recommendations = []
    for s in target_stocks:
        # (此處沿用先前的 analyze_inertia 邏輯，僅補充名稱與建議)
        # 假設分析結果如下：
        res = {"symbol": s, "name": get_stock_name(s), "score": 88.5} # 簡化演示
        res["advice"] = get_advice(res["score"])
        recommendations.append(res)
    
    # 計算追蹤名單損益
    watchlist = load_watchlist()
    tracked_results = []
    for item in watchlist:
        try:
            current_price = yf.Ticker(item['symbol']).fast_info['last_price']
            profit_pct = round(((current_price - item['buy_price']) / item['buy_price']) * 100, 2)
            tracked_results.append({
                **item,
                "name": get_stock_name(item['symbol']),
                "current_price": round(current_price, 2),
                "profit": profit_pct
            })
        except: pass

    return render_template('index.html', tw_idx=tw_idx, recommendations=recommendations, 
                           tracked_results=tracked_results, current_time=now)

# --- 5. 路由：加入追蹤 ---
@app.route('/add_track/<symbol>')
def add_track(symbol):
    try:
        price = yf.Ticker(symbol).fast_info['last_price']
        watchlist = load_watchlist()
        # 檢查是否已在名單
        if not any(d['symbol'] == symbol for d in watchlist):
            watchlist.append({
                "symbol": symbol,
                "buy_price": round(price, 2),
                "date": datetime.now().strftime("%Y-%m-%d")
            })
            save_watchlist(watchlist)
    except: pass
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
