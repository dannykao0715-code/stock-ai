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
        # 櫃買指數若 yfinance 抓不到，給個預設或 N/A
        try:
            twoii = yf.Ticker("^TWOII").fast_info['last_price']
        except:
            twoii = "N/A"
        return round(twii, 2), (round(twoii, 2) if isinstance(twoii, float) else twoii)
    except:
        return "N/A", "N/A"

# --- 2. 自動抓取今日成交量前 20 名 (Yahoo 爬蟲) ---
def get_top_stocks():
    try:
        url = "https://tw.stock.yahoo.com/rank/volume?exchange=TAI"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        links = soup.select('a[href*="/quote/"]')
        stock_list = []
        
        for link in links:
            href = link.get('href')
            # 提取代號 (例如 2330)
            symbol = href.split('/')[-1].split('.')[0]
            if symbol.isdigit() and len(symbol) == 4:
                full_symbol = f"{symbol}.TW"
                if full_symbol not in stock_list:
                    stock_list.append(full_symbol)
            if len(stock_list) >= 20: break
        return stock_list
    except Exception as e:
        print(f"排行榜抓取失敗: {e}")
        # 保底清單，確保網頁不會空空的
        return ["2330.TW", "2317.TW", "2454.TW", "2603.TW", "2303.TW", "2382.TW", "3231.TW", "1513.TW", "2881.TW", "2609.TW"]

# --- 3. AI 核心：慣性比對 ---
def analyze_inertia(symbol):
    try:
        # 下載 10 年數據，progress=False 保持日誌乾淨
        data = yf.download(symbol, period="10y", interval="1d", progress=False)
        if data.empty or len(data) < 500: return None
        
        # 關鍵修正：相容新版 Pandas 的補洞語法
        close_prices = data['Close'].ffill().values.flatten()
        
        # 取得最近 20 天走勢
        current = close_prices[-20:]
        
        def norm(arr):
            std = np.std(arr)
            if std == 0: return arr * 0
            return (arr - np.mean(arr)) / (std + 1e-9)

        current_norm = norm(current)
        best_match_score = -1
        best_match_date = ""
        
        # 掃描歷史 (step=10 提升速度，避免 Railway 超時)
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
    
    # 執行自動抓取排行榜
    target_stocks = get_top_stocks()
    results = []
    
    # 限制分析熱門前 10 檔，確保網頁讀取在 20 秒內完成
    for s in target_stocks[:10]:
        analysis = analyze_inertia(s)
        if analysis and analysis['score'] > 70: # 稍微調低門檻讓清單更容易出現結果
            results.append(analysis)
    
    results = sorted(results, key=lambda x: x['score'], reverse=True)

    return render_template('index.html', 
                           current_time=now, 
                           tw_idx=tw_idx, 
                           otc_idx=otc_idx, 
                           recommendations=results)

if __name__ == "__main__":
    # Railway 會自動給 PORT，預設 8080
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
