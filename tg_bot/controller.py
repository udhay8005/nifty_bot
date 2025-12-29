import logging
import time
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackContext, MessageHandler, Filters

# Local Imports
import config
from infra.db import log_audit, get_setting, set_param, get_trade_history, get_weekly_pnl

logger = logging.getLogger("TelegramController")

class TelegramController:
    def __init__(self, context, bot_token):
        """
        The Commander. Maps Telegram commands to Bot actions.
        """
        self.ctx = context
        self.admin_ids = config.ADMIN_CHAT_IDS
        
        # Initialize Updater with robust timeout settings for mobile networks
        self.updater = Updater(
            bot_token, 
            use_context=True,
            request_kwargs={'read_timeout': 20, 'connect_timeout': 20}
        )
        self.dispatcher = self.updater.dispatcher

        # Register the Strategy Alert Callback
        # This allows the Strategy to say "self.ctx.telegram_alert()" and have it sent here.
        self.ctx.set_alert_callback(self.broadcast_message)
        
        # Load Handlers
        self._register_handlers()

    def _register_handlers(self):
        """Registers all command handlers."""
        dp = self.dispatcher

        # --- 🟢 BASIC COMMANDS ---
        dp.add_handler(CommandHandler("start", self.cmd_start))
        dp.add_handler(CommandHandler("help", self.cmd_help))
        dp.add_handler(CommandHandler("health", self.cmd_health))
        dp.add_handler(CommandHandler("status", self.cmd_status))
        dp.add_handler(CommandHandler("profile", self.cmd_profile)) # Connection Check
        dp.add_handler(CommandHandler("weekly", self.cmd_weekly))   # 🆕 Weekly PnL Check

        # --- ⚙️ CONFIGURATION (The Brain) ---
        dp.add_handler(CommandHandler("mode", self.cmd_mode))
        dp.add_handler(CommandHandler("set_token", self.cmd_set_token))
        dp.add_handler(CommandHandler("set_strategy", self.cmd_set_strategy)) # Target, SL, Lots
        dp.add_handler(CommandHandler("set_trigger", self.cmd_set_trigger))   # Premium Price (180)

        # --- 📊 REPORTING (The Memory) ---
        dp.add_handler(CommandHandler("history", self.cmd_history))

        # --- ⏯️ OPERATIONS ---
        dp.add_handler(CommandHandler("pause", self.cmd_pause))
        dp.add_handler(CommandHandler("resume", self.cmd_resume))
        dp.add_handler(CommandHandler("system_reset", self.cmd_system_reset)) # Un-Kill

        # --- 🚨 EMERGENCY ---
        dp.add_handler(CommandHandler("kill", self.cmd_kill))
        dp.add_handler(CommandHandler("kill_confirm", self.cmd_kill_confirm))

        # Error Handler
        dp.add_error_handler(self.error_handler)

    # =========================================================
    # 🛡️ SECURITY & UTILS
    # =========================================================

    def check_admin(self, update: Update) -> bool:
        """Ensures only YOU can control the bot."""
        if not update.effective_user: return False
        user_id = update.effective_user.id
        if user_id not in self.admin_ids:
            logger.warning(f"⛔ Unauthorized access attempt by ID: {user_id}")
            update.message.reply_text("⛔ **Unauthorized Access.**")
            return False
        return True

    def broadcast_message(self, message):
        """Sends a message to all configured Admin IDs."""
        for chat_id in self.admin_ids:
            try:
                self.updater.bot.send_message(
                    chat_id=chat_id, 
                    text=message, 
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Failed to broadcast to {chat_id}: {e}")

    # =========================================================
    # 🟢 COMMAND HANDLERS
    # =========================================================

    def cmd_start(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        update.message.reply_text(
            "🤖 **Nifty Option Bot Connected**\n"
            "Ready to trade. Use `/help` for commands.",
            parse_mode=ParseMode.MARKDOWN
        )

    def cmd_help(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        msg = (
            "🛠 **Command List**\n\n"
            "**Control:**\n"
            "`/status` - View Mode & Strategy\n"
            "`/profile` - Check Broker Connection\n"
            "`/weekly` - View Weekly PnL\n"
            "`/mode <live|paper>` - Switch Engine\n"
            "`/pause` / `/resume` - Stop/Start Entry\n\n"
            "**Tuning:**\n"
            "`/set_strategy <TGT> <SL> <QTY>`\n"
            "`/set_trigger <PRICE>`\n\n"
            "**Emergency:**\n"
            "`/kill` - 🚨 STOP & CLOSE ALL\n"
            "`/system_reset` - Un-kill the bot"
        )
        update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    def cmd_status(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        
        flags = self.ctx.get_flags()
        params = self.ctx.params # Read from Context Memory
        
        # Icons
        mode_icon = "🚀" if flags['mode'] == 'live' else "🧪"
        state_icon = "⏸️" if flags['paused'] else ("☠️" if flags['killed'] else "▶️")
        broker_icon = "✅" if flags['broker_connected'] else "❌"
        token_status = "✅ Set" if get_setting('UPSTOX_ACCESS_TOKEN') else "❌ Missing"

        msg = (
            f"{state_icon} **System Status**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• Mode: {mode_icon} `{flags['mode'].upper()}`\n"
            f"• Broker: {broker_icon}\n"
            f"• Token: {token_status}\n\n"
            f"🧠 **Strategy Config**\n"
            f"• Trigger: `{params.get('TARGET_PREMIUM')}`\n"
            f"• Target: `{params.get('TARGET_POINTS')}` pts\n"
            f"• StopLoss: `{params.get('SL_POINTS')}` pts\n"
            f"• Lot Size: `{params.get('LOT_SIZE')}`\n"
            f"• Trailing: `{'ON' if params.get('TRAILING_ON')=='1' else 'OFF'}`"
        )
        update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    def cmd_profile(self, update: Update, context: CallbackContext):
        """
        Fetches live profile data from the Broker to verify connection.
        """
        if not self.check_admin(update): return
        
        broker = self.ctx.broker
        if not broker:
            update.message.reply_text("❌ **Broker Not Initialized.** Check Token.", parse_mode=ParseMode.MARKDOWN)
            return

        update.message.reply_text("🔄 Fetching Profile...", parse_mode=ParseMode.MARKDOWN)

        # 1. Access the API (Handle Paper Mode Wrapper)
        profile_data = None
        try:
            # If we are in Paper Mode, the broker is a wrapper. 
            # We try to access the underlying real_broker if the wrapper doesn't have the method.
            if hasattr(broker, 'get_profile'):
                profile_data = broker.get_profile()
            elif hasattr(broker, 'real_broker') and broker.real_broker:
                profile_data = broker.real_broker.get_profile()
        except Exception as e:
            logger.error(f"Profile Command Error: {e}")

        # 2. Respond
        if profile_data:
            name = profile_data.get('name', 'Unknown')
            funds = profile_data.get('funds', 0.0)
            
            msg = (f"✅ Connected as **{name}**.\n"
                   f"Funds: `₹{funds:,.2f}`")
            update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            update.message.reply_text(
                "⚠️ **Profile Fetch Failed.**\n"
                "Broker may be in Blind Mode (No Token) or API is down.",
                parse_mode=ParseMode.MARKDOWN
            )

    def cmd_weekly(self, update: Update, context: CallbackContext):
        """
        Displays the Weekly PnL status and proximity to the Kill Switch.
        """
        if not self.check_admin(update): return
        
        try:
            pnl = get_weekly_pnl()
            limit = config.WEEKLY_MAX_LOSS
            
            icon = "📈" if pnl >= 0 else "📉"
            status = "safe"
            
            # Check proximity to limit
            if pnl < -limit:
                status = "LOCKED ⛔"
            elif pnl < -(limit * 0.8):
                status = "CRITICAL ⚠️"
            
            msg = (f"{icon} **Weekly Performance**\n"
                   f"Net PnL: `₹{pnl:,.2f}`\n"
                   f"Max Loss Limit: `₹{limit:,.2f}`\n"
                   f"Status: **{status.upper()}**")
            
            update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.error(f"Weekly Cmd Error: {e}")
            update.message.reply_text("❌ Failed to fetch weekly stats.")

    # =========================================================
    # 🧠 STRATEGY TUNING
    # =========================================================

    def cmd_set_strategy(self, update: Update, context: CallbackContext):
        """Updates Target, SL, and Lot Size instantly."""
        if not self.check_admin(update): return
        try:
            if len(context.args) < 3:
                raise ValueError
            
            tgt = float(context.args[0])
            sl = float(context.args[1])
            qty = int(context.args[2])
            
            # 1. Update DB (The Brain)
            set_param('TARGET_POINTS', tgt)
            set_param('SL_POINTS', sl)
            set_param('LOT_SIZE', qty)
            
            # 2. Refresh Context (The Memory)
            self.ctx.refresh_params()
            
            update.message.reply_text(f"✅ **Strategy Updated**\nTarget: {tgt} | SL: {sl} | Qty: {qty}", parse_mode=ParseMode.MARKDOWN)
            log_audit(update.effective_chat.id, '/set_strategy', f"{tgt}/{sl}/{qty}")
            
        except ValueError:
            update.message.reply_text("⚠️ Usage: `/set_strategy <TARGET> <SL> <QTY>`\nExample: `/set_strategy 40 20 50`", parse_mode=ParseMode.MARKDOWN)

    def cmd_set_trigger(self, update: Update, context: CallbackContext):
        """Updates the Breakout Premium Price."""
        if not self.check_admin(update): return
        try:
            if not context.args: raise ValueError
            price = float(context.args[0])
            
            set_param('TARGET_PREMIUM', price)
            self.ctx.refresh_params()
            
            update.message.reply_text(f"✅ **Trigger Price Updated**\nNew Breakout Level: `{price}`", parse_mode=ParseMode.MARKDOWN)
            log_audit(update.effective_chat.id, '/set_trigger', str(price))
        except ValueError:
            update.message.reply_text("⚠️ Usage: `/set_trigger <PRICE>`\nExample: `/set_trigger 180`", parse_mode=ParseMode.MARKDOWN)

    # =========================================================
    # ⚙️ SYSTEM OPERATIONS
    # =========================================================

    def cmd_mode(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        
        if not context.args:
            update.message.reply_text("⚠️ Usage: `/mode <live|paper>`", parse_mode=ParseMode.MARKDOWN)
            return

        target_mode = context.args[0].lower()
        if target_mode not in ['live', 'paper']:
            update.message.reply_text("❌ Invalid mode. Use 'live' or 'paper'.")
            return

        try:
            self.ctx.switch_mode(target_mode)
            update.message.reply_text(f"🔄 Switched to **{target_mode.upper()}** Mode.", parse_mode=ParseMode.MARKDOWN)
            log_audit(update.effective_chat.id, '/mode', target_mode)
        except Exception as e:
            update.message.reply_text(f"❌ Error switching mode: {e}")

    def cmd_set_token(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        
        if not context.args:
            update.message.reply_text("⚠️ Usage: `/set_token <YOUR_ACCESS_TOKEN>`", parse_mode=ParseMode.MARKDOWN)
            return
            
        token = context.args[0]
        try:
            self.ctx.update_runtime_token(token)
            update.message.reply_text("✅ **Token Updated Successfully.**\nBroker re-initialized.", parse_mode=ParseMode.MARKDOWN)
            # Delete user message for security
            try: context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
            except: pass
        except Exception as e:
            update.message.reply_text(f"❌ Failed to update token: {e}")

    def cmd_history(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        
        history = get_trade_history(limit=5)
        if not history:
            update.message.reply_text("📉 No trades recorded yet.")
            return

        msg = "📜 **Recent Trade History**\n"
        for t in history:
            icon = "🟢" if t['pnl'] > 0 else "🔴"
            mode_badge = "🧪" if t['mode'] == 'PAPER' else "🚀"
            msg += (
                f"\n{icon} **{t['date']}** ({mode_badge})\n"
                f"   {t['side']} | PnL: ₹{t['pnl']}\n"
                f"   {t['entry_price']} ➝ {t['exit_price']}\n"
            )
        
        update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    # =========================================================
    # ⏯️ PAUSE / RESUME / KILL
    # =========================================================

    def cmd_pause(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        self.ctx.toggle_pause(True)
        update.message.reply_text("⏸️ **System PAUSED.**\nNo new entries will be taken.", parse_mode=ParseMode.MARKDOWN)

    def cmd_resume(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        if self.ctx.killed:
            update.message.reply_text("❌ System is **KILLED**. Use `/system_reset` first.", parse_mode=ParseMode.MARKDOWN)
            return
        self.ctx.toggle_pause(False)
        update.message.reply_text("▶️ **System RESUMED.**", parse_mode=ParseMode.MARKDOWN)

    def cmd_kill(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        
        import random
        code = str(random.randint(1000, 9999))
        self.ctx.kill_confirmations[update.effective_user.id] = code
        
        update.message.reply_text(
            f"🚨 **EMERGENCY KILL REQUEST** 🚨\n\n"
            "This will:\n"
            "1. Cancel ALL Orders\n"
            "2. Close ALL Positions\n"
            "3. Lock the System\n\n"
            f"To confirm, reply:\n`/kill_confirm {code}`",
            parse_mode=ParseMode.MARKDOWN
        )

    def cmd_kill_confirm(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        
        user_id = update.effective_user.id
        saved_code = self.ctx.kill_confirmations.get(user_id)
        
        if not context.args or context.args[0] != saved_code:
            update.message.reply_text("❌ Invalid Confirmation Code.")
            return
            
        # Execute Kill
        self.ctx.emergency_kill()
        self.ctx.kill_confirmations.pop(user_id, None)
        
        update.message.reply_text("☠️ **SYSTEM KILLED.**\nAll operations stopped. Check broker manually.", parse_mode=ParseMode.MARKDOWN)

    def cmd_system_reset(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        self.ctx.system_reset()
        update.message.reply_text("✅ **System Reset Complete.**\nKilled state cleared. Broker re-connected.", parse_mode=ParseMode.MARKDOWN)

    def cmd_health(self, update: Update, context: CallbackContext):
        if not self.check_admin(update): return
        update.message.reply_text("💓 System is Online.")

    # =========================================================
    # 🔧 INTERNALS
    # =========================================================

    def error_handler(self, update: Update, context: CallbackContext):
        """Log Errors caused by Updates."""
        logger.error(f'Telegram Error: {context.error}')

    def start(self):
        """Starts the Bot Polling Loop."""
        logger.info("Telegram Polling Started...")
        self.updater.start_polling(drop_pending_updates=True, poll_interval=1.0)