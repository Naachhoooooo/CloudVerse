import humanize
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import psutil
import os
from shared.core.AsyncUtils import track_task
from shared.core.Logger import get_logger
from shared.managers.ServerManager import get_server_manager
from shared.managers.TransferManager.TransferTracker import get_transfer_tracker
from shared.core.UserState import UserState, UserStateEnum

logger = get_logger(__name__)

async def _render_performance_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = getattr(update, "callback_query", None)
    m = getattr(update, "message", None)
    
    server_manager = get_server_manager()
    server = await server_manager.get_server_stats()
    bot = await server_manager.get_bot_stats()
    
    from bots.administrator.components.db_utils import get_global_user_stats
    user_stats = await get_global_user_stats()
    bot.update(user_stats)


    temp_display = f"{server['temperature']}°C" if server["temperature"] is not None else "N/A"
    lanes = bot["lanes"]
    public_lane = f"{lanes['public']['active']}/{lanes['public']['capacity']}"
    private_lane = f"{lanes['private']['active']}/{lanes['private']['capacity']}"
    public_waiting = lanes["public"]["waiting"]
    private_waiting = lanes["private"]["waiting"]
    last_updated = datetime.now().strftime("%H:%M:%S")
    
    tracker = get_transfer_tracker()
    lane_mode = tracker.get_lane_mode()

    text = (
        "📊 <b>Server Performance</b>\n\n"
        "모 <b>Hardware Metrics</b>\n"
        f"├ Bot Response Time: {server.get('response_time', 0):.0f} ms\n"
        f"├ Live Bandwidth: {server['live_bandwidth']} Mbps\n"
        f"├ Today's Traffic: {server['bandwidth_today']:.2f} MB\n"
        f"├ Temperature: {temp_display}\n"
        f"├ Load Average: {server['load']:.2f}\n"
        f"├ CPU Usage: {server['cpu']}%\n"
        f"├ RAM Usage: {server['mem']}%\n"
        f"└ Uptime: {int(server['uptime'] // 3600)}h {int((server['uptime'] % 3600) // 60)}m {int(server['uptime'] % 60)}s\n\n"
        "⭐<b>Userbase</b>\n"
        f"├ Registered Today: {bot.get('registered_today', 0)}\n"
        f"├ Super Admins: {bot.get('super_admins', 0)}\n"
        f"├ Admins: {bot.get('admins', 0)}\n"
        f"├ Whitelisted: {bot.get('whitelisted', 0)}\n"
        f"├ Pending: {bot.get('pending', 0)}\n"
        f"├ Blacklisted: {bot.get('blacklisted', 0)}\n"
        f"└ Total Users: {bot.get('total_users', 0)}\n\n"
        f"⛙ <b>Global Lanes</b>\n"
        f"├ Routing Mode: <b>{lane_mode.upper()}</b>\n"
        f"├ 🌍 Public: {public_lane} ({public_waiting} waiting)\n"
        f"└ 🛡️ Private: {private_lane} ({private_waiting} waiting)\n\n"
        f"<i>Last update: {last_updated}</i>"
    )

    buttons = []
    if lane_mode == "auto":
        buttons.append([InlineKeyboardButton("🛠️ Enable Manual Routing", callback_data="server_mode_manual")])
    else:
        buttons.extend([
            [
                InlineKeyboardButton("➖", callback_data="server_dec_public"),
                InlineKeyboardButton("🌍 Public", callback_data="noop"),
                InlineKeyboardButton("➕", callback_data="server_inc_public"),
            ],
            [
                InlineKeyboardButton("➖", callback_data="server_dec_private"),
                InlineKeyboardButton("🛡️ Private", callback_data="noop"),
                InlineKeyboardButton("➕", callback_data="server_inc_private"),
            ],
            [InlineKeyboardButton("🤖 Enable Auto Routing", callback_data="server_mode_auto")],
        ])

    buttons.extend([
        [
            InlineKeyboardButton("🛑 Terminate Drive", callback_data="terminate_bot:drive"),
            InlineKeyboardButton("🛑 Terminate Mega", callback_data="terminate_bot:mega"),
            InlineKeyboardButton("🛑 Terminate Rclone", callback_data="terminate_bot:rclone"),
        ],
        [InlineKeyboardButton("Refresh", callback_data="server_refresh")],
    ])

    markup = InlineKeyboardMarkup(buttons)
    try:
        if q:
            await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
        else:
            await m.reply_text(text, reply_markup=markup, parse_mode="HTML")
            
        ctx.user_data["state"] = UserState(ctx)
        ctx.user_data["state"].set_state(UserStateEnum.PERFORMANCE_PANEL)
    except Exception as e:
        logger.error(f"Error in performance panel: {e}")
        await get_server_manager().send_error_notification(
            error_message=f"Performance Panel Error: {str(e)}",
            error_type="Performance Monitor",
            severity="MEDIUM",
        )
        if q:
            await q.edit_message_text(
                "❌ Failed to load performance stats. Admins notified.",
            )
        else:
            await m.reply_text("❌ Failed to load performance stats. Admins notified.")

async def handle_server_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    await _render_performance_panel(update, ctx)

