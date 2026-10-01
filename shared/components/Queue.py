"""Queue UI component to show transfer statuses."""

from telegram import Update
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.managers.TransferManager.TransferTracker import get_transfer_tracker

logger = get_logger(__name__)

@handle_errors
async def handle_queue_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Command handler for /queue"""
    telegram_id = update.effective_user.id if update.effective_user else None
    if not telegram_id:
        return
    tracker = get_transfer_tracker()
    status = await tracker.get_lane_status_for_user(telegram_id)
    lane = status.get('lane')
    active = status.get('active_in_lane')
    cap = status.get('lane_capacity')
    qlen = status.get('waiting_queue_len')
    pos = status.get('your_position')

    # Active uploads section
    user_transfers = await tracker.get_user_transfers(telegram_id)
    uploads_text = ""
    if user_transfers:
        uploads_text = "\n\n\U0001f4e4 <b>Your Active Transfers</b>\n"
        for tid, info in user_transfers.items():
            if not info:
                continue
            fname = info.get('file_name', 'Unknown')
            total = info.get('total_bytes', 0)
            current = info.get('current_bytes', 0)
            phase = info.get('phase_label', 'Processing')

            if total and total > 0:
                pct = (current / total) * 100
                filled = int(pct / 10)
                bar = '\u2593' * filled + '\u2591' * (10 - filled)
                size_str = f"{current/1024/1024:.1f}/{total/1024/1024:.1f} MB"
                uploads_text += f"  \u251c <code>{fname}</code>\n    [{bar}] {pct:.0f}% \u2022 {size_str}\n    {phase}\n"
            else:
                uploads_text += f"  \u251c <code>{fname}</code>\n    \u23f3 {phase}\n"
    else:
        uploads_text = "\n\n\u2705 No active transfers."

    text = (
        f"\U0001f4ca <b>Queue Status</b> (Lane: {lane})\n\n"
        f"Active in lane: {active}/{cap}\n"
        f"Queue length: {qlen}\n"
        f"Your position: {pos if pos is not None else 'not waiting'}"
        f"{uploads_text}"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode='HTML')

def register_handlers(app):
    from telegram.ext import CommandHandler
    app.add_handler(CommandHandler("queue", handle_queue_status))
