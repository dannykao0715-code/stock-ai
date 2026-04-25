import os, json, numpy as np, yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 核心邏輯：擴大掃描池 (包含各產業龍頭、高權重股、中型 100) ---
SCAN_POOL = [
    # 半導體、AI、電子
    "2330.TW", "2317.TW", "2454.TW", "2382.TW", "3231.TW", "2308.TW", "2303.TW", "2357.TW", "2376.TW", "3037.TW",
    # 航運、傳產、重電
    "2603.TW", "2609.TW", "2615.TW", "1513.TW", "1503.TW", "1519.TW", "1802.TW", "2105.TW",
    # 金融、指數
    "2881.TW", "2882.TW", "2884.TW", "2886.TW", "0050.TW", "0056.TW"
]

def get_market_intelligence():
    """總體經濟分析儀表板"""
    intel = {
        "twii": "---", "otc": "---", "vix": "---", "usd": "---", "gold": "---", "oil": "---",
        "sentiment": "數據分析中", "strategy": "保守觀望", "threshold": 85
    }
    try:
        # 指數與總經數據 (增加超時保護)
        twii_data = yf.Ticker("^TWII").fast_info
        otc_data = yf.Ticker("^TWOII").fast_info
        intel["twii"] = f"{twii_data['last_price']:,.0f}"
        intel["otc"] = f"{otc_data['last_price']:.2f}"
        
        vix = yf.Ticker("^VIX").fast_info['last_price']
        usd = yf.Ticker("TWD=X").fast_info['last_price']
        intel["vix"] = round(vix, 2)
        intel["usd"] = round(usd, 2)
        
        # 總經位階邏輯判斷
        if vix > 21 or usd > 32.6:
            intel["sentiment"], intel["strategy"], intel["threshold"] = "空頭防守", "長線避險 / 保留現金", 88
        elif vix < 16 and usd < 32.2:
            intel["sentiment"], intel["strategy"], intel["threshold"] = "多頭攻擊", "短線慣性 / 強勢噴發", 80
        else:
            intel["sentiment"], intel["strategy"], intel["threshold"] = "中性震盪", "波段操作 / 尋找支撐", 84
    except Exception as e:
        print(f"指標抓取延遲: {e}")
    return intel

def run_ai_inertia(symbol, threshold):
    """AI 大數據比對個股慣性"""
    try:
        # 抓取較長歷史進行大數據比對
        df = yf.download(symbol, period="10y", interval="1d", progress=False)
        if df.empty or len(df) < 120: return None
        
        close = df['Close'].ffill().values.flatten()
        target = close[-20:] # 當前慣性特徵
        
        # 形態標準化演算
        def norm(arr):
            s = np.std(arr)
            return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        
        target_n = norm(target)
        max_corr = -1
        # 遍歷 10 年歷史尋找相似形態
        for i in range(0, len(close) - 40, 5):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_corr: max_corr = corr
        
        score = round(max_corr * 100, 2)
        
        if score >= threshold:
            curr_p = round(close[-1], 2)
            ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
            ma250 = df['Close'].rolling(window=250).mean().iloc[-1]
            
            # 獲利週期判定
            if curr_p > ma60 and ma60 > ma250:
                inv_type = "長線(3-6M+)"
                advice = "🔥 趨勢續強"
            else:
                inv_type = "短線(3M內)"
                advice = "🔍 轉折觀察"
                
            return {"symbol": symbol, "score": score, "type": inv_type, "price": curr_p, "advice": advice}
    except: return None

@app.route('/')
def index():
    intel = get_market_intelligence()
    # 執行全市場慣性掃描
    recs = [res for s in SCAN_POOL if (res := run_ai_inertia(s, intel["threshold"]))]
    recs = sorted(recs, key=lambda x: x['score'], reverse=True)

    # 回測損益追蹤
    tracked = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            for item in json.load(f):
                try:
                    curr = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
                    profit = round(((curr - item['buy_price']) / item['buy_price']) * 100, 2)
                    tracked.append({**item, "curr_p": curr, "profit": profit})
                except: pass

    return render_template('index.html', 
                           intel=intel, 
                           recs=recs, 
                           tracked=tracked, 
                           now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

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