async def handle_server_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    q = update.callback_query
    if not q or not q.from_user:
        return

    await q.answer()
    data = getattr(q, "data", None)
    tracker = get_transfer_tracker()
    
    if data == "server_refresh":
        pass
    elif data == "server_mode_auto":
        tracker.set_lane_mode("auto")
    elif data == "server_mode_manual":
        tracker.set_lane_mode("manual")
    elif data == "server_inc_public":
        current = tracker.get_manual_lane_limits().get("public", 3)
        tracker.set_manual_lane_limit("public", min(10, current + 1))
    elif data == "server_dec_public":
        current = tracker.get_manual_lane_limits().get("public", 3)
        tracker.set_manual_lane_limit("public", max(1, current - 1))
    elif data == "server_inc_private":
        current = tracker.get_manual_lane_limits().get("private", 2)
        tracker.set_manual_lane_limit("private", min(4, current + 1))
    elif data == "server_dec_private":
        current = tracker.get_manual_lane_limits().get("private", 2)
        tracker.set_manual_lane_limit("private", max(0, current - 1))
    elif data == "terminate_server":
        telegram_id = q.from_user.id
        username = q.from_user.username or "Unknown"
        
        from bots.administrator.components.db_utils import get_account_repo
        account_repo = get_account_repo("drive")
        if not account_repo or not await account_repo.is_super_admin(telegram_id=telegram_id):
            await q.answer("❌ Access denied. Super Admin privileges required.", show_alert=True)
            return
        
        import html
        safe_username = html.escape(username)
        confirmation_text = (
            "🚨 <b>SERVER TERMINATION CONFIRMATION</b> | Bot Server\n\n"
            "⚠️ <b>WARNING:</b> This action will completely shut down the server.\n\n"
            "<b>Consequences:</b>\n"
            "• Services will stop immediately\n"
            "• Active sessions will terminate\n"
            "• File transfers will abort\n"
            "• Manual restart will be required\n\n"
            f"<b>Initiated by:</b> @{safe_username}\n"
            f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "<i>Are you absolutely sure you want to proceed?</i>"
        )
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💥 Yes, Terminate Server", callback_data=f"confirm_terminate:{telegram_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_terminate")
            ]
        ])
        
        await q.edit_message_text(text=confirmation_text, reply_markup=keyboard, parse_mode="HTML")
        return
        
    elif data == "cancel_terminate":
        # Just re-render the performance panel
        await q.answer("❌ Server termination cancelled.", show_alert=True)
        pass
        
    elif data.startswith("confirm_terminate:"):
        requesting_user_id = int(data.split(":")[1])
        current_user_id = q.from_user.id
        
        if requesting_user_id != current_user_id:
            await q.answer("❌ Only the user who initiated termination can confirm it.", show_alert=True)
            return
        
        from bots.administrator.components.db_utils import get_account_repo
        account_repo = get_account_repo("drive")
        if not account_repo or not await account_repo.is_super_admin(telegram_id=current_user_id):
            await q.answer("❌ Access denied.", show_alert=True)
            return
            
        username = q.from_user.username or "Unknown"
        provider = ctx.bot_data.get('provider_name', 'drive').capitalize()
        import html
        safe_username = html.escape(username)
        shutdown_text = (
            f"🔴 <b>SHUTDOWN INITIATED</b> | CloudVerse {provider} Bot\n\n"
            f"<b>Terminated by:</b> @{safe_username}\n"
            f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"<i>Server termination in progress...</i>"
        )
        
        await q.edit_message_text(text=shutdown_text, parse_mode="HTML")
        logger.warning(f"Server termination initiated by {username} ({current_user_id})")
        
        track_task(get_server_manager()._perform_shutdown(username))
        return

    elif data.startswith("terminate_bot:"):
        bot_name = data.split(":")[1]
        
        # UI Logic for terminate bot request
        telegram_id = q.from_user.id
        from bots.administrator.components.db_utils import get_account_repo
        account_repo = get_account_repo("drive")  # Any bot's DB is fine for super admin check
        if not account_repo or not await account_repo.is_super_admin(telegram_id=telegram_id):
            await q.answer("❌ Super Admin privileges required.", show_alert=True)
            return
            
        text = (
            f"🚨 <b>TERMINATE BOT: {bot_name.upper()}</b>\n\n"
            f"Are you sure you want to forcibly terminate the {bot_name} bot process?\n"
            f"Note: If the orchestrator is running, it may attempt to restart it."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"💥 Terminate {bot_name}", callback_data=f"confirm_term_bot:{bot_name}:{telegram_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_term_bot")
            ]
        ])
        await q.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        return
    elif data.startswith("confirm_term_bot:"):
        _, bot_name, requesting_user_id = data.split(":")
        
        if int(requesting_user_id) != q.from_user.id:
            await q.answer("❌ Only the initiator can confirm.", show_alert=True)
            return
            
        await q.edit_message_text(f"🛑 <b>Terminating {bot_name.upper()} bot...</b>", parse_mode="HTML")
        
        # Kill the process
        target_script = f"bots/{bot_name.lower()}/main.py"
        target_script_alt = f"bots\\{bot_name.lower()}\\main.py"
        killed = False
        try:
            for p in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmdline = p.info.get('cmdline')
                    if cmdline:
                        cmd_str = ' '.join(cmdline)
                        if target_script in cmd_str or target_script_alt in cmd_str:
                            logger.warning(f"Requesting graceful shutdown for process {p.pid} ({bot_name} bot)")
                            import sys
                            if sys.platform == "win32":
                                import signal
                                try:
                                    p.send_signal(signal.CTRL_BREAK_EVENT)
                                except Exception:
                                    p.kill() # Fallback if signal fails
                            else:
                                p.terminate()
                            killed = True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            logger.error(f"Error finding process for {bot_name}: {e}")
            
        if killed:
            await q.edit_message_text(f"✅ <b>{bot_name.upper()} bot terminated.</b>", parse_mode="HTML")
        else:
            await q.edit_message_text(f"⚠️ <b>{bot_name.upper()} bot process not found.</b>", parse_mode="HTML")
        return
    elif data == "cancel_term_bot":
        # Just re-render the performance panel as a way to cancel
        pass
        
    await _render_performance_panel(update, ctx)
