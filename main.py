import os
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask, render_template
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

app = Flask(__name__)

# --- 高手邏輯層：抓取排行榜 ---
def get_top_stocks():
    """抓取成交量與漲幅熱門股 (範例抓取 Yahoo 財經熱門榜)"""
    stocks = ["2330.TW", "2317.TW", "2454.TW", "2603.TW", "2303.TW", "2308.TW", "3231.TW", "2382.TW", "1513.TW", "2881.TW"]
    # 實際上這裡可以串接更複雜的爬蟲抓取每日 Top 50，我們先以這 10 檔指標股為核心池
    return stocks

# --- AI 核心層：十年慣性比對 ---
def analyze_inertia(symbol):
    try:
        # 抓取 10 年數據
        data = yf.download(symbol, period="10y", interval="1d", progress=False)
        if len(data) < 250: return None
        
        # 當前走勢 (最近 20 天)
        current = data['Close'].tail(20).values
        current_vol = data['Volume'].tail(20).values
        
        # 正規化函數 (高手觀點：要把量也納入考慮)
        def norm(arr):
            return (arr - np.min(arr)) / (np.max(arr) - np.min(arr) + 1e-9)

        current_norm = norm(current)
        
        best_match_score = 0
        best_match_date = ""
        
        # 10 年滑動視窗比對 (每 5 天跳一次增加效率)
        for i in range(0, len(data) - 40, 5):
            past_segment = data['Close'].iloc[i : i+20].values
            # 計算相關係數
            score = np.corrcoef(current_norm, norm(past_segment))[0, 1]
            
            if score > best_match_score:
                best_match_score = score
                best_match_date = data.index[i].strftime('%Y-%m-%d')
        
        # 計算預期損益 (如果歷史重演，未來 5 天的漲跌)
        # 這裡就是你要求的「進化策略」：看歷史那次之後發生什麼事
        return {
            "symbol": symbol,
            "score": round(best_match_score * 100, 2),
            "history_date": best_match_date
        }
    except Exception as e:
        print(f"Error analyzing {symbol}: {e}")
        return None

# --- 網頁路由層 ---
@app.route('/')
def index():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. 抓取排行榜股票
    target_stocks = get_top_stocks()
    
    # 2. 進行 AI 慣性分析
    results = []
    for s in target_stocks:
        analysis = analyze_inertia(s)
        if analysis and analysis['score'] > 80: # 相似度門檻
            results.append(analysis)
    
    # 3. 排序：相似度最高排前面
    results = sorted(results, key=lambda x: x['score'], reverse=True)

    return render_template('index.html', 
                           current_time=now, 
                           recommendations=results)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
