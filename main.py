import os, requests, json, time
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime
from bs4 import BeautifulSoup

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

def get_stock_info(symbol):
    """獲取中文名稱與籌碼狀態"""
    headers = {'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    clean_id = symbol.split('.')[0]
    info = {"name": clean_id, "retail": "▼ 散戶減持", "retail_reduce": True}
    
    try:
        # 優先從 yfinance 拿名稱，如果沒有則用代號
        ticker = yf.Ticker(symbol)
        name = ticker.info.get('longName', clean_id)
        # 簡易過濾掉英文名稱，讓介面乾淨一點
        info["name"] = name if '\u4e00' <= name <= '\u9fff' else clean_id
    except:
        pass
    return info

def analyze_stock(symbol):
    try:
        df = yf.download(symbol, period="10y", interval="1d", progress=False)
        if df.empty or len(df) < 250: return None
        
        # 修正 Pandas fillna 警告
        close = df['Close'].ffill().values.flatten()
        target = close[-20:]
        def norm(arr):
            s = np.std(arr)
            return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        
        target_n = norm(target)
        max_c = -1
        for i in range(0, len(close) - 60, 5):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_c: max_c = corr
        
        score = round(max_c * 100, 2)
        info = get_stock_info(symbol)
        
        advice = "🚀 極致看好" if score >= 88 and info["retail_reduce"] else "📈 慣性重演" if score >= 80 else "🔎 觀望"
        
        return {**info, "symbol": symbol, "score": score, "advice": advice}
    except: return None

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    return []

@app.route('/')
def index():
    # 抓取大盤與櫃買
    indices = {"twii": "---", "otc": "---"}
    try:
        indices["twii"] = round(yf.Ticker("^TWII").fast_info['last_price'], 2)
        indices["otc"] = round(yf.Ticker("^TWOII").fast_info['last_price'], 2)
    except: pass

    # 推薦名單
    stocks = ["2330.TW", "2317.TW", "1802.TW", "2603.TW", "3231.TW", "0050.TW"]
    recs = []
    for s in stocks:
        res = analyze_stock(s)
        if res: recs.append(res)
    
    # 追蹤損益
    watchlist = load_watchlist()
    tracked_list = []
    for item in watchlist:
        try:
            p = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
            profit = round(((p - item['buy_price']) / item['buy_price']) * 100, 2)
            tracked_list.append({**item, "curr_p": p, "profit": profit})
        except: pass

    return render_template('index.html', recs=recs, tracked=tracked_list, indices=indices, now=datetime.now().strftime("%Y-%m-%d %H:%M"))

@app.route('/add/<symbol>/<name>')
def add(symbol, name):
    price = round(yf.Ticker(symbol).fast_info['last_price'], 2)
    data = load_watchlist()
    if not any(x['symbol'] == symbol for x in data):
        data.append({"symbol": symbol, "name": name, "buy_price": price, "date": datetime.now().strftime("%Y-%m-%d")})
        with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f: json.dump(data, f)
    return redirect(url_for('index'))

@app.route('/clear')
def clear():
    if os.path.exists(WATCHLIST_FILE): os.remove(WATCHLIST_FILE)
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
