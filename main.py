import os
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask, render_template
from datetime import datetime, timedelta

app = Flask(__name__)

# --- 1. 抓取大盤指數 ---
def get_stock_indices():
    try:
        # 抓取大盤與櫃買指數
        twii = yf.Ticker("^TWII").fast_info['last_price']
        twoii = yf.Ticker("^TWOII").fast_info['last_price']
        return round(twii, 2), round(twoii, 2)
    except:
        return "N/A", "N/A"

# --- 2. 預定義排行榜 (你可以隨時增加代號) ---
def get_top_stocks():
    # 這裡放目前成交量大或熱門的個股
    return ["2330.TW", "2317.TW", "2454.TW", "2603.TW", "2303.TW", "2382.TW", "3231.TW", "1513.TW", "2881.TW", "2609.TW"]

# --- 3. AI 核心：十年慣性比對 ---
def analyze_inertia(symbol):
    try:
        # 抓取 10 年數據 (減少進度條輸出)
        data = yf.download(symbol, period="10y", interval="1d", progress=False)
        if data.empty or len(data) < 500:
            return None
        
        # 處理缺失值並平滑數據
        close_prices = data['Close'].fillna(method='ffill').values.flatten()
        
        # 當前走勢 (最近 20 天)
        current = close_prices[-20:]
        
        # 正規化函數：防止除以零並標準化形狀
        def norm(arr):
            std = np.std(arr)
            if std == 0: return arr * 0
            return (arr - np.mean(arr)) / (std + 1e-9)

        current_norm = norm(current)
        best_match_score = -1
        best_match_date = ""
        
        # 掃描 10 年歷史 (step=10 提升 10 倍速度)
        for i in range(0, len(close_prices) - 60, 10):
            past_segment = close_prices[i : i+20]
            # 計算相關係數
            score = np.corrcoef(current_norm, norm(past_segment))[0, 1]
            
            if not np.isnan(score) and score > best_match_score:
                best_match_score = score
                best_match_date = data.index[i].strftime('%Y-%m-%d')
        
        return {
            "symbol": symbol,
            "score": round(best_match_score * 100, 2),
            "history_date": best_match_date
        }
    except Exception as e:
        print(f"分析 {symbol} 出錯: {e}")
        return None

# --- 4. 網頁路由 ---
@app.route('/')
def index():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 取得指數
    tw_idx, otc_idx = get_stock_indices()
    
    # 取得熱門股並掃描 (先限制前 6 檔，確保穩定度)
    target_stocks = get_top_stocks()
    results = []
    
    for s in target_stocks[:6]:
        analysis = analyze_inertia(s)
        if analysis and analysis['score'] > 75: # 相似度門檻
            results.append(analysis)
    
    # 排序
    results = sorted(results, key=lambda x: x['score'], reverse=True)

    return render_template('index.html', 
                           current_time=now, 
                           tw_idx=tw_idx, 
                           otc_idx=otc_idx, 
                           recommendations=results)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
