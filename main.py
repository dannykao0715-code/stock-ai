import os, requests, json, time
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# 備援名稱字典，防止 API 沒抓到中文
BACKUP_NAMES = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "1802.TW": "台玻",
    "2603.TW": "長榮", "3231.TW": "緯創", "0050.TW": "元大台灣50",
    "^TWII": "加權指數", "^TWOII": "櫃買指數"
}

def get_stock_display_name(symbol):
    """多重機制抓取中文名稱"""
    clean_id = symbol.split('.')[0]
    # 1. 檢查備援清單
    if symbol in BACKUP_NAMES: return BACKUP_NAMES[symbol]
    try:
        # 2. 嘗試從 yfinance 抓取
        t = yf.Ticker(symbol)
        name = t.info.get('shortName', t.info.get('longName', clean_id))
        return name
    except: return clean_id

def analyze_stock(symbol):
    try:
        df = yf.download(symbol, period="10y", interval="1d", progress=False)
        if df.empty or len(df) < 100: return None
        
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
        name = get_stock_display_name(symbol)
        
        # 主委邏輯：相似度 > 88% 且假定籌碼集中
        advice = "🚀 極致看好" if score >= 88 else "📈 慣性重演" if score >= 80 else "🔎 觀望"
        return {"symbol": symbol, "name": name, "score": score, "advice": advice}
    except: return None

@app.route('/')
def index():
    # 抓取雙指數
    indices = {"twii": "載入中", "otc": "載入中"}
    try:
        indices["twii"] = round(yf.Ticker("^TWII").fast_info['last_price'], 2)
        indices["otc"] = round(yf.Ticker("^TWOII").fast_info['last_price'], 2)
    except: pass

    # 推薦名單
    stocks = ["2330.TW", "2317.TW", "1802.TW", "2603.TW", "3231.TW", "0050.TW"]
    recs = [res for s in stocks if (res := analyze_stock(s))]
    
    # 追蹤損益
    watchlist = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f: watchlist = json.load(f)
    
    tracked_list = []
    for item in watchlist:
        try:
            curr = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
            profit = round(((curr - item['buy_price']) / item['buy_price']) * 100, 2)
            tracked_list.append({**item, "curr_p": curr, "profit": profit})
        except: pass

    return render_template('index.html', recs=recs, tracked=tracked_list, indices=indices, now=datetime.now().strftime("%Y-%m-%d %H:%M"))

@app.route('/add/<symbol>/<name>')
def add(symbol, name):
    price = round(yf.Ticker(symbol).fast_info['last_price'], 2)
    data = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f: data = json.load(f)
    if not any(x['symbol'] == symbol for x in data):
        data.append({"symbol": symbol, "name": name, "buy_price": price, "date": datetime.now().strftime("%m/%d")})
        with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f: json.dump(data, f)
    return redirect(url_for('index'))

@app.route('/clear')
def clear():
    if os.path.exists(WATCHLIST_FILE): os.remove(WATCHLIST_FILE)
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
