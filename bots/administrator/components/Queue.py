"""Queue component for administrator bot."""

import math
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.managers.AccessManager import admin_required
from shared.managers.TransferManager.TransferTracker import get_transfer_tracker

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
        transfer_id = data.split(":")[1]
        tracker = get_transfer_tracker()
        await tracker.cancel_transfer(transfer_id)
        page = ctx.user_data.get('queue_page', 0)
        await _render_queue_page(update, ctx, page=page, is_callback=True)

async def _render_queue_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE, page: int, is_callback: bool = False):
    q = getattr(update, "callback_query", None)
    m = q.message if q else update.message

    tracker = get_transfer_tracker()
    transfers = tracker._state._transfer_info
    
    # Calculate stats
    total_active = len(transfers)
    load_factor = tracker._calculate_system_load_factor()
    
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
        
        # Sort transfers by start_time (newest first)
        sorted_transfers = sorted(
            transfers.items(), 
            key=lambda item: item[1].get('start_time', datetime.min), 
            reverse=True
        )
        
        total_pages = math.ceil(total_active / TRANSFERS_PER_PAGE)
        if page >= total_pages:
            page = max(0, total_pages - 1)
            
        start_idx = page * TRANSFERS_PER_PAGE
        end_idx = start_idx + TRANSFERS_PER_PAGE
        page_transfers = sorted_transfers[start_idx:end_idx]
        
        buttons = []
        for tid, info in page_transfers:
            username = info.get("username", "Unknown")
            provider = info.get("provider", "Unknown").capitalize()
            
            current_bytes = info.get("progress", 0)
            total_bytes = info.get("file_size") or 0
            
            percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
            current_mb = current_bytes / (1024 * 1024)
            total_mb = total_bytes / (1024 * 1024)
            
            btn_text = f"[{provider}] @{username} | {percent:.1f}% ({current_mb:.1f}/{total_mb:.1f} MB)"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"queue_details:{tid}")])
            
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


async def _render_queue_details(update: Update, ctx: ContextTypes.DEFAULT_TYPE, transfer_id: str):
    q = update.callback_query
    if not q:
        return
        
    tracker = get_transfer_tracker()
    info = tracker._state.get_transfer_info(transfer_id)
    
    if not info:
        # Transfer might have finished or been killed
        page = ctx.user_data.get('queue_page', 0)
        await _render_queue_page(update, ctx, page=page, is_callback=True)
        return
        
    username = info.get("username", "Unknown")
    telegram_id = info.get("telegram_id", "Unknown")
    provider = info.get("provider", "Unknown").capitalize()
    file_name = info.get("file_name", "unknown")
    
    current_bytes = info.get("progress", 0)
    total_bytes = info.get("file_size") or 0
    
    percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
    current_mb = current_bytes / (1024 * 1024)
    total_mb = total_bytes / (1024 * 1024)
    
    start_time = info.get("start_time", datetime.now())
    elapsed = (datetime.now() - start_time).total_seconds()
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
        f"Progress: {percent:.2f}% ({current_mb:.1f} MB / {total_mb:.1f} MB)\n"
        f"Speed: {speed_mbs:.1f} MB/s\n"
        f"ETA: {eta_str}\n"
        f"Time Elapsed: {int(elapsed)}s"
    )
    
    buttons = [
        [InlineKeyboardButton("Kill Transfer", callback_data=f"queue_kill:{transfer_id}")],
        [InlineKeyboardButton("Refresh", callback_data=f"queue_details:{transfer_id}")],
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
