import os, json, numpy as np, yfinance as yf, requests
import pandas as pd
from flask import Flask, render_template, redirect, url_for
from datetime import datetime

app = Flask(__name__)
WATCHLIST_FILE = 'watchlist.json'

# --- 1. 總經指標與全市場獲取 ---
def get_market_sentiment():
    """判斷總經情勢：決定進攻或防守"""
    try:
        vix = yf.Ticker("^VIX").fast_info['last_price']
        usd = yf.Ticker("TWD=X").fast_info['last_price'] # 美元/台幣
        gold = yf.Ticker("GC=F").fast_info['last_price'] # 黃金
        oil = yf.Ticker("CL=F").fast_info['last_price']  # 原油
        
        # 簡易決策模型
        if vix > 22 or usd > 32.5:
            return "空頭/避險", 88 # 嚴格篩選
        return "多頭/進攻", 82 # 寬鬆篩選
    except:
        return "中性震盪", 85

def get_full_stock_list():
    """自動獲取全台股上市標的 (範例以關鍵 200 支為海選池，實務可擴充至全市場)"""
    # 為了運算效率，這裡我們先以台灣 50 + 中型 100 + 產業龍頭為基礎進行「海選」
    # 若要真正 1700 支，建議部署在效能較高的主機
    base_list = [f"{i:04d}.TW" for i in range(1101, 9999)] 
    return base_list

# --- 2. 核心分析邏輯 (K線慣性+回測思維) ---
def analyze_stock_logic(symbol, threshold):
    try:
        # 下載歷史數據 (三個月至十年的跨度)
        df = yf.download(symbol, period="5y", interval="1d", progress=False)
        if df.empty or len(df) < 120: return None
        
        close = df['Close'].ffill().values.flatten()
        target = close[-20:] # 近 20 日慣性
        
        # AI 形態比對
        def norm(arr):
            s = np.std(arr)
            return (arr - np.mean(arr)) / (s + 1e-9) if s != 0 else arr * 0
        
        target_n = norm(target)
        max_c = -1
        # 這裡縮短比對步長，提高精度
        for i in range(0, len(close) - 40, 3):
            corr = np.corrcoef(target_n, norm(close[i:i+20]))[0, 1]
            if corr > max_c: max_c = corr
        
        score = round(max_c * 100, 2)
        
        if score >= threshold:
            # 加入位階判斷 (三個月/六個月/一年)
            ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
            ma250 = df['Close'].rolling(window=250).mean().iloc[-1]
            
            # 判斷是短線爆發還是長線潛力
            if close[-1] > ma60 and ma60 > ma250:
                inv_type = "多頭排列(長線型)"
            else:
                inv_type = "低檔轉折(短線型)"
                
            return {
                "symbol": symbol, "score": score, 
                "type": inv_type, "price": round(close[-1], 2)
            }
    except:
        return None

@app.route('/')
def index():
    sentiment, threshold = get_market_sentiment()
    
    # 全市場海選 (這裡先取前 100 支做示範，以免程式跑太久)
    all_stocks = ["2330.TW", "2317.TW", "2454.TW", "2603.TW", "1513.TW", "2382.TW", "3231.TW", "2409.TW", "1802.TW", "2303.TW", "2609.TW", "2615.TW", "2301.TW", "2357.TW"]
    # 實際上可以透過爬蟲獲取證交所全清單：all_stocks = get_taiwan_stock_ids()
    
    recs = []
    for s in all_stocks:
        res = analyze_stock_logic(s, threshold)
        if res: recs.append(res)
    
    # 依相似度排序
    recs = sorted(recs, key=lambda x: x['score'], reverse=True)
    
    # 損益追蹤 (回測邏輯)
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

    return render_template('index.html', recs=recs, sentiment=sentiment, now=datetime.now().strftime("%Y-%m-%d"))

# add, clear 路由同前...
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
