import os, requests, json, time
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime
from bs4 import BeautifulSoup

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 1. Goodinfo! 爬蟲工具 (獲取更準確的名稱與即時資訊) ---
def get_goodinfo_data(symbol_no):
    """從 Goodinfo! 獲取股票基本面資訊"""
    url = f"https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={symbol_no}"
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
        'referer': 'https://goodinfo.tw/tw/index.asp'
    }
    try:
        # Goodinfo 有防爬機制，加入隨機延遲
        res = requests.get(url, headers=headers, timeout=8)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 抓取標題中的名稱 (例如: 2330 台積電)
        title = soup.find('title').text
        name = title.split(' ')[1] if ' ' in title else symbol_no
        return name
    except:
        return None

# --- 2. 追蹤名單系統 ---
def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r') as f: return json.load(f)
    return []

def save_watchlist(data):
    with open(WATCHLIST_FILE, 'w') as f: json.dump(data, f)

# --- 3. 核心分析與建議 ---
def analyze_inertia(symbol):
    try:
        data = yf.download(symbol, period="10y", interval="1d", progress=False)
        if data.empty or len(data) < 500: return None
        close = data['Close'].ffill().values.flatten()
        current = close[-20:]
        
        def norm(arr):
            s = np.std(arr); return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
            
        cur_norm = norm(current)
        best_s, best_d = -1, ""
        for i in range(0, len(close) - 60, 10):
            past = close[i : i+20]
            score = np.corrcoef(cur_norm, norm(past))[0, 1]
            if not np.isnan(score) and score > best_s:
                best_s, best_d = score, data.index[i].strftime('%Y-%m-%d')
        
        score_val = round(best_s * 100, 2)
        advice = "🔥 強勢佈局" if score_val >= 92 else "📈 分批買進" if score_val >= 82 else "🔎 觀察等待"
        
        # 獲取中文名稱 (優先從 Goodinfo 或本地字典)
        clean_id = symbol.split('.')[0]
        name = get_goodinfo_data(clean_id) or clean_id
        
        return {"symbol": symbol, "name": name, "score": score_val, "history_date": best_d, "advice": advice}
    except: return None

# --- 4. 路由與損益計算 ---
@app.route('/')
def index():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 獲取成交量排行榜 (爬取 Yahoo)
    try:
        rank_url = "https://tw.stock.yahoo.com/rank/volume?exchange=TAI"
        r = requests.get(rank_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        soup = BeautifulSoup(r.text, 'html.parser')
        top_ids = [a.get('href').split('/')[-1].split('.')[0] + ".TW" for a in soup.select('a[href*="/quote/"]')[:12]]
    except:
        top_ids = ["2330.TW", "2317.TW", "1802.TW", "2603.TW"]

    recommendations = []
    for s in top_ids:
        res = analyze_inertia(s)
        if res: recommendations.append(res)
    
    # 計算損益
    watchlist = load_watchlist()
    tracked = []
    for item in watchlist:
        try:
            p = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
            profit = round(((p - item['buy_price']) / item['buy_price']) * 100, 2)
            tracked.append({**item, "name": get_goodinfo_data(item['symbol'].split('.')[0]) or item['symbol'], "curr_p": p, "profit": profit})
        except: pass

    return render_template('index.html', recommendations=recommendations, tracked_results=tracked, current_time=now)

@app.route('/add_track/<symbol>')
def add_track(symbol):
    price = round(yf.Ticker(symbol).fast_info['last_price'], 2)
    watchlist = load_watchlist()
    if not any(d['symbol'] == symbol for d in watchlist):
        watchlist.append({"symbol": symbol, "buy_price": price, "date": datetime.now().strftime("%Y-%m-%d")})
        save_watchlist(watchlist)
    return redirect(url_for('index'))

@app.route('/clear')
def clear():
    save_watchlist([]); return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
