import os
import random
import secrets
from datetime import datetime, timedelta

import httpx
import pytz
from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware

TWELVE_DATA_KEY = os.environ["TWELVE_DATA_KEY"]
API_TOKEN = os.environ["API_TOKEN"]
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")

TWELVE_DATA_URL = "https://api.twelvedata.com"

app = FastAPI(title="Inovatrader Signals API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["GET"],
    allow_headers=["*"],
)

ASSETS = {
    "forex": [
        {"pair": "EUR/USD", "symbol": "EUR/USD", "asset_id": "eurusd"},
        {"pair": "GBP/USD", "symbol": "GBP/USD", "asset_id": "gbpusd"},
        {"pair": "USD/JPY", "symbol": "USD/JPY", "asset_id": "usdjpy"},
        {"pair": "AUD/USD", "symbol": "AUD/USD", "asset_id": "audusd"},
        {"pair": "USD/CAD", "symbol": "USD/CAD", "asset_id": "usdcad"},
    ],
    "crypto": [
        {"pair": "BTC/USD", "symbol": "BTC/USD", "asset_id": "btcusd"},
        {"pair": "ETH/USD", "symbol": "ETH/USD", "asset_id": "ethusd"},
        {"pair": "LTC/USD", "symbol": "LTC/USD", "asset_id": "ltcusd"},
    ],
    "commodities": [
        {"pair": "XAU/USD", "symbol": "XAU/USD", "asset_id": "xauusd"},
        {"pair": "XAG/USD", "symbol": "XAG/USD", "asset_id": "xagusd"},
        {"pair": "WTI", "symbol": "USOIL", "asset_id": "wtousd"},
    ],
}

CATEGORY_META = {
    "forex": {"label": "Forex", "icon": "💱"},
    "crypto": {"label": "Cripto", "icon": "₿"},
    "commodities": {"label": "Commodity", "icon": "🪙"},
}

def verify_token(token: str = Query(...)):
    if not secrets.compare_digest(token, API_TOKEN):
        raise HTTPException(status_code=401, detail="Token invalido.")
    return token

async def _get(path: str, params: dict) -> dict:
    params["apikey"] = TWELVE_DATA_KEY
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.get(f"{TWELVE_DATA_URL}/{path}", params=params)
        r.raise_for_status()
        return r.json()

async def fetch_rsi(symbol, interval="1min"):
    data = await _get("rsi", {"symbol": symbol, "interval": interval, "time_period": 14, "outputsize": 1})
    values = data.get("values")
    return float(values[0]["rsi"]) if values else None

async def fetch_ema(symbol, period, interval="1min"):
    data = await _get("ema", {"symbol": symbol, "interval": interval, "time_period": period, "outputsize": 1})
    values = data.get("values")
    return float(values[0]["ema"]) if values else None

async def fetch_macd(symbol, interval="1min"):
    data = await _get("macd", {"symbol": symbol, "interval": interval, "outputsize": 1})
    values = data.get("values")
    if not values:
        return None
    v = values[0]
    return {"macd": float(v["macd"]), "signal": float(v["macd_signal"]), "histogram": float(v["macd_hist"])}

async def fetch_stoch(symbol, interval="1min"):
    data = await _get("stoch", {"symbol": symbol, "interval": interval, "outputsize": 10})
    values = data.get("values")
    if not values:
        return None
    return [{"k": float(v["slow_k"]), "d": float(v["slow_d"])} for v in values]

def analyze_signals(rsi, ema_fast, ema_slow, macd, stoch):
    buy_votes = sell_votes = 0
    if rsi is not None:
        if rsi < 35: buy_votes += 1
        elif rsi > 65: sell_votes += 1
    if ema_fast and ema_slow:
        if ema_fast > ema_slow: buy_votes += 1
        else: sell_votes += 1
    if macd:
        if macd["macd"] > macd["signal"] and macd["histogram"] > 0: buy_votes += 1
        elif macd["macd"] < macd["signal"] and macd["histogram"] < 0: sell_votes += 1
    if stoch:
        last = stoch[0]
        if last["k"] > last["d"] and last["k"] < 80: buy_votes += 1
        elif last["k"] < last["d"] and last["k"] > 20: sell_votes += 1
    score = max(buy_votes, sell_votes)
    total = buy_votes + sell_votes or 1
    direction = "COMPRA" if buy_votes >= sell_votes else "VENDA"
    agreement = score / total
    base = 0.60
    rsi_bonus = min(abs(rsi - 50) / 50, 1.0) * 0.15 if rsi else 0
    stoch_bonus = 0.0
    if stoch and len(stoch) >= 3:
        crossovers = sum(1 for i in range(1, len(stoch)) if (stoch[i]["k"] > stoch[i]["d"]) == (stoch[i-1]["k"] > stoch[i-1]["d"]))
        stoch_bonus = (crossovers / (len(stoch) - 1)) * 0.10
    agree_bonus = (agreement - 0.5) * 0.30
    win_rate_f = min(base + rsi_bonus + stoch_bonus + agree_bonus, 0.95)
    confidence = "Muito Alta" if score >= 3 else "Alta" if score == 2 else "Media"
    return {"direction": direction, "score": score, "confidence": confidence, "win_rate": f"{int(win_rate_f * 100)}%"}

def next_entry_time(minutes_ahead=2):
    tz = pytz.timezone("America/Sao_Paulo")
    now = datetime.now(tz) + timedelta(minutes=minutes_ahead)
    return now.strftime("%H:%M")

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/signal")
async def generate_signal(_token: str = Depends(verify_token), timeframe: str = Query("M1")):
    category_key = random.choice(list(ASSETS.keys()))
    asset = random.choice(ASSETS[category_key])
    symbol = asset["symbol"]
    rsi = await fetch_rsi(symbol)
    ema_fast = await fetch_ema(symbol, 9)
    ema_slow = await fetch_ema(symbol, 21)
    macd = await fetch_macd(symbol)
    stoch = await fetch_stoch(symbol)
    result = analyze_signals(rsi, ema_fast, ema_slow, macd, stoch)
    expiration_map = {"M1": "1 min", "M5": "5 min", "M15": "15 min"}
    return {
        "success": True,
        "signal": {
            "pair": asset["pair"],
            "asset_id": asset["asset_id"],
            "type": result["direction"],
            "entry_time": next_entry_time(2),
            "win_rate": result["win_rate"],
            "confidence": result["confidence"],
            "expiration": expiration_map.get(timeframe, "5 min"),
            "category": CATEGORY_META[category_key]["label"],
            "category_icon": CATEGORY_META[category_key]["icon"],
            "has_news_impact": False,
            "indicators": {
                "rsi": round(rsi, 2) if rsi else None,
                "ema_fast": round(ema_fast, 5) if ema_fast else None,
                "ema_slow": round(ema_slow, 5) if ema_slow else None,
                "macd": macd,
            },
        },
    }
