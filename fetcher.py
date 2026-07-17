import requests
import time
import yfinance
from config import FINNHUB_API_KEY
import logging

logger = logging.getLogger(__name__)

_fx_cache = {}  # (from, to) -> (fetched_at, rate)
FX_CACHE_TTL_SECONDS = 60 * 60

def is_sg_ticker(symbol):
    """SGX tickers use the .SI suffix (e.g. D05.SI for DBS Group)."""
    return symbol.upper().endswith(".SI")

def get_currency_for_symbol(symbol):
    """Finnhub free tier only covers USD; SGX (.SI) tickers trade in SGD."""
    return "SGD" if is_sg_ticker(symbol) else "USD"

def validate_ticker(symbol):
    """Check if ticker exists on Finnhub (validation)."""
    try:
        url = "https://finnhub.io/api/v1/quote"
        params = {"symbol": symbol.upper(), "token": FINNHUB_API_KEY}
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"❌ Ticker validation failed: {symbol}")
            return False
        
        data = response.json()
        # Finnhub returns c=0 (not an error, not null) for unknown tickers
        if "error" in data or not data.get("c"):
            logger.warning(f"⚠️ Invalid ticker: {symbol}")
            return False
        
        logger.info(f"✅ Ticker valid: {symbol}")
        return True
    except Exception as e:
        logger.error(f"❌ Error validating ticker {symbol}: {e}")
        return False

def fetch_stock_price(symbol):
    """Fetch current price and change from Finnhub."""
    try:
        url = "https://finnhub.io/api/v1/quote"
        params = {"symbol": symbol.upper(), "token": FINNHUB_API_KEY}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        return {
            "symbol": symbol.upper(),
            "price": data.get("c"),  # Current price
            "change": data.get("d"),  # Dollar change
            "change_pct": data.get("dp"),  # % change
            "high_52w": data.get("h52"),
            "low_52w": data.get("l52"),
        }
    except Exception as e:
        logger.error(f"❌ Error fetching price for {symbol}: {e}")
        return None

def fetch_sg_stock_price(symbol):
    """Fetch current price and change for an SGX (.SI) ticker via yfinance (Finnhub free tier doesn't cover it)."""
    try:
        info = yfinance.Ticker(symbol.upper()).fast_info
        price = info["last_price"]
        prev_close = info["previous_close"]
        if price is None or prev_close is None:
            return None
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0
        return {
            "symbol": symbol.upper(),
            "price": price,
            "change": change,
            "change_pct": change_pct,
            "high_52w": None,
            "low_52w": None,
        }
    except Exception as e:
        logger.error(f"❌ Error fetching SG price for {symbol}: {e}")
        return None

def get_price(symbol):
    """Dispatch to yfinance for SGX tickers, Finnhub for everything else."""
    if is_sg_ticker(symbol):
        return fetch_sg_stock_price(symbol)
    return fetch_stock_price(symbol)

def validate_symbol(symbol):
    """Dispatch ticker validation the same way as get_price."""
    if is_sg_ticker(symbol):
        return fetch_sg_stock_price(symbol) is not None
    return validate_ticker(symbol)

def fetch_fx_rate(from_currency, to_currency):
    """Fetch an FX conversion rate (cached) via Frankfurter, a free no-key-required API."""
    if from_currency == to_currency:
        return 1.0

    key = (from_currency, to_currency)
    cached = _fx_cache.get(key)
    if cached and (time.time() - cached[0]) < FX_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        url = "https://api.frankfurter.dev/v1/latest"
        params = {"from": from_currency, "to": to_currency}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        rate = response.json()["rates"][to_currency]
        _fx_cache[key] = (time.time(), rate)
        return rate
    except Exception as e:
        logger.error(f"❌ Error fetching FX rate {from_currency}->{to_currency}: {e}")
        return cached[1] if cached else None