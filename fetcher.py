import requests
from config import FINNHUB_API_KEY, NEWSAPI_KEY, MACRO_SYMBOLS
import logging

logger = logging.getLogger(__name__)

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
        # Finnhub returns empty dict or error if ticker invalid
        if "error" in data or data.get("c") is None:
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

def fetch_macro_data():
    """Fetch macro indicator levels (SPY, QQQ, DXY)."""
    macro_data = {}
    for symbol in MACRO_SYMBOLS:
        data = fetch_stock_price(symbol)
        if data:
            macro_data[symbol] = {
                "price": data["price"],
                "change_pct": data["change_pct"],
            }
    return macro_data

def fetch_top_news(symbol, limit=1):
    """Fetch top news story for a stock using NewsAPI."""
    try:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": symbol.upper(),
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": limit,
            "apiKey": NEWSAPI_KEY,
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        articles = data.get("articles", [])
        
        if articles:
            article = articles[0]
            return {
                "symbol": symbol.upper(),
                "title": article.get("title"),
                "url": article.get("url"),
                "source": article.get("source", {}).get("name"),
                "published_at": article.get("publishedAt"),
            }
        return None
    except Exception as e:
        logger.error(f"❌ Error fetching news for {symbol}: {e}")
        return None

def fetch_all_news(symbols):
    """Fetch top news for all stocks."""
    news_items = []
    for symbol in symbols:
        news = fetch_top_news(symbol, limit=1)
        if news:
            news_items.append(news)
    return sorted(news_items, key=lambda x: x["published_at"], reverse=True)[:3]