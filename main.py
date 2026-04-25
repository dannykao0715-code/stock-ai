def analyze_inertia(symbol):
    try:
        # 抓取 10 年數據並填補空值
        data = yf.download(symbol, period="10y", interval="1d", progress=False)
        if data.empty or len(data) < 500: return None
        
        # 修正：處理多層索引並填補缺失值
        close_prices = data['Close'].fillna(method='ffill').fillna(method='bfill').values.flatten()
        
        # 當前走勢 (最近 20 天)
        current = close_prices[-20:]
        if len(current) < 20: return None
        
        # 正規化函數 (加入微小數值防止除以零)
        def norm(arr):
            std = np.std(arr)
            if std == 0: return arr * 0
            return (arr - np.mean(arr)) / std

        current_norm = norm(current)
        best_match_score = -1
        best_match_date = ""
        
        # 增加步長到 10，大幅提升速度並減少運算壓力
        for i in range(0, len(close_prices) - 60, 10):
            past_segment = close_prices[i : i+20]
            # 使用相關係數
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
        print(f"Error analyzing {symbol}: {e}")
        return None
