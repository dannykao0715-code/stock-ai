import os, json, numpy as np, yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 1. 總經指標：決定「天時」 ---
def get_market_sentiment():
    try:
        # 抓取關鍵總經數據
        vix = yf.Ticker("^VIX").fast_info['last_price']        # 恐慌指數
        usd_twd = yf.Ticker("TWD=X").fast_info['last_price']  # 台幣匯率
        us10y = yf.Ticker("^TNX").fast_info['last_price']     # 美債殖利率
        oil = yf.Ticker("CL=F").fast_info['last_price']       # 原油 (通膨/成本)
        
        # 邏輯：匯率貶值 (>32.5) 或 VIX 高 (>20) 則防守
        if vix > 20 or usd_twd > 32.5:
            return "空頭防守", "挑選長線抗跌標的", 88
        return "多頭進攻", "挑選短線爆發標的", 82
    except:
        return "數據獲取中", "波段操作", 85

# --- 2. 核心分析：慣性與回測邏輯 ---
def analyze_stock(symbol, threshold):
    try:
        df = yf.download(symbol, period="5y", interval="1d", progress=False)
        if df.empty or len(df) < 250: return None
        
        close = df['Close'].ffill().values.flatten()
        target = close[-20:] # 當前 20 日慣性
        
        # AI 形態相似度比對
        def norm(arr):
            s = np.std(arr)
            return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        
        target_n = norm(target)
        max_c = -1
        # 掃描歷史：尋找 10 年內最強慣性重演
        for i in range(0, len(close) - 40, 5):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_c: max_c = corr
        
        score = round(max_c * 100, 2)
        
        if score >= threshold:
            # 加入長短線判斷
            ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
            ma250 = df['Close'].rolling(window=250).mean().iloc[-1]
            curr_p = round(close[-1], 2)
            
            # 判斷投資屬性
            if curr_p > ma60 and ma60 > ma250:
                inv_type = "長線(穩健)"
            else:
                inv_type = "短線(轉折)"
                
            return {"symbol": symbol, "score": score, "type": inv_type, "price": curr_p}
        return None
    except: return None

@app.route('/')
def index():
    sentiment, strategy, threshold = get_market_sentiment()
    
    # 擴大海選池：包含半導體、AI、航運、金融、重電等各產業龍頭 (可持續擴充)
    stock_pool = [
        "2330.TW", "2317.TW", "2454.TW", "2603.TW", "1513.TW", "2382.TW", "3231.TW", 
        "2409.TW", "2303.TW", "2609.TW", "2881.TW", "2882.TW", "2308.TW", "2357.TW",
        "1503.TW", "1519.TW", "2367.TW", "4958.TW", "3037.TW", "2376.TW", "6235.TWO"
    ]
    
    recs = []
    for s in stock_pool:
        res = analyze_stock(s, threshold)
        if res: recs.append(res)
    
    recs = sorted(recs, key=lambda x: x['score'], reverse=True)

    # 追蹤與回測數據
    tracked_list = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            for item in json.load(f):
                try:
                    p = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
                    profit = round(((p - item['buy_price']) / item['buy_price']) * 100, 2)
                    tracked_list.append({**item, "curr_p": p, "profit": profit})
                except: pass

    return render_template('index.html', recs=recs, sentiment=sentiment, 
                           strategy=strategy, tracked=tracked_list, now=datetime.now().strftime("%Y-%m-%d"))

@app.route('/add/<symbol>/<float:price>')
def add(symbol, price):
    data = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f: data = json.load(f)
    if not any(x['symbol'] == symbol for x in data):
        data.append({"symbol": symbol, "buy_price": price, "date": datetime.now().strftime("%m/%d")})
        with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f: json.dump(data, f)
    return redirect(url_for('index'))

@app.route('/clear')
def clear():
    if os.path.exists(WATCHLIST_FILE): os.remove(WATCHLIST_FILE)
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
