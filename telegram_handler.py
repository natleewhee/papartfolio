import requests
from telegram.ext import ContextTypes
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID
from portfolio import calculate_portfolio_metrics, get_macro_snapshot
from fetcher import fetch_all_news
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

async def send_telegram_message(text: str, parse_mode: str = "Markdown"):
    """Send message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": TELEGRAM_USER_ID,
        "text": text,
        "parse_mode": parse_mode,
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("✅ Message sent to Telegram")
            return True
        else:
            logger.error(f"❌ Telegram API error: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Error sending message: {e}")
        return False

async def send_daily_report(context: ContextTypes.DEFAULT_TYPE = None):
    """Generate and send daily portfolio report."""
    try:
        # Calculate metrics
        metrics = calculate_portfolio_metrics()
        
        if not metrics["holdings"]:
            msg = "📭 Portfolio is empty. Use /add to add holdings."
            await send_telegram_message(msg)
            return
        
        # Build report
        report = f"📊 *Portfolio Snapshot | {datetime.now().strftime('%d %b %Y')}*\n\n"
        
        # Asset breakdown
        report += "*💰 Asset Breakdown:*\n"
        for holding in metrics["holdings"]:
            emoji = "🟢" if holding["daily_change_%"] >= 0 else "🔴"
            report += (
                f"• {holding['symbol']}: ${holding['current_value']:.2f} "
                f"| {emoji} {holding['daily_change_%']:+.2f}% "
                f"(${holding['daily_change_$']:+.2f})\n"
            )
        
        report += "\n"
        
        # Portfolio summary
        emoji = "🟢" if metrics["daily_change_%"] >= 0 else "🔴"
        report += (
            f"*📈 Portfolio Summary:*\n"
            f"   Total Value: ${metrics['total_value']:,.2f}\n"
            f"   Daily Change: {emoji} ${metrics['daily_change_$']:+.2f} "
            f"({metrics['daily_change_%']:+.2f}%)\n"
            f"   Unrealized Gain: ${metrics['unrealized_gain']:+.2f} "
            f"({metrics['unrealized_gain_pct']:+.2f}%)\n\n"
        )
        
        # Macro context
        report += "*🌍 Market Intelligence:*\n"
        macro = get_macro_snapshot()
        report += macro + "\n"
        
        # Top news
        symbols = [h["symbol"] for h in metrics["holdings"]]
        news_items = fetch_all_news(symbols)
        
        if news_items:
            report += "*📰 Top News:*\n"
            for i, news in enumerate(news_items, 1):
                report += f"{i}. *{news['symbol']}*: {news['title'][:80]}...\n"
        
        await send_telegram_message(report)
        logger.info("✅ Daily report sent")
    
    except Exception as e:
        logger.error(f"❌ Error generating report: {e}")
        await send_telegram_message(f"❌ Error generating report: {e}")