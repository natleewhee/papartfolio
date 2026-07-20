import requests
import time
import yfinance
from concurrent.futures import ThreadPoolExecutor
from config import FINNHUB_API_KEY
import logging

logger = logging.getLogger(__name__)

_fx_cache = {}  # (from, to) -> (fetched_at, rate)
FX_CACHE_TTL_SECONDS = 60 * 60

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.0

def _retry(fn, label):
    """Run fn() with a few retries + exponential backoff, so a single transient
    blip during the once-a-day report doesn't drop a holding (which would skip
    the day's aggregate). Returns fn()'s result, or None if all attempts fail."""
    for attempt in range(MAX_RETRIES):
        try:
            return fn()
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (2 ** attempt))
            else:
                logger.error(f"❌ {label} failed after {MAX_RETRIES} attempts: {e}")
    return None

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
    """Fetch current price and change from Finnhub (retried on transient failure)."""
    def _fetch():
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
        }
    return _retry(_fetch, f"price fetch for {symbol}")

def fetch_sg_stock_price(symbol):
    """Fetch current price and change for an SGX (.SI) ticker via yfinance (retried; Finnhub free tier doesn't cover it)."""
    def _fetch():
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
        }
    return _retry(_fetch, f"SG price fetch for {symbol}")

def get_price(symbol):
    """Dispatch to yfinance for SGX tickers, Finnhub for everything else."""
    if is_sg_ticker(symbol):
        return fetch_sg_stock_price(symbol)
    return fetch_stock_price(symbol)

def get_prices_bulk(symbols):
    """Fetch quotes for many symbols concurrently — each call is a blocking
    network request (Finnhub or yfinance), so looping sequentially scales
    linearly with portfolio/watchlist size for no reason.

    Returns {symbol: price_data_or_None}, matching get_price()'s own return
    shape per symbol. A per-symbol failure logs and resolves to None rather
    than failing the whole batch.
    """
    symbols = list(symbols)
    if not symbols:
        return {}

    def _one(symbol):
        try:
            return symbol, get_price(symbol)
        except Exception as e:
            logger.error(f"❌ Error fetching price for {symbol}: {e}")
            return symbol, None

    with ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as pool:
        return dict(pool.map(_one, symbols))

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

    def _fetch():
        url = "https://api.frankfurter.dev/v1/latest"
        params = {"from": from_currency, "to": to_currency}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()["rates"][to_currency]

    rate = _retry(_fetch, f"FX rate {from_currency}->{to_currency}")
    if rate is not None:
        _fx_cache[key] = (time.time(), rate)
        return rate
    # All attempts failed — fall back to the last known rate if we have one
    return cached[1] if cached else None