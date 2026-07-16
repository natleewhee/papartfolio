from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from fetcher import validate_ticker, fetch_stock_price
from portfolio_db import (
    add_holding, remove_holding, update_holding,
    get_all_holdings, get_holding, clear_all_holdings
)
from portfolio import calculate_portfolio_metrics
from telegram_handler import send_daily_report
from config import TELEGRAM_USER_ID
import logging

logger = logging.getLogger(__name__)

# Conversation states
CONFIRM_CLEAR = 1

async def check_user(update: Update) -> bool:
    """Verify message is from authorized user."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        await update.message.reply_text("❌ Unauthorized")
        return False
    return True

# ==================== COMMAND HANDLERS ====================

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add SYMBOL SHARES AVG_COST"""
    if not await check_user(update):
        return
    
    try:
        parts = update.message.text.split()
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format\n\n"
                "Usage: /add SYMBOL SHARES AVG_COST\n"
                "Example: /add ECHO 50 12.50"
            )
            return
        
        symbol = parts[1].upper()
        shares = int(parts[2])
        avg_cost = float(parts[3])
        
        # Validation
        if shares <= 0:
            await update.message.reply_text("❌ Shares must be > 0")
            return
        
        if avg_cost <= 0:
            await update.message.reply_text("❌ Cost must be > 0")
            return
        
        # Check if ticker exists
        await update.message.reply_text(f"🔍 Validating {symbol}...")
        if not validate_ticker(symbol):
            await update.message.reply_text(f"❌ Invalid ticker: {symbol}")
            return
        
        # Add to database
        if add_holding(symbol, shares, avg_cost):
            await update.message.reply_text(
                f"✅ Added {symbol}\n"
                f"Shares: {shares}\n"
                f"Avg Cost: ${avg_cost}"
            )
        else:
            await update.message.reply_text(f"❌ {symbol} already in portfolio")
    
    except ValueError:
        await update.message.reply_text("❌ Shares and cost must be numbers")
    except Exception as e:
        logger.error(f"Error in /add: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove SYMBOL"""
    if not await check_user(update):
        return
    
    try:
        parts = update.message.text.split()
        if len(parts) != 2:
            await update.message.reply_text(
                "❌ Invalid format\n\n"
                "Usage: /remove SYMBOL\n"
                "Example: /remove ECHO"
            )
            return
        
        symbol = parts[1].upper()
        
        if remove_holding(symbol):
            await update.message.reply_text(f"✅ Removed {symbol}")
        else:
            await update.message.reply_text(f"❌ {symbol} not found in portfolio")
    
    except Exception as e:
        logger.error(f"Error in /remove: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /update SYMBOL SHARES AVG_COST"""
    if not await check_user(update):
        return
    
    try:
        parts = update.message.text.split()
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format\n\n"
                "Usage: /update SYMBOL SHARES AVG_COST\n"
                "Example: /update ECHO 60 13.00"
            )
            return
        
        symbol = parts[1].upper()
        shares = int(parts[2])
        avg_cost = float(parts[3])
        
        # Validation
        if shares <= 0:
            await update.message.reply_text("❌ Shares must be > 0")
            return
        
        if avg_cost <= 0:
            await update.message.reply_text("❌ Cost must be > 0")
            return
        
        # Check if exists
        if not get_holding(symbol):
            await update.message.reply_text(f"❌ {symbol} not found in portfolio")
            return
        
        # Update
        if update_holding(symbol, shares, avg_cost):
            await update.message.reply_text(
                f"✅ Updated {symbol}\n"
                f"Shares: {shares}\n"
                f"Avg Cost: ${avg_cost}"
            )
        else:
            await update.message.reply_text(f"❌ Error updating {symbol}")
    
    except ValueError:
        await update.message.reply_text("❌ Shares and cost must be numbers")
    except Exception as e:
        logger.error(f"Error in /update: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list and /portfolio"""
    if not await check_user(update):
        return
    
    try:
        holdings = get_all_holdings()
        
        if not holdings:
            await update.message.reply_text("📭 Portfolio is empty")
            return
        
        # Calculate metrics
        metrics = calculate_portfolio_metrics()
        
        # Format message
        msg = "📊 *Current Portfolio*\n\n"
        
        for holding in metrics["holdings"]:
            emoji = "🟢" if holding["daily_change_%"] >= 0 else "🔴"
            msg += (
                f"*{holding['symbol']}*\n"
                f"  Shares: {holding['shares']} @ ${holding['avg_cost']}\n"
                f"  Current: ${holding['current_price']} {emoji} {holding['daily_change_%']:+.2f}%\n"
                f"  Value: ${holding['current_value']}\n"
                f"  Gain: ${holding['unrealized_gain']:+.2f} ({holding['unrealized_gain_pct']:+.2f}%)\n\n"
            )
        
        msg += (
            f"*Portfolio Total*\n"
            f"Value: ${metrics['total_value']}\n"
            f"Cost Basis: ${metrics['total_cost_basis']}\n"
            f"Gain: ${metrics['unrealized_gain']:+.2f} ({metrics['unrealized_gain_pct']:+.2f}%)\n"
            f"Today: {emoji} ${metrics['daily_change_$']:+.2f} ({metrics['daily_change_%']:+.2f}%)"
        )
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    
    except Exception as e:
        logger.error(f"Error in /list: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear (with confirmation)"""
    if not await check_user(update):
        return
    
    msg = "⚠️ This will delete ALL holdings!\n\nReply with 'YES' to confirm or anything else to cancel"
    await update.message.reply_text(msg)
    return CONFIRM_CLEAR

async def clear_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle clear confirmation"""
    if update.message.text.upper() == "YES":
        clear_all_holdings()
        await update.message.reply_text("✅ Portfolio cleared")
    else:
        await update.message.reply_text("❌ Cancelled")
    
    return ConversationHandler.END

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sync (manually trigger daily report)"""
    if not await check_user(update):
        return
    
    try:
        await update.message.reply_text("🔄 Generating daily report...")
        await send_daily_report(context)
        await update.message.reply_text("✅ Report sent")
    except Exception as e:
        logger.error(f"Error in /sync: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help"""
    if not await check_user(update):
        return
    
    help_text = """
*📊 Stock Portfolio Bot Commands*

*/add SYMBOL SHARES AVG_COST*
  Add new holding
  Example: /add ECHO 50 12.50

*/remove SYMBOL*
  Remove holding
  Example: /remove ECHO

*/update SYMBOL SHARES AVG_COST*
  Update existing holding
  Example: /update ECHO 60 13.00

*/list* or */portfolio*
  Show current portfolio with P&L

*/clear*
  Delete all holdings (asks for confirmation)

*/sync*
  Manually trigger daily report

*/help*
  Show this message
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start"""
    if not await check_user(update):
        return
    
    msg = "👋 Stock portfolio bot ready!\n\nType /help for commands"
    await update.message.reply_text(msg)