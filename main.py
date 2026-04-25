import os, json, numpy as np, yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 核心字典：對齊截圖中的全產業觀察名單 ---
INDUSTRY_MAP = {
    "電子/半導體/AI": ["2330.TW", "2454.TW", "2317.TW", "2308.TW", "3443.TW", "6669.TW", "3231.TW", "2382.TW", "2376.TW", "3037.TW"],
    "通信/光電/記憶體": ["2409.TW", "2412.TW", "2303.TW", "2408.TW", "3260.TWO", "4958.TW"],
    "航運/能源/傳統": ["2603.TW", "2609.TW", "2615.TW", "1513.TW", "1519.TW", "1503.TW", "1802.TW", "1301.TW"],
    "生技/金融/營建": ["1760.TWO", "4147.TWO", "6472.TWO", "2881.TW", "2882.TW", "2542.TW", "9945.TW"]
}

def get_market_intel():
    intel = {"twii": "---", "otc": "---", "sentiment": "掃描中", "strategy": "數據抓取", "threshold": 84}
    try:
        # 修正加權/櫃買數據抓取，確保數值呈現
        twii_price = yf.Ticker('^TWII').fast_info['last_price']
        otc_price = yf.Ticker('^TWOII').fast_info['last_price']
        intel["twii"] = f"{twii_price:,.0f}"
        intel["otc"] = f"{otc_price:.2f}"
        
        vix = yf.Ticker("^VIX").fast_info['last_price']
        usd = yf.Ticker("TWD=X").fast_info['last_price']
        
        if vix > 22 or usd > 32.7:
            intel["sentiment"], intel["strategy"], intel["threshold"] = "空頭防禦", "保守減碼 / 避險", 88
        elif vix < 16 and usd < 32.2:
            intel["sentiment"], intel["strategy"], intel["threshold"] = "多頭進攻", "全面參與 / 追蹤", 80
        else:
            intel["sentiment"], intel["strategy"], intel["threshold"] = "中性震盪", "產業輪動 / 區間", 84
    except: pass
    return intel

def analyze_logic(symbol, threshold):
    try:
        df = yf.download(symbol, period="5y", interval="1d", progress=False)
        if df.empty or len(df) < 60: return None
        close = df['Close'].ffill().values.flatten()
        target_n = (close[-20:] - np.mean(close[-20:])) / (np.std(close[-20:]) + 1e-9)
        max_c = -1
        for i in range(0, len(close)-40, 5):
            win = close[i:i+20]
            corr = np.corrcoef(target_n, (win - np.mean(win))/(np.std(win)+1e-9))[0,1]
            if corr > max_c: max_c = corr
        score = round(max_c * 100, 2)
        if score >= threshold:
            curr = round(close[-1], 2)
            ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
            return {"symbol": symbol, "score": score, "type": "趨勢" if curr > ma60 else "轉折", "price": curr}
    except: return None

@app.route('/')
def index():
    intel = get_market_intel()
    all_recs = {}
    for ind, syms in INDUSTRY_MAP.items():
        res = [r for s in syms if (r := analyze_logic(s, intel["threshold"]))]
        if res: all_recs[ind] = sorted(res, key=lambda x: x['score'], reverse=True)
    
    tracked = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            for item in json.load(f):
                try:
                    curr = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
                    profit = round(((curr - item['buy_price']) / item['buy_price']) * 100, 2)
                    tracked.append({**item, "curr_p": curr, "profit": profit})
                except: pass
    return render_template('index.html', intel=intel, all_recs=all_recs, tracked=tracked)

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
