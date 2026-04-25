from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
import requests
import pandas as pd

app = FastAPI()
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )


def get_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.TW"
    res = requests.get(url).json()

    close = res["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    volume = res["chart"]["result"][0]["indicators"]["quote"][0]["volume"]

    return pd.Series(close).dropna(), pd.Series(volume).dropna()


def score(price, volume):
    s = 0

    # 底底高
    if price.iloc[-1] > price.iloc[-5]:
        s += 30

    # 爆量
    if volume.iloc[-1] > volume.mean()*1.5:
        s += 30

    # 突破前高
    if price.iloc[-1] > price.max()*0.95:
        s += 40

    return s


@app.get("/recommendations")
def rec():
    stocks = [
        "2330",
        "2317",
        "2303",
        "2603",
        "2454",
        "2382"
    ]

    result = []

    for s in stocks:
        try:
            price, volume = get_data(s)
            sc = score(price, volume)

            if sc >= 60:
                result.append({
                    "stock": s,
                    "score": sc
                })
        except:
            continue

    return result
