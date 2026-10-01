import os
import html
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors

logger = get_logger(__name__)

async def _get_ticket_manager(ctx: ContextTypes.DEFAULT_TYPE):
    manager = ctx.bot_data.get('ticket_manager')
    if not manager:
        logger.error("TicketManager not found in bot_data. Did you initialize it?")
    return manager

@handle_errors
async def support_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /support command from the bot menu."""
    manager = await _get_ticket_manager(context)
    if not manager: return

    support_group_id = os.getenv("SUPPORT_GROUP_ID")
    if not update.message or update.effective_chat.type != "private":
        return

    if not support_group_id:
        await update.message.reply_text(
            "⚠️ <b>CloudVerse Support is currently offline.</b>\n\n"
            "The administrator has not configured the support group setup yet. Please try again later.",
            parse_mode="HTML"
        )
        return

    telegram_id = update.effective_user.id
    username = update.effective_user.username
    name = update.effective_user.full_name

    source_system = context.bot.username if context.bot.username else "CloudVerseBot"
    await manager.authorize_user(telegram_id, username, name, source_system)

    active_ticket = await manager.get_active_ticket(telegram_id)
    if active_ticket:
        await update.message.reply_text(
            f"👋 Welcome back to <b>CloudVerse Support</b>!\n\n"
            f"You currently have an active ticket (<code>{active_ticket['ticket_code']}</code>). Send your message whenever you are ready.",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "👋 Welcome to <b>CloudVerse Support</b>!\n\n"
            "Please describe your issue in detail. Our team will review your message and reply here as soon as possible.\n\n"
            "<i>Note: Please do not spam messages, as it may delay your response.</i>",
            parse_mode="HTML"
        )

@handle_errors
async def support_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles normal user messages, routes them to active support tickets."""
    manager = await _get_ticket_manager(context)
    if not manager: return

    support_group_id = os.getenv("SUPPORT_GROUP_ID")
    if not update.message or update.effective_chat.type != "private" or not support_group_id:
        return
        
    if update.message.text and update.message.text.startswith('/'):
        return

    telegram_id = update.effective_user.id

    if not await manager.is_authorized(telegram_id):
        return

    db_user = await manager.get_user(telegram_id)
    if not db_user:
        return

    topic_id = db_user.get('topic_id')
    active_ticket = await manager.get_active_ticket(telegram_id)

    try:
        if not active_ticket:
            ticket_code = await manager.generate_next_ticket_code()
            
            if not topic_id:
                topic_name = f"{update.effective_user.full_name} ({telegram_id})"
                try:
                    forum_topic = await context.bot.create_forum_topic(
                        chat_id=support_group_id,
                        name=topic_name
                    )
                    topic_id = forum_topic.message_thread_id
                    await manager.set_user_topic(telegram_id, topic_id)
                    logger.info(f"Created dedicated topic {topic_id} for user {telegram_id}")
                except Exception as e:
                    logger.error(f"Failed to create forum topic for user {telegram_id}: {e}")
                    await update.message.reply_text("⚠️ <b>Support system is currently experiencing technical difficulties.</b>", parse_mode="HTML")
                    return

            await manager.create_ticket(ticket_code, telegram_id)

            safe_name = html.escape(update.effective_user.full_name)
            safe_username = html.escape(update.effective_user.username) if update.effective_user.username else 'N/A'
            safe_source = html.escape(db_user.get('source_system', 'Unknown'))
            
            intro_text = (
                f"🚨 <b>New Support Ticket</b>\n\n"
                f"<b>Ticket Code:</b> <code>{ticket_code}</code>\n"
                f"<b>Source:</b> <code>{safe_source}</code>\n"
                f"<b>Name:</b> {safe_name}\n"
                f"<b>Username:</b> @{safe_username}\n"
                f"<b>ID:</b> <code>{telegram_id}</code>\n\n"
                f"Reply to user messages below to send a response."
            )
            
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Solved", callback_data=f"support_action:ticket_solve:{telegram_id}"),
                    InlineKeyboardButton("❌ Unsolved", callback_data=f"support_action:ticket_unsolve:{telegram_id}")
                ],
                [InlineKeyboardButton("🚩 Flag User", callback_data=f"support_action:ticket_flag:{telegram_id}")]
            ])

            await context.bot.send_message(
                chat_id=support_group_id,
                message_thread_id=topic_id,
                text=intro_text,
                parse_mode="HTML",
                reply_markup=keyboard
            )

            await update.message.reply_text(
                f"🎟️ Your ticket has been opened (Code: <code>{ticket_code}</code>). A team member will reply shortly.",
                parse_mode="HTML"
            )

        await context.bot.forward_message(
            chat_id=support_group_id,
            message_thread_id=topic_id,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )

    except Exception as e:
        logger.error(f"Error handling user message from {telegram_id}: {e}")
        await update.message.reply_text("⚠️ Sorry, our support system is currently experiencing issues.")

