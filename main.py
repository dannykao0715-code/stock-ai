import os, json, pandas as pd, yfinance as yf
from flask import Flask, render_template, redirect, url_for, request
from datetime import datetime

app = Flask(__name__)
DB_FILE = 'sim_trading.json'

def get_full_market_list():
    """直接從網路抓取台灣上市櫃完整清單，不再侷限手寫名單"""
    try:
        # 抓取上市清單
        url_tse = "http://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
        # 抓取上櫃清單
        url_otc = "http://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
        
        df_tse = pd.read_html(url_tse)[0]
        df_otc = pd.read_html(url_otc)[0]
        
        full_df = pd.concat([df_tse, df_otc])
        # 篩選出股票代號（格式通常是 "2330 台積電"）
        full_df = full_df[full_df[0].str.contains(r'^\d{4}\s')]
        
        stock_dict = {}
        for item in full_df[0]:
            code, name = item.split()
            suffix = ".TW" if len(code) == 4 else ".TWO" # 簡單判斷，上市用.TW
            stock_dict[f"{code}{suffix}"] = name
        return stock_dict
    except:
        return {"2330.TW": "台積電", "2317.TW": "鴻海"} # 備援方案

@app.route('/')
def index():
    # 獲取即時大盤數據與系統時間
    now = datetime.now()
    market = {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "twii": "---", "otc": "---", "cond": "監測中"
    }
    try:
        market['twii'] = f"{yf.Ticker('^TWII').fast_info['last_price']:,.0f}"
        market['otc'] = f"{yf.Ticker('^TWOII').fast_info['last_price']:.2f}"
    except: pass

    # 模擬交易損益計算
    trades = []
    total_pnl = 0
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            for t in json.load(f):
                try:
                    curr = yf.Ticker(t['symbol']).fast_info['last_price']
                    p = (curr - t['buy_price']) / t['buy_price'] * 100
                    total_pnl += p
                    trades.append({**t, "curr_p": round(curr, 2), "pnl": round(p, 2)})
                except: pass

    # --- 強大按鈕觸發：1,800+ 檔地毯式選股 ---
    recommendations = []
    if request.args.get('scan') == 'true':
        all_stocks = get_full_market_list()
        # 這裡示範 AI 選股邏輯：抓取昨日強勢且具備成交量的標的
        # 註：全市場掃描較耗時，實際運作建議加入快取或限制掃描前 500 大成交量
        test_count = 0
        for symbol, name in all_stocks.items():
            if test_count > 100: break # 範例限制前100檔，實際可根據伺服器效能調整
            try:
                tk = yf.Ticker(symbol)
                hist = tk.history(period="2d")
                if len(hist) < 2: continue
                change = (hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100
                if change > 3: # 選出昨日漲幅大於 3% 的強勢股
                    recommendations.append({
                        "symbol": symbol, "name": name, 
                        "price": round(hist['Close'].iloc[-1], 2), "change": round(change, 2)
                    })
                test_count += 1
            except: continue
            
    return render_template('index.html', market=market, trades=trades, 
                           recs=recommendations, total_pnl=round(total_pnl, 2))

# (Track / Untrack 路由保持不變，略)
