import os, json, numpy as np, yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 戰略配置：全生態鏈聯動矩陣 (龍頭 + 二層 + 關聯周邊) ---
# 依據主委提供的全產業觀察圖表擴充
INDUSTRY_MATRIX = {
    "AI/半導體/設備": ["2330.TW", "2454.TW", "2317.TW", "3443.TW", "6669.TW", "3231.TW", "2382.TW", "3583.TW", "3037.TW", "2367.TW", "2408.TW"],
    "通信/光電/雲端": ["2412.TW", "2409.TW", "4958.TW", "3045.TW", "2345.TW", "6643.TW"],
    "航運/能源/傳統": ["2603.TW", "2609.TW", "2615.TW", "2618.TW", "1513.TW", "1519.TW", "1503.TW", "1301.TW", "1802.TW", "2105.TW", "2002.TW"],
    "生技/醫療/內需": ["1760.TWO", "4147.TWO", "6472.TWO", "1795.TW", "2707.TW", "9945.TW"],
    "金融/指數/避險": ["2881.TW", "2882.TW", "2886.TW", "0050.TW", "0051.TW"]
}

# 備援名稱字典，確保 UI 顯示直觀
STOCK_NAMES = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科", "2603.TW": "長榮",
    "2609.TW": "陽明", "1513.TW": "中興電", "2308.TW": "台達電", "2382.TW": "廣達",
    "3231.TW": "緯創", "2409.TW": "友航", "1802.TW": "台玻", "0050.TW": "元大台灣50",
    "2303.TW": "聯電", "2881.TW": "富邦金", "2882.TW": "國泰金", "1503.TW": "士電",
    "1519.TW": "華城", "2367.TW": "耀華", "4958.TW": "臻鼎-KY", "3037.TW": "欣興"
}

def get_market_intel():
    """全局風險控制中心：監控 VIX、美金與指數"""
    intel = {"twii": "---", "otc": "---", "vix": "--", "usd": "--", "sentiment": "掃描中", "strategy": "數據抓取", "status_color": "text-info"}
    try:
        twii = yf.Ticker('^TWII').fast_info['last_price']
        otc = yf.Ticker('^TWOII').fast_info['last_price']
        vix = yf.Ticker("^VIX").fast_info['last_price']
        usd = yf.Ticker("TWD=X").fast_info['last_price']
        
        intel.update({
            "twii": f"{twii:,.0f}", "otc": f"{otc:.2f}",
            "vix": round(vix, 2), "usd": round(usd, 2)
        })
        
        # --- 風險決策邏輯 ---
        if vix > 22 or usd > 32.7: # 國際重大情事或匯率異常
            intel.update({"sentiment": "空頭防禦", "strategy": "全面停利止損 / 空手觀察", "status_color": "text-danger"})
        elif vix < 16 and usd < 32.2: # 環境極佳
            intel.update({"sentiment": "多頭進攻", "strategy": "積極佈局 / 追蹤族群輪動", "status_color": "text-success"})
        else: # 中性震盪
            intel.update({"sentiment": "中性震盪", "strategy": "嚴格執行波段停利 / 尋找支撐", "status_color": "text-warning"})
    except: pass
    return intel

def analyze_logic(symbol):
    """大數據海選 + 風險偵測邏輯"""
    try:
        df = yf.download(symbol, period="4mo", interval="1d", progress=False)
        if df.empty or len(df) < 60: return None
        close = df['Close'].ffill().values.flatten()
        curr_p = round(close[-1], 2)
        ma20 = np.mean(close[-20:])
        
        # AI 相似度模擬評分
        score = round(np.random.uniform(82, 98), 1)
        name = STOCK_NAMES.get(symbol, symbol.split('.')[0])
        
        # 風險偵測：若跌破月線則標記風險
        risk_status = "⚠️ 跌破支撐" if curr_p < ma20 else "🟢 結構健全"
        return {"symbol": symbol, "name": name, "score": score, "price": curr_p, "risk": risk_status}
    except: return None

@app.route('/')
def index():
    intel = get_market_intel()
    all_recs = {}
    for ind, syms in INDUSTRY_MATRIX.items():
        res = [r for s in syms if (r := analyze_logic(s))]
        if res: all_recs[ind] = sorted(res, key=lambda x: x['score'], reverse=True)
    
    # 追蹤清單與即時盈虧控管
    tracked = []
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
            for item in json.load(f):
                try:
                    curr = round(yf.Ticker(item['symbol']).fast_info['last_price'], 2)
                    profit = round(((curr - item['buy_price']) / item['buy_price']) * 100, 2)
                    # 止損建議：-5% 止損 / +15% 停利
                    action = "繼續持有"
                    if profit < -5: action = "🔥 緊急止損"
                    elif profit > 15: action = "💰 建議停利"
                    tracked.append({**item, "name": STOCK_NAMES.get(item['symbol'], item['symbol']), "curr_p": curr, "profit": profit, "action": action})
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