@handle_errors
async def support_admin_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles admin replies from the support forum group."""
    manager = await _get_ticket_manager(context)
    if not manager: return

    support_group_id = os.getenv("SUPPORT_GROUP_ID")
    if not update.message or str(update.effective_chat.id) != str(support_group_id):
        return
        
    if not update.message.is_topic_message:
        return
        
    topic_id = update.message.message_thread_id
    if update.message.from_user.is_bot:
        return
        
    try:
        db_user = await manager.get_user_by_topic(topic_id)
        if not db_user:
            return
            
        telegram_id = db_user['telegram_id']
        admin_name = update.message.from_user.first_name
        
        active_ticket = await manager.get_active_ticket(telegram_id)
        if active_ticket and not active_ticket.get('admin_replied'):
            ticket_code = active_ticket['ticket_code']
            safe_admin_name = html.escape(admin_name)
            welcome_text = (
                f"👋 <b>{safe_admin_name} has joined the chat!</b>\n\n"
                f"You are now connected with a team member for Ticket <code>{ticket_code}</code>.\n"
                f"If you have any screenshots or screen recordings of the issue, please send them now to assist us."
            )
            try:
                await context.bot.send_message(chat_id=telegram_id, text=welcome_text, parse_mode="HTML")
                await manager.mark_admin_replied(ticket_code)
            except Exception as e:
                logger.warning(f"Could not send welcome message to user {telegram_id}: {e}")

        await context.bot.copy_message(
            chat_id=telegram_id,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
        
        try:
            await update.message.set_reaction(reaction="👍")
        except Exception:
            pass
            
    except Exception as e:
        logger.error(f"Error handling admin reply in topic {topic_id}: {e}")

@handle_errors
async def support_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline button callbacks (e.g. solve, unsolve, flag)."""
    manager = await _get_ticket_manager(context)
    if not manager: return

    support_group_id = os.getenv("SUPPORT_GROUP_ID")
    team_group_id = os.getenv("TEAM_CLOUDVERSE_GROUP_ID")
    flags_topic_id = os.getenv("FLAGS_TOPIC_ID")

    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("support_action:"):
        return

    try:
        payload = data.replace("support_action:", "")
        action, telegram_id_str = payload.split(":")
        telegram_id = int(telegram_id_str)
        topic_id = query.message.message_thread_id

        active_ticket = await manager.get_active_ticket(telegram_id)
        admin_name = query.from_user.first_name
        admin_details = f"@{query.from_user.username}" if query.from_user.username else admin_name

        if action in ["ticket_solve", "ticket_unsolve"]:
            if not active_ticket:
                await query.answer("This ticket is already closed or resolved.", show_alert=True)
                return
            ticket_code = active_ticket['ticket_code']
            
            is_solved = (action == "ticket_solve")
            status = "CLOSED_SOLVED" if is_solved else "CLOSED_UNSOLVED"
            note = "Marked solved by admin." if is_solved else "Marked unsolved by admin."
            
            await manager.close_ticket(ticket_code, status, note, admin_details)
            await manager.purge_auth(telegram_id)
            
            state_str = "Solved" if is_solved else "Unsolved"
            icon = "✅" if is_solved else "❌"
            
            user_message = f"{icon} <b>Your support ticket (<code>{ticket_code}</code>) has been closed and marked as {state_str}.</b>\n\nIf you need further assistance, please use /support to open a new ticket."
            admin_message = f"{icon} Ticket <b><code>{ticket_code}</code></b> closed as <b>{state_str}</b> by {html.escape(admin_name)}."
            
            try:
                await context.bot.send_message(chat_id=telegram_id, text=user_message, parse_mode="HTML")
            except Exception:
                pass

            try:
                original_text = html.escape(query.message.text) if query.message.text else ""
                await query.edit_message_text(text=f"{original_text}\n\n{admin_message}", parse_mode="HTML", reply_markup=None)
            except Exception:
                pass
            
        elif action == "ticket_flag":
            ticket_code = active_ticket['ticket_code'] if active_ticket else "UNKNOWN"
            text = (
                f"🚩 <b>User Flagged from Ticket</b> 🚩\n\n"
                f"• <b>Ticket Number:</b> <code>{ticket_code}</code>\n"
                f"• <b>User ID:</b> <code>{telegram_id}</code>\n"
                f"• <b>Flagged By:</b> {html.escape(admin_name)}\n\n"
                f"🔗 <b>Source:</b> <a href=\"https://t.me/c/{str(support_group_id).replace('-100', '')}/{topic_id}\">Support Ticket</a>"
            )
            if team_group_id and flags_topic_id:
                await context.bot.send_message(
                    chat_id=team_group_id,
                    message_thread_id=int(flags_topic_id),
                    text=text,
                    parse_mode="HTML"
                )
            await query.answer("User flagged and report sent.", show_alert=True)

    except Exception as e:
        logger.error(f"Error handling admin ticket action: {e}")

def register_handlers(app):
    from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters
    app.add_handler(CommandHandler("support", support_command_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, support_message_handler), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, support_admin_reply_handler), group=2)
    app.add_handler(CallbackQueryHandler(support_callback_handler, pattern=r"^support_action:"))
