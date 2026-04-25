import os, json, numpy as np, yfinance as yf
from flask import Flask, render_template, redirect, url_for, request
from datetime import datetime, timedelta

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 1. 全球宏觀監控清單 ---
MACRO_TICKERS = {
    "VIX": "^VIX",         # 恐慌指數 (風險指標)
    "US10Y": "^TNX",       # 美債10年期 (資金成本)
    "USD_TWD": "TWD=X",    # 台幣匯率 (外資動向)
    "GOLD": "GC=F",        # 黃金 (避險情緒)
    "OIL": "CL=F"          # 原油 (通膨/成本)
}

# --- 2. 名稱備援與產業池 ---
# 系統會動態掃描這些核心產業標的
BACKUP_NAMES = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科",
    "2603.TW": "長榮", "2609.TW": "陽明", "1513.TW": "中興電",
    "2308.TW": "台達電", "2382.TW": "廣達", "3231.TW": "緯創",
    "2409.TW": "友達", "1802.TW": "台玻", "0050.TW": "元大台灣50"
}

def get_market_context():
    """總體經濟判斷：決定目前市場情緒"""
    try:
        vix = yf.Ticker("^VIX").fast_info['last_price']
        us10y = yf.Ticker("^TNX").fast_info['last_price']
        
        if vix > 22:
            return "空頭防守", "側重高殖利率/避險個股", 88 # 門檻調高
        elif vix < 16:
            return "多頭進攻", "側重強勢慣性/噴發個股", 80 # 門檻調低
        else:
            return "中性震盪", "波段操作/區間慣性", 84
    except:
        return "數據連線中", "謹慎操作", 82

def analyze_logic(symbol, threshold):
    """整合 K 線慣性與均線斜率"""
    try:
        df = yf.download(symbol, period="10y", interval="1d", progress=False)
        if df.empty or len(df) < 100: return None
        
        close = df['Close'].ffill().values.flatten()
        target = close[-20:]
        
        # 相似度比對 (AI 部分)
        def norm(arr):
            s = np.std(arr); return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        
        target_n = norm(target)
        max_c = -1
        for i in range(0, len(close) - 60, 5):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_c: max_c = corr
        score = round(max_c * 100, 2)

        # 趨勢慣性 (MA20 斜率)
        ma20 = df['Close'].rolling(window=20).mean()
        is_up = ma20.iloc[-1] > ma20.iloc[-3]
        
        # 支撐慣性 (今日收盤不破昨日低點)
        is_supported = close[-1] >= df['Low'].iloc[-2]

        if score >= threshold:
            name = BACKUP_NAMES.get(symbol, symbol.split('.')[0])
            # 長短線判斷
            ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
            inv_type = "長線優選" if close[-1] > ma60 else "短線轉折"
            
            return {
                "symbol": symbol, "name": name, "score": score, 
                "advice": "🚀 強慣性" if is_up else "📈 穩支撐",
                "type": inv_type, "is_hot": score >= 88
            }
        return None
    except: return None

@app.route('/')
def index():
    sentiment, strategy, dynamic_threshold = get_market_context()
    
    # 執行掃描 (主委關注池)
    scan_list = list(BACKUP_NAMES.keys())
    recs = [res for s in scan_list if (res := analyze_logic(s, dynamic_threshold))]
    
    # 排序：相似度高優先
    recs = sorted(recs, key=lambda x: x['score'], reverse=True)

    # 損益追蹤與回測
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

    return render_template('index.html', recs=recs, sentiment=sentiment, 
                           strategy=strategy, tracked=tracked_list, 
                           now=datetime.now().strftime("%Y-%m-%d"))

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
