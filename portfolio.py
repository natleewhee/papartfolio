from fetcher import fetch_stock_price, fetch_macro_data, fetch_all_news
from portfolio_db import get_all_holdings, save_daily_snapshot, save_portfolio_aggregate
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def calculate_portfolio_metrics():
    """
    Calculate portfolio value, P&L, and per-holding metrics.
    
    Returns:
    {
        "total_value": 50000.00,
        "total_cost_basis": 48000.00,
        "unrealized_gain": 2000.00,
        "unrealized_gain_pct": 4.17,
        "daily_change_$": 150.00,
        "daily_change_%": 0.3,
        "holdings": [
            {
                "symbol": "ECHO",
                "shares": 50,
                "avg_cost": 12.50,
                "current_price": 15.00,
                "current_value": 750.00,
                "cost_basis": 625.00,
                "unrealized_gain": 125.00,
                "unrealized_gain_pct": 20.0,
                "daily_change_$": 25.00,
                "daily_change_%": 1.7,
            }
        ]
    }
    """
    holdings = get_all_holdings()
    
    if not holdings:
        return {
            "total_value": 0,
            "total_cost_basis": 0,
            "unrealized_gain": 0,
            "unrealized_gain_pct": 0,
            "daily_change_$": 0,
            "daily_change_%": 0,
            "holdings": [],
        }
    
    portfolio_data = []
    total_value = 0
    total_cost_basis = 0
    
    for holding in holdings:
        symbol = holding["symbol"]
        shares = holding["shares"]
        avg_cost = holding["avg_cost"]
        
        # Fetch current price
        price_data = fetch_stock_price(symbol)
        if not price_data:
            logger.warning(f"⚠️ Could not fetch price for {symbol}")
            continue
        
        current_price = price_data["price"]
        current_value = current_price * shares
        cost_basis = avg_cost * shares
        
        unrealized_gain = current_value - cost_basis
        unrealized_gain_pct = (unrealized_gain / cost_basis * 100) if cost_basis > 0 else 0
        
        # Daily change (from price change)
        daily_change_$ = (price_data["change"] or 0) * shares
        daily_change_% = price_data["change_pct"] or 0
        
        portfolio_data.append({
            "symbol": symbol,
            "shares": shares,
            "avg_cost": round(avg_cost, 2),
            "current_price": round(current_price, 2),
            "current_value": round(current_value, 2),
            "cost_basis": round(cost_basis, 2),
            "unrealized_gain": round(unrealized_gain, 2),
            "unrealized_gain_pct": round(unrealized_gain_pct, 2),
            "daily_change_$": round(daily_change_$, 2),
            "daily_change_%": round(daily_change_%, 2),
        })
        
        total_value += current_value
        total_cost_basis += cost_basis
        
        # Save daily snapshot
        save_daily_snapshot(
            datetime.now().strftime("%Y-%m-%d"),
            symbol,
            current_price,
            shares,
            daily_change_$,
            daily_change_%,
        )
    
    unrealized_gain = total_value - total_cost_basis
    unrealized_gain_pct = (unrealized_gain / total_cost_basis * 100) if total_cost_basis > 0 else 0
    
    # Calculate portfolio daily change
    portfolio_daily_change = sum(h["daily_change_$"] for h in portfolio_data)
    portfolio_daily_change_pct = (portfolio_daily_change / (total_value - portfolio_daily_change) * 100) if (total_value - portfolio_daily_change) > 0 else 0
    
    # Save portfolio aggregate
    save_portfolio_aggregate(
        datetime.now().strftime("%Y-%m-%d"),
        total_value,
        total_cost_basis,
        portfolio_daily_change,
        portfolio_daily_change_pct,
    )
    
    return {
        "total_value": round(total_value, 2),
        "total_cost_basis": round(total_cost_basis, 2),
        "unrealized_gain": round(unrealized_gain, 2),
        "unrealized_gain_pct": round(unrealized_gain_pct, 2),
        "daily_change_$": round(portfolio_daily_change, 2),
        "daily_change_%": round(portfolio_daily_change_pct, 2),
        "holdings": portfolio_data,
    }

def get_macro_snapshot():
    """Fetch and format macro data."""
    macro = fetch_macro_data()
    formatted = ""
    for symbol, data in macro.items():
        emoji = "🟢" if data["change_pct"] >= 0 else "🔴"
        formatted += f"{emoji} {symbol}: ${data['price']:.2f} ({data['change_pct']:+.2f}%)\n"
    return formatted