"""Queue component for administrator bot."""

import math
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.managers.AccessManager import admin_required
from bots.administrator.utils.db_utils import get_all_active_transfers, signal_kill_transfer
from shared.managers.ServerManager import get_server_manager

logger = get_logger(__name__)

TRANSFERS_PER_PAGE = 10

@handle_errors
@admin_required
async def handle_queue_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _render_queue_page(update, ctx, page=0, is_callback=False)

@handle_errors
@admin_required
async def handle_queue_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    
    data = q.data
    
    if data == "refresh_queue":
        page = ctx.user_data.get('queue_page', 0)
        await _render_queue_page(update, ctx, page=page, is_callback=True)
    elif data.startswith("queue_page:"):
        page = int(data.split(":")[1])
        ctx.user_data['queue_page'] = page
        await _render_queue_page(update, ctx, page=page, is_callback=True)
    elif data.startswith("queue_details:"):
        transfer_id = data.split(":")[1]
        await _render_queue_details(update, ctx, transfer_id)
    elif data.startswith("queue_kill:"):
        _, bot_name, transfer_id = data.split(":")
        await signal_kill_transfer(bot_name, int(transfer_id))
        page = ctx.user_data.get('queue_page', 0)
        await _render_queue_page(update, ctx, page=page, is_callback=True)

async def _render_queue_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE, page: int, is_callback: bool = False):
    q = getattr(update, "callback_query", None)
    m = q.message if q else update.message

    transfers = await get_all_active_transfers()
    
    # Calculate stats
    total_active = len(transfers)
    
    server_manager = get_server_manager()
    server_stats = await server_manager.get_server_stats()
    load_factor = server_stats.get('load', 0.0) / 100.0 if server_stats.get('load') else 0.0
    
    header = (
        f"Live Traffic Queue\n\n"
        f"System Load: {load_factor*100:.1f}%\n"
        f"Active Transfers: {total_active}"
    )
    
    if not transfers:
        text = f"{header}\n\nNo active transfers."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("Refresh", callback_data="refresh_queue")]])
    else:
        text = header
        
        # Sort transfers by started time (newest first)
        sorted_transfers = sorted(
            transfers, 
            key=lambda t: t.get('started') or '2000-01-01', 
            reverse=True
        )
        
        total_pages = math.ceil(total_active / TRANSFERS_PER_PAGE)
        if page >= total_pages:
            page = max(0, total_pages - 1)
            
        start_idx = page * TRANSFERS_PER_PAGE
        end_idx = start_idx + TRANSFERS_PER_PAGE
        page_transfers = sorted_transfers[start_idx:end_idx]
        
        buttons = []
        for info in page_transfers:
            tid = info.get("id")
            bot_provider = info.get("bot_provider", "unknown")
            username = info.get("username", "Unknown")
            provider = bot_provider.capitalize()
            
            current_bytes = info.get("bytes_transferred", 0)
            total_bytes = info.get("file_size") or info.get("transfer_size") or 0
            
            percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
            current_mb = current_bytes / (1024 * 1024)
            total_mb = total_bytes / (1024 * 1024)
            
            btn_text = f"[{provider}] @{username} | {percent:.1f}% ({current_mb:.1f}/{total_mb:.1f} MB)"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"queue_details:{bot_provider}_{tid}")])
            
        # Pagination controls
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("< Prev", callback_data=f"queue_page:{page-1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next >", callback_data=f"queue_page:{page+1}"))
            
        if nav_buttons:
            buttons.append(nav_buttons)
            
        buttons.append([InlineKeyboardButton("Refresh", callback_data="refresh_queue")])
        markup = InlineKeyboardMarkup(buttons)

    if is_callback and q:
        try:
            if q.message and q.message.text == text:
                return
            await q.edit_message_text(text, reply_markup=markup, parse_mode=None)
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Error editing queue message: {e}")
    else:
        await m.reply_text(text, reply_markup=markup, parse_mode=None)


async def _render_queue_details(update: Update, ctx: ContextTypes.DEFAULT_TYPE, transfer_id_str: str):
    q = update.callback_query
    if not q:
        return
        
    bot_provider, tid = transfer_id_str.split("_", 1)
    tid = int(tid)
    
    transfers = await get_all_active_transfers()
    info = next((t for t in transfers if t.get("id") == tid and t.get("bot_provider") == bot_provider), None)
    
    if not info:
        # Transfer might have finished or been killed
        page = ctx.user_data.get('queue_page', 0)
        await _render_queue_page(update, ctx, page=page, is_callback=True)
        return
        
    username = info.get("username", "Unknown")
    telegram_id = info.get("telegram_id", "Unknown")
    provider = bot_provider.capitalize()
    file_name = info.get("file_name") or info.get("job_id") or "unknown"
    status = info.get("status", "unknown")
    
    current_bytes = info.get("bytes_transferred", 0)
    total_bytes = info.get("file_size") or info.get("transfer_size") or 0
    
    percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
    current_mb = current_bytes / (1024 * 1024)
    total_mb = total_bytes / (1024 * 1024)
    
    started_str = info.get("started")
    elapsed = 0
    if started_str:
        try:
            started = datetime.strptime(started_str, "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.now() - started).total_seconds()
        except Exception:
            pass
            
    speed_mbs = (current_bytes / elapsed / (1024 * 1024)) if elapsed > 0 else 0
    
    remaining_bytes = max(total_bytes - current_bytes, 0)
    eta_raw_sec = int(remaining_bytes / (speed_mbs * 1024 * 1024)) if speed_mbs > 0 else 0
    
    if eta_raw_sec >= 60:
        eta_str = f"{eta_raw_sec // 60}m {eta_raw_sec % 60}s"
    else:
        eta_str = f"{eta_raw_sec}s"
        
    text = (
        f"Transfer Details\n\n"
        f"Provider: {provider}\n"
        f"User: @{username} ({telegram_id})\n"
        f"File: {file_name}\n"
        f"Status: {status}\n"
        f"Progress: {percent:.2f}% ({current_mb:.1f} MB / {total_mb:.1f} MB)\n"
        f"Speed: {speed_mbs:.1f} MB/s\n"
        f"ETA: {eta_str}\n"
        f"Time Elapsed: {int(elapsed)}s"
    )
    
    buttons = [
        [InlineKeyboardButton("Kill Transfer", callback_data=f"queue_kill:{bot_provider}:{tid}")],
        [InlineKeyboardButton("Refresh", callback_data=f"queue_details:{transfer_id_str}")],
        [InlineKeyboardButton("Back", callback_data="refresh_queue")]
    ]
    markup = InlineKeyboardMarkup(buttons)
    
    try:
        if q.message and q.message.text == text:
            return
        await q.edit_message_text(text, reply_markup=markup, parse_mode=None)
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error editing queue details message: {e}")
