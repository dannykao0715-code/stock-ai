import os
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask, render_template
from datetime import datetime
from bs4 import BeautifulSoup

app = Flask(__name__)

# --- 1. 抓取大盤指數 ---
def get_stock_indices():
    try:
        twii = yf.Ticker("^TWII").fast_info['last_price']
        twoii = yf.Ticker("^TWOII").fast_info['last_price']
        return round(twii, 2), round(twoii, 2)
    except:
        return "N/A", "N/A"

# --- 2. 自動抓取今日成交量前 20 名 (爬蟲) ---
def get_top_stocks():
    try:
        url = "https://tw.stock.yahoo.com/rank/volume?exchange=TAI"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        links = soup.select('a[href*="/quote/"]')
        stock_list = []
        
        for link in links:
            href = link.get('href')
            symbol = href.split('/')[-1].split('.')[0]
            if symbol.isdigit() and len(symbol) == 4:
                full_symbol = f"{symbol}.TW"
                if full_symbol not in stock_list:
                    stock_list.append(full_symbol)
            if len(stock_list) >= 20: break
        return stock_list
    except Exception as e:
        print(f"排行榜抓取失敗: {e}")
        return ["2330.TW", "2317.TW", "2603.TW", "2382.TW", "3231.TW"]

# --- 3. AI 核心：慣性比對 ---
def analyze_inertia(symbol):
    try:
        data = yf.download(symbol, period="10y", interval="1d", progress=False)
        if data.empty or len(data) < 500: return None
        
        # 修正後的數據補洞語法 (相容新版 Pandas)
        close_prices = data['Close'].ffill().values.flatten()
        
        current = close_prices[-20:]
        
        def norm(arr):
            std = np.std(arr)
            if std == 0: return arr * 0
            return (arr - np.mean(arr)) / (std + 1e-9)

        current_norm = norm(current)
        best_match_score = -1
        best_match_date = ""
        
        for i in range(0, len(close_prices) - 60, 10):
            past_segment = close_prices[i : i+20]
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
    tw_idx, otc_idx = get_stock_indices()
    
    target_stocks = get_top_stocks()
    results = []
    
    # 限制分析前 12 檔以確保 Railway 效能穩定
    for s in target_stocks[:12]:
        analysis = analyze_inertia(s)
        if analysis and analysis['score'] > 75:
            results.append(analysis)
    
    results = sorted(results, key=lambda x: x['score'], reverse=True)

    return render_template('index.html', 
                           current_time=now, 
                           tw_idx=tw_idx, 
                           otc_idx=otc_idx, 
                           recommendations=results)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
