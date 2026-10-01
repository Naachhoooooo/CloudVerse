from telegram import Update
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger

logger = get_logger(__name__)

import os

async def handle_topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /topic command, breaking down into sub-routines."""
    team_group = os.getenv("TEAM_CLOUDVERSE_GROUP_ID")
    support_group = os.getenv("SUPPORT_GROUP_ID")
    
    if not update.message:
        return
    
    chat_id_str = str(update.message.chat_id)
    if chat_id_str not in [str(team_group), str(support_group)]:
        return  # Silently ignore if outside configured groups

    if update.message.text and update.message.text.strip().lower() == "/topic all" and chat_id_str == str(team_group):
        return await _handle_ping_all_topics(update, context)

    if update.message.message_thread_id:
        return await _handle_forum_topic_ping(update, context, chat_id_str, str(support_group))
        
    return await _handle_unknown_topic_ping(update, context)


async def _handle_ping_all_topics(update, context):
    topics_to_ping = [
        ("Access", os.getenv("ACCESS_TOPIC_ID") or os.getenv("Access_TOPIC_ID")),
        ("Flags", os.getenv("FLAGS_TOPIC_ID") or os.getenv("Flags_TOPIC_ID")),
        ("Broadcasts", os.getenv("BROADCASTS_TOPIC_ID") or os.getenv("Broadcasts_TOPIC_ID")),
        ("Management", os.getenv("MANAGEMENT_TOPIC_ID") or os.getenv("Management_TOPIC_ID")),
        ("Alerts", os.getenv("ALERTS_TOPIC_ID") or os.getenv("Alerts_TOPIC_ID")),
        ("Bugs", os.getenv("BUGS_TOPIC_ID") or os.getenv("Bugs_TOPIC_ID")),
        ("backup", os.getenv("BACKUP_TOPIC_ID") or os.getenv("BACKUP_TOPIC_ID")),
    ]
    chat_id = update.message.chat_id
    for _, t_id in topics_to_ping:
        if t_id:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=int(t_id),
                    text=f"?? Chat ID: {chat_id}\n?? Topic ID : {t_id}",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.debug(f"Failed to ping topic {t_id}: {e}")

async def _handle_forum_topic_ping(update, context, current_chat_id_str: str, support_group_id_str: str):
    thread_id = update.message.message_thread_id
    chat_id = update.message.chat_id
    user_info = ""
    
    if current_chat_id_str == support_group_id_str:
        user_info = "User Info: (Database lookup removed for cross-bot compatibility)\n"
            
    await context.bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text=f"{user_info}?? Chat ID: {chat_id}\n?? Topic ID : {thread_id}",
        parse_mode="HTML"
    )

async def _handle_unknown_topic_ping(update, context):
    chat_id = update.message.chat_id
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"This does not appear to be a topic in a forum group.\n?? Chat ID: {chat_id}",
        parse_mode="HTML"
    )
