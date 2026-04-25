import os, json, numpy as np, yfinance as yf, requests
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 配置區：主委核心選股名單與備援名稱 ---
STOCK_POOL = ["2330.TW", "2317.TW", "1802.TW", "2603.TW", "3231.TW", "0050.TW", "2409.TW", "2367.TW", "4958.TW", "2303.TW", "1513.TW"]
BACKUP_NAMES = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "1802.TW": "台玻", "2603.TW": "長榮", 
    "3231.TW": "緯創", "0050.TW": "元大台灣50", "2409.TW": "友達", "2303.TW": "聯電",
    "2367.TW": "燿華", "4958.TW": "臻鼎-KY", "1513.TW": "中興電"
}

def get_display_name(symbol):
    if symbol in BACKUP_NAMES: return BACKUP_NAMES[symbol]
    try:
        t = yf.Ticker(symbol)
        return t.info.get('shortName', symbol.split('.')[0])
    except: return symbol.split('.')[0]

# --- 核心邏輯：強化慣性偵測 ---
def analyze_inertia(symbol, threshold=80):
    try:
        # 下載歷史數據
        df = yf.download(symbol, period="10y", interval="1d", progress=False)
        if df.empty or len(df) < 100: return None
        
        close = df['Close'].ffill().values.flatten()
        target = close[-20:]
        
        # 相似度計算
        def norm(arr):
            s = np.std(arr)
            return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        
        target_n = norm(target)
        max_c = -1
        for i in range(0, len(close) - 60, 5):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_c: max_c = corr
        
        score = round(max_c * 100, 2)

        # 根據簡報資料強化的慣性邏輯
        # 1. 趨勢慣性：20日線方向
        ma20 = df['Close'].rolling(window=20).mean()
        is_up_trend = ma20.iloc[-1] > ma20.iloc[-5]
        
        # 2. 支撐慣性：今日低點不破前三日支撐
        is_supported = close[-1] >= np.min(close[-3:])
        
        if score >= threshold:
            # 綜合判定建議
            if score >= 88 and is_up_trend:
                advice = "🚀 強勢噴出慣性"
            elif is_supported:
                advice = "📈 支撐轉折慣性"
            else:
                advice = "🔎 慣性醞釀中"
                
            return {
                "symbol": symbol, 
                "name": get_display_name(symbol), 
                "score": score, 
                "advice": advice,
                "is_hot": score >= 88
            }
        return None
    except: return None

@app.route('/')
def index():
    # 1. 抓取雙指數
    indices = {"twii": "載入中", "otc": "載入中"}
    try:
        indices["twii"] = round(yf.Ticker("^TWII").fast_info['last_price'], 2)
        indices["otc"] = round(yf.Ticker("^TWOII").fast_info['last_price'], 2)
    except: pass

    # 2. 雙門檻掃描：解決 NO CONTENT FOUND
    recs = [res for s in STOCK_POOL if (res := analyze_inertia(s, 82))]
    if not recs:
        recs = [res for s in STOCK_POOL if (res := analyze_inertia(s, 72))]
    
    # 3. 損益追蹤
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

    return render_template('index.html', recs=recs, tracked=tracked_list, indices=indices, now=datetime.now().strftime("%m/%d %H:%M"))

@app.route('/add/<symbol>/<name>')
def add(symbol, name):
    try:
        price = round(yf.Ticker(symbol).fast_info['last_price'], 2)
        data = []
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f: data = json.load(f)
        if not any(x['symbol'] == symbol for x in data):
            data.append({"symbol": symbol, "name": name, "buy_price": price, "date": datetime.now().strftime("%m/%d")})
            with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f: json.dump(data, f)
    except: pass
    return redirect(url_for('index'))

@app.route('/clear')
def clear():
    if os.path.exists(WATCHLIST_FILE): os.remove(WATCHLIST_FILE)
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
