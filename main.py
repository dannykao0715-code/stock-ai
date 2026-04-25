import os, json, numpy as np, yfinance as yf
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# 主委核心選股名單
STOCK_POOL = ["2330.TW", "2317.TW", "1802.TW", "2603.TW", "3231.TW", "0050.TW", "2409.TW", "2367.TW", "4958.TW", "2303.TW"]

def get_name(s):
    names = {"2330.TW":"台積電", "2317.TW":"鴻海", "1802.TW":"台玻", "2603.TW":"長榮", "3231.TW":"緯創", "0050.TW":"元大台灣50"}
    return names.get(s, s.split('.')[0])

def analyze_inertia(symbol, threshold=80):
    """
    根據『10進階K線慣性』強化：
    1. 形態相似度 (原本的 AI 比對)
    2. 趨勢慣性 (MA均線斜率)
    3. 支撐慣性 (近3日低點不破)
    """
    try:
        df = yf.download(symbol, period="10y", interval="1d", progress=False)
        if df.empty or len(df) < 100: return None
        
        close = df['Close'].ffill().values.flatten()
        
        # --- 邏輯 A: 形態相似度 ---
        target = close[-20:]
        def norm(arr):
            s = np.std(arr)
            return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        
        target_n = norm(target)
        max_c = -1
        for i in range(0, len(close) - 60, 5):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_c: max_c = corr
        score = round(max_c * 100, 2)

        # --- 邏輯 B: 趨勢與支撐慣性 (根據進階K線資料) ---
        ma20 = df['Close'].rolling(window=20).mean()
        is_up_inertia = ma20.iloc[-1] > ma20.iloc[-5] # 20日線向上
        is_support = close[-1] >= np.min(close[-3:]) # 短期支撐慣性
        
        # 綜合判定
        if score >= threshold:
            advice = "🚀 強勢噴出" if score >= 88 and is_up_inertia else "📈 慣性修復" if is_support else "🔎 觀察轉折"
            return {"symbol": symbol, "name": get_name(symbol), "score": score, "advice": advice}
        return None
    except: return None

@app.route('/')
def index():
    indices = {"twii": "---", "otc": "---"}
    try:
        indices["twii"] = round(yf.Ticker("^TWII").fast_info['last_price'], 2)
        indices["otc"] = round(yf.Ticker("^TWOII").fast_info['last_price'], 2)
    except: pass

    # 先用 82% 高標篩選，若無則降至 72%
    recs = [res for s in STOCK_POOL if (res := analyze_inertia(s, 82))]
    if not recs:
        recs = [res for s in STOCK_POOL if (res := analyze_inertia(s, 72))]
    
    # 損益追蹤邏輯... (保持不變)
    return render_template('index.html', recs=recs, indices=indices, now=datetime.now().strftime("%m/%d %H:%M"))

# 路由 add, clear 略...
