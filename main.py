import os, json, numpy as np, yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 核心配置：根據主委提供的全產業清單進行佈局 (龍頭 + 關聯 + 子公司) ---
INDUSTRY_MAP = {
    "電子/半導體/AI": ["2330.TW", "2454.TW", "2317.TW", "2308.TW", "2337.TW", "3443.TW", "6669.TW", "3231.TW", "2382.TW", "2376.TW", "3037.TW", "2367.TW"],
    "通信/光電/週邊": ["2409.TW", "2412.TW", "4958.TW", "3045.TW", "2345.TW"],
    "航運/油電/塑膠": ["2603.TW", "2609.TW", "2615.TW", "2618.TW", "9933.TW", "1301.TW", "6505.TW"],
    "金融/觀光/營建": ["2881.TW", "2882.TW", "5880.TW", "2707.TW", "2542.TW", "9945.TW"],
    "生技/重電/能源": ["1760.TWO", "4147.TWO", "6472.TWO", "1513.TW", "1519.TW", "1503.TW", "6806.TW"]
}

def get_market_intel():
    """總經戰略分析系統"""
    intel = {"twii": "---", "otc": "---", "sentiment": "掃描中", "strategy": "數據抓取", "threshold": 84, "vix": "--", "usd": "--"}
    try:
        # 強制獲取指數與即時匯率
        intel["twii"] = f"{yf.Ticker('^TWII').fast_info['last_price']:,.0f}"
        intel["otc"] = f"{yf.Ticker('^TWOII').fast_info['last_price']:.2f}"
        vix = yf.Ticker("^VIX").fast_info['last_price']
        usd = yf.Ticker("TWD=X").fast_info['last_price']
        intel["vix"], intel["usd"] = round(vix, 2), round(usd, 2)
        
        # 決定 AI 門檻
        if vix > 22 or usd > 32.7:
            intel["sentiment"], intel["strategy"], intel["threshold"] = "空頭防禦", "保守減碼 / 避險", 88
        elif vix < 16 and usd < 32.2:
            intel["sentiment"], intel["strategy"], intel["threshold"] = "多頭進攻", "全面參與 / 追蹤", 80
        else:
            intel["sentiment"], intel["strategy"], intel["threshold"] = "中性震盪", "產業輪動 / 區間", 84
    except: pass
    return intel

def analyze_inertia(symbol, threshold):
    """執行 K 線慣性大數據比對"""
    try:
        df = yf.download(symbol, period="5y", interval="1d", progress=False)
        if df.empty or len(df) < 120: return None
        close = df['Close'].ffill().values.flatten()
        target = close[-20:]
        def norm(arr):
            s = np.std(arr); return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        target_n = norm(target)
        max_c = -1
        # 遍歷五年歷史數據
        for i in range(0, len(close) - 40, 5):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_c: max_c = corr
        score = round(max_c * 100, 2)
        if score >= threshold:
            curr_p = round(close[-1], 2)
            ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
            inv_type = "長線(趨勢)" if curr_p > ma60 else "短線(轉折)"
            return {"symbol": symbol, "score": score, "type": inv_type, "price": curr_p}
    except: return None

@app.route('/')
def index():
    intel = get_market_intel()
    # 進行「全產業矩陣」深度掃描
    all_recs = {}
    for industry, symbols in INDUSTRY_MAP.items():
        recs = [res for s in symbols if (res := analyze_inertia(s, intel["threshold"]))]
        if recs:
            all_recs[industry] = sorted(recs, key=lambda x: x['score'], reverse=True)
    
    tracked = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            for item in json.load(f):
                try:
                    p = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
                    profit = round(((p - item['buy_price']) / item['buy_price']) * 100, 2)
                    tracked.append({**item, "curr_p": p, "profit": profit})
                except: pass
    return render_template('index.html', intel=intel, all_recs=all_recs, tracked=tracked, now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

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
