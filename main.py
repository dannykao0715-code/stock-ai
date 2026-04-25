import os, json, numpy as np, yfinance as yf
from flask import Flask, render_template, redirect, url_for, request
from datetime import datetime

app = Flask(__name__)
DB_FILE = 'trading_db.json' # 儲存模擬交易數據

# --- 全生態鏈掃描池 (含龍頭、子公司、周邊) ---
SCAN_POOL = [
    "2330.TW", "2454.TW", "2317.TW", "3443.TW", "6669.TW", "3231.TW", "2382.TW", "3583.TW", "3037.TW", # AI/半導體
    "2603.TW", "2609.TW", "2618.TW", "1513.TW", "1519.TW", "1503.TW", # 航運/重電
    "2881.TW", "2882.TW", "1795.TW", "6472.TWO", "9945.TW" # 金融/生技/營建
]

STOCK_NAMES = {
    "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科", "2603.TW": "長榮",
    "1513.TW": "中興電", "3231.TW": "緯創", "2382.TW": "廣達", "3583.TW": "辛耘"
}

def get_market_condition():
    """判斷股市走向：回傳 多頭/中性/空頭 與 對應的選股門檻"""
    try:
        vix = yf.Ticker("^VIX").fast_info['last_price']
        usd = yf.Ticker("TWD=X").fast_info['last_price']
        if vix > 22 or usd > 32.7: return "空頭防禦", 92  # 門檻提高，精選避險標的
        if vix < 17: return "多頭進攻", 85               # 門檻降低，抓取動能標的
        return "中性震盪", 88
    except: return "未知", 90

@app.route('/')
def index():
    cond, threshold = get_market_condition()
    
    # 讀取模擬交易紀錄並更新損益
    trades = []
    total_pnl = 0
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            raw_trades = json.load(f)
            for t in raw_trades:
                try:
                    curr_p = yf.Ticker(t['symbol']).fast_info['last_price']
                    pnl = (curr_p - t['buy_price']) / t['buy_price'] * 100
                    t.update({"curr_p": round(curr_p, 2), "pnl": round(pnl, 2)})
                    total_pnl += pnl
                    trades.append(t)
                except: pass
    
    # 檢查是否有啟動選股指令
    recommendations = []
    if request.args.get('scan') == 'true':
        for s in SCAN_POOL:
            try:
                # 簡單 AI 邏輯：近期強勢度
                hist = yf.download(s, period="1mo", progress=False)['Close']
                strength = ((hist.iloc[-1] - hist.iloc[0]) / hist.iloc[0]) * 100
                score = 80 + strength # 模擬 AI 評分
                if score > threshold:
                    recommendations.append({
                        "symbol": s, "name": STOCK_NAMES.get(s, s),
                        "price": round(hist.iloc[-1], 2), "score": round(score, 1)
                    })
            except: pass
            
    return render_template('index.html', cond=cond, trades=trades, 
                           recs=recommendations, total_pnl=round(total_pnl, 2))

@app.route('/track/<symbol>/<float:price>')
def track(symbol, price):
    data = []
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f: data = json.load(f)
    if not any(x['symbol'] == symbol for x in data):
        data.append({
            "symbol": symbol, "name": STOCK_NAMES.get(symbol, symbol),
            "buy_price": price, "date": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        with open(DB_FILE, 'w', encoding='utf-8') as f: json.dump(data, f)
    return redirect(url_for('index'))

@app.route('/untrack/<symbol>')
def untrack(symbol):
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            data = [x for x in json.load(f) if x['symbol'] != symbol]
            with open(DB_FILE, 'w', encoding='utf-8') as f: json.dump(data, f)
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)
