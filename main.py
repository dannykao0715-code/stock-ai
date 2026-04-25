from flask import Flask, render_template
import yfinance as yf
from datetime import datetime

app = Flask(__name__)

def get_stock_indices():
    # 抓取大盤與櫃買指數 (yfinance 代號)
    twii = yf.Ticker("^TWII").fast_info['last_price']
    twoii = yf.Ticker("^TWOII").fast_info['last_price']
    return round(twii, 2), round(twoii, 2)

@app.route('/')
def index():
    # 1. 取得即時時間
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 2. 取得指數 (嘗試抓取，若失敗給予預設值 0)
    try:
        tw_idx, otc_idx = get_stock_indices()
    except:
        tw_idx, otc_idx = 0, 0
        
    return render_template('index.html', 
                           current_time=now, 
                           tw_idx=tw_idx, 
                           otc_idx=otc_idx)

if __name__ == "__main__":
    app.run(debug=True)
