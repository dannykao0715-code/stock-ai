import os, json, numpy as np, yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 核心字典：確保名稱永遠正確 ---
STOCK_NAMES = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科", "2603.TW": "長榮",
    "2609.TW": "陽明", "1513.TW": "中興電", "2308.TW": "台達電", "2382.TW": "廣達",
    "3231.TW": "緯創", "2409.TW": "友達", "1802.TW": "台玻", "0050.TW": "元大台灣50",
    "2303.TW": "聯電", "2881.TW": "富邦金", "2882.TW": "國泰金", "1503.TW": "士電",
    "1519.TW": "華城", "2367.TW": "燿華", "4958.TW": "臻鼎-KY", "3037.TW": "欣興"
}

def get_market_data():
    """獲取總經大數據並判斷位階"""
    data = {
        "twii": "N/A", "otc": "N/A", "vix": 0, "usd": 0,
        "sentiment": "數據連線中", "strategy": "波段操作", "threshold": 84
    }
    try:
        # 指數與關鍵指標
        data["twii"] = round(yf.Ticker("^TWII").fast_info['last_price'], 2)
        data["otc"] = round(yf.Ticker("^TWOII").fast_info['last_price'], 2)
        vix = yf.Ticker("^VIX").fast_info['last_price']
        usd = yf.Ticker("TWD=X").fast_info['last_price']
        
        # 總經決策邏輯
        if vix > 21 or usd > 32.6:
            data["sentiment"], data["strategy"], data["threshold"] = "空頭避險", "防守策略 / 長線價值", 88
        elif vix < 16 and usd < 32.2:
            data["sentiment"], data["strategy"], data["threshold"] = "多頭攻擊", "短線噴發 / 強勢慣性", 80
        else:
            data["sentiment"], data["strategy"], data["threshold"] = "中性震盪", "區間操作 / 支撐轉折", 84
    except:
        pass
    return data

def analyze_logic(symbol, threshold):
    """執行 K 線慣性大數據比對"""
    try:
        df = yf.download(symbol, period="5y", interval="1d", progress=False)
        if df.empty or len(df) < 120: return None
        
        close = df['Close'].ffill().values.flatten()
        target = close[-20:] # 當前慣性
        
        def norm(arr):
            s = np.std(arr)
            return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        
        target_n = norm(target)
        max_c = -1
        # 全量掃描歷史慣性
        for i in range(0, len(close) - 40, 5):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_c: max_c = corr
        score = round(max_c * 100, 2)

        if score >= threshold:
            ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
            ma250 = df['Close'].rolling(window=250).mean().iloc[-1]
            curr_p = round(close[-1], 2)
            # 判斷長短線
            inv_type = "長線優選" if curr_p > ma60 and ma60 > ma250 else "短線轉折"
            # 支撐判斷：今日最低不破昨日最低
            advice = "🔥 慣性噴發" if curr_p > df['Close'].iloc[-2] else "🛡️ 支撐確認"
            
            return {
                "symbol": symbol, "name": STOCK_NAMES.get(symbol, symbol.split('.')[0]),
                "score": score, "type": inv_type, "price": curr_p, "advice": advice
            }
    except:
        return None

@app.route('/')
def index():
    market = get_market_data()
    # 海選池 (自動包含所有定義在字典中的龍頭股)
    pool = list(STOCK_NAMES.keys())
    recs = [res for s in pool if (res := analyze_logic(s, market["threshold"]))]
    recs = sorted(recs, key=lambda x: x['score'], reverse=True)

    # 損益追蹤
    tracked = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            for item in json.load(f):
                try:
                    curr = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
                    profit = round(((curr - item['buy_price']) / item['buy_price']) * 100, 2)
                    tracked.append({**item, "curr_p": curr, "profit": profit})
                except: pass

    return render_template('index.html', market=market, recs=recs, tracked=tracked, 
                           now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

@app.route('/add/<symbol>/<name>/<float:price>')
def add(symbol, name, price):
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
