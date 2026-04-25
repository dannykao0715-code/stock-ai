import os, json, numpy as np, yfinance as yf, requests
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 核心配置：全球監控指標 ---
GLOBAL_INDICATORS = {
    "VIX": "^VIX",       # 恐慌指數 (風險意識)
    "US10Y": "^TNX",    # 美債10年期 (資金流向)
    "GOLD": "GC=F",      # 黃金 (避險情緒)
    "OIL": "CL=F",       # 原油 (通膨、航運成本)
    "USD/TWD": "TWD=X"   # 匯率 (外資動向)
}

# --- 備援名稱字典 (持續擴充) ---
BACKUP_NAMES = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "1802.TW": "台玻", "2603.TW": "長榮",
    "2303.TW": "聯電", "2409.TW": "友達", "4958.TW": "臻鼎-KY", "1513.TW": "中興電"
}

def get_market_sentiment():
    """總經決策：判斷目前市場位階"""
    try:
        vix = yf.Ticker("^VIX").fast_info['last_price']
        us10y = yf.Ticker("^TNX").fast_info['last_price']
        # 簡單邏輯：VIX > 20 代表恐慌，選股應偏向長線穩健；VIX < 15 代表多頭活躍，可選短線噴發
        if vix > 20: return "空頭防守", "長線/避險"
        return "多頭進攻", "短線噴發/趨勢"
    except: return "中性觀望", "波段操作"

def analyze_stock_full(symbol, market_mode):
    """結合個股慣性與總經模式選股"""
    try:
        df = yf.download(symbol, period="10y", interval="1d", progress=False)
        if df.empty or len(df) < 250: return None
        
        close = df['Close'].ffill().values.flatten()
        target = close[-20:]
        
        # 相似度比對
        def norm(arr):
            s = np.std(arr)
            return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        
        target_n = norm(target)
        max_c = -1
        for i in range(0, len(close) - 60, 5):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_c: max_c = corr
        score = round(max_c * 100, 2)

        # 根據模式設定動態門檻
        min_threshold = 82 if market_mode == "多頭進攻" else 88 # 空頭時要求更嚴謹
        
        if score >= min_threshold:
            # 加入短期/長期慣性判斷 (依據收盤價站穩均線天數)
            ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
            investment_type = "短線(3M)" if close[-1] > ma60 * 1.1 else "長線(6M+)"
            
            return {
                "symbol": symbol, "name": BACKUP_NAMES.get(symbol, symbol.split('.')[0]),
                "score": score, "type": investment_type, 
                "advice": "符合慣性重演" if score >= 85 else "觀察中"
            }
        return None
    except: return None

@app.route('/')
def index():
    sentiment, inv_strategy = get_market_sentiment()
    
    # 動態掃描清單 (這部分可隨產業輪動更新)
    current_focus = ["2330.TW", "2317.TW", "2603.TW", "1513.TW", "2409.TW", "2303.TW", "3231.TW", "2367.TW"]
    
    recs = []
    for s in current_focus:
        res = analyze_stock_full(s, sentiment)
        if res: recs.append(res)
    
    # 損益追蹤與回測思維 (此處暫存 watchlist)
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
                           strategy=inv_strategy, tracked=tracked_list, now=datetime.now().strftime("%Y-%m-%d"))

# add 與 clear 路由同前...
