from fastapi import FastAPI
import requests
import pandas as pd

app = FastAPI()

@app.get("/")
def home():
    return {"message": "stock ai running"}

def get_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.TW"
    res = requests.get(url).json()

    close = res["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    volume = res["chart"]["result"][0]["indicators"]["quote"][0]["volume"]

    return pd.Series(close).dropna(), pd.Series(volume).dropna()

def score(price, volume):
    s = 0

    if price.iloc[-1] > price.iloc[-5]:
        s += 50

    if volume.iloc[-1] > volume.mean():
        s += 50

    return s

@app.get("/recommendations")
def rec():
    stocks = ["2330", "2317", "2303"]
    result = []

    for s in stocks:
        try:
            price, volume = get_data(s)
            sc = score(price, volume)

            if sc >= 50:
                result.append({
                    "stock": s,
                    "score": sc
                })
        except:
            continue

    return result
