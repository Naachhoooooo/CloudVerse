"""
TransferHandlers — Telegram UI callback/command handlers for transfer actions.

Handles link display, file deletion, delete confirmation, back navigation,
queue status display, cancel upload, and notify-on-completion toggle.
"""
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors

from .TransferTracker import get_transfer_tracker
from . import get_service_provider

logger = get_logger(__name__)



@handle_errors
async def cancel_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cancel active uploads for the user."""
    telegram_id = update.effective_user.id if update.effective_user else None
    if not telegram_id:
        return
    
    tracker = get_transfer_tracker()
    user_transfers = await tracker.get_user_transfers(telegram_id)
    
    if not user_transfers:
        if update.callback_query:
            await update.callback_query.answer("No active uploads to cancel.")
        return
    
    cancelled_count = 0
    for transfer_id in list(user_transfers.keys()):
        await tracker.cancel_transfer(transfer_id)
        cancelled_count += 1
    
    upload_manager = get_service_provider().get_upload_manager()
    active_uploads = upload_manager.get_active_uploads()
    for upload_id, upload_info in active_uploads.items():
        if upload_info.get('telegram_id') == telegram_id:
            await upload_manager.cancel_upload(upload_id)
    
    download_manager = get_service_provider().get_download_manager()
    active_downloads = download_manager.get_active_downloads()
    for download_id, download_info in active_downloads.items():
        if download_info.get('telegram_id') == telegram_id:
            await download_manager.cancel_download(download_id)
    
    message = f"? Cancelled {cancelled_count} active transfer(s)."
    
    if update.callback_query:
        await update.callback_query.answer(message)
        try:
            await update.callback_query.edit_message_text(message)
        except Exception as e:
            logger.debug(f"Failed to edit message: {e}")
    elif update.message:
        await update.message.reply_text(message)




@handle_errors
async def handle_transfer_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle transfer link button click - show file link with back button"""
    q = update.callback_query
    if not q or not q.data:
        return
    await q.answer()
    
    from shared.components.FileManager import extract_id
    file_id = extract_id(ctx, q.data, "transfer_link:")
    telegram_id = q.from_user.id if q.from_user else None
    
    if not telegram_id:
        await q.answer("? User information not found.")
        return
    
    try:
        provider = ctx.bot_data.get('provider_name', 'drive')
        provider_display = 'Google Drive' if provider == 'drive' else 'Mega.nz' if provider == 'mega' else 'Cloud Storage'
        
        service = await get_service_provider().get_drive_service(telegram_id)
        if not service:
            await q.answer(f"❓ Please login to {provider_display} first.")
            return
        
        file_link = await get_service_provider().get_file_link(service, file_id)
        if not file_link:
            await q.answer("? Could not retrieve file link.")
            return
        
        try:
            file_metadata = await get_service_provider().get_file_metadata(service, file_id)
            file_name = file_metadata.get('name', 'Unknown File') if file_metadata else 'Unknown File'
        except Exception as e:
            logger.debug(f"Could not fetch file metadata for link display (file_id={file_id}): {e}")
            file_name = 'Unknown File'
        
        buttons = [
            [InlineKeyboardButton("Back", callback_data=f"transfer_back:{file_id}")]
        ]
        
        
        import html
        safe_file_name = html.escape(file_name)
        await q.edit_message_text(
            f"<code>{file_link}</code>\n\n"
            f"👆 <i>Tap the link above to copy it.</i>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logger.error(f"Error handling transfer link: {e}")
        await q.answer("? Failed to retrieve file link.")


@handle_errors
async def handle_transfer_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle transfer delete button click - show confirmation with cancel option"""
    q = update.callback_query
    if not q or not q.data:
        return
    await q.answer()
    
    from shared.components.FileManager import extract_id
    file_id = extract_id(ctx, q.data, "transfer_delete:")
    telegram_id = q.from_user.id if q.from_user else None
    
    if not telegram_id:
        await q.answer("? User information not found.")
        return
    
    try:
        provider = ctx.bot_data.get('provider_name', 'drive')
        provider_display = 'Google Drive' if provider == 'drive' else 'Mega.nz' if provider == 'mega' else 'Cloud Storage'
        
        service = await get_service_provider().get_drive_service(telegram_id)
        if not service:
            await q.answer(f"❓ Please login to {provider_display} first.")
            return
        
        try:
            file_metadata = await get_service_provider().get_file_metadata(service, file_id)
            file_name = file_metadata.get('name', 'Unknown File') if file_metadata else 'Unknown File'
        except Exception as e:
            logger.debug(f"Could not fetch file metadata for delete confirmation (file_id={file_id}): {e}")
            file_name = 'Unknown File'
        
        buttons = [
            [InlineKeyboardButton("🗑️ Yes, Delete", callback_data=f"transfer_confirm_delete:{file_id}"),
             InlineKeyboardButton("❌ Cancel", callback_data=f"transfer_back:{file_id}")]
        ]
        
        
        import html
        safe_file_name = html.escape(file_name)
        await q.edit_message_text(
            f"🗑️ <b>Delete Confirmation</b>\n\n"
            f"📄 <b>File:</b> {safe_file_name}\n\n"
            f"Are you sure you want to delete this file?\n"
            f"This action will move the file to {provider_display}'s Recycle Bin.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logger.error(f"Error handling transfer delete: {e}")
        await q.answer("? Failed to prepare delete confirmation.")


@handle_errors
async def handle_transfer_confirm_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle confirmed file deletion"""
    q = update.callback_query
    if not q or not q.data:
        return
    await q.answer()
    
    from shared.components.FileManager import extract_id
    file_id = extract_id(ctx, q.data, "transfer_confirm_delete:")
    telegram_id = q.from_user.id if q.from_user else None
    
    if not telegram_id:
        await q.answer("? User information not found.")
        return
    
    try:
        provider = ctx.bot_data.get('provider_name', 'drive')
        provider_display = 'Google Drive' if provider == 'drive' else 'Mega.nz' if provider == 'mega' else 'Cloud Storage'
        
        service = await get_service_provider().get_drive_service(telegram_id)
        if not service:
            await q.answer(f"❓ Please login to {provider_display} first.")
            return
        
        try:
            file_metadata = await get_service_provider().get_file_metadata(service, file_id)
            file_name = file_metadata.get('name', 'Unknown File') if file_metadata else 'Unknown File'
        except Exception as e:
            logger.debug(f"Could not fetch file name before deletion (file_id={file_id}): {e}")
            file_name = 'Unknown File'
        
        await get_service_provider().delete_file(service, file_id)
        
        
        import html
        safe_file_name = html.escape(file_name)
        await q.edit_message_text(
            f"✅ <b>File Deleted</b>\n\n"
            f"📄 <b>File:</b> {safe_file_name}\n\n"
            f"The file has been moved to {provider_display}'s Recycle Bin.\n"
            f"You can restore it from the Recycle Bin if needed.",
            parse_mode='HTML'
        )
        
        await q.answer("? File deleted successfully.")
        
    except Exception as e:
        logger.error(f"Error confirming transfer delete: {e}")
        await q.answer("? Failed to delete file.")


@handle_errors
async def handle_transfer_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle back button - return to original completion message with buttons"""
    q = update.callback_query
    if not q or not q.data:
        return
    await q.answer()
    
    from shared.components.FileManager import extract_id
    file_id = extract_id(ctx, q.data, "transfer_back:")
    
    try:
        telegram_id = q.from_user.id if q.from_user else None
        if telegram_id:
            service = await get_service_provider().get_drive_service(telegram_id)
            if service:
                try:
                    file_metadata = await get_service_provider().get_file_metadata(service, file_id)
                    file_name = file_metadata.get('name', 'Unknown File') if file_metadata else 'Unknown File'
                except Exception as e:
                    logger.debug(f"Could not fetch file metadata for back button display (file_id={file_id}): {e}")
                    file_name = 'Unknown File'
            else:
                file_name = 'Unknown File'
        else:
            file_name = 'Unknown File'
        
        buttons = [
            [InlineKeyboardButton("🔗 Link", callback_data=f"transfer_link:{file_id}"),
             InlineKeyboardButton("🗑️ Delete", callback_data=f"transfer_delete:{file_id}")]
        ]
        
        provider = ctx.bot_data.get('provider_name', 'drive')
        provider_display = 'Google Drive' if provider == 'drive' else 'Mega.nz' if provider == 'mega' else 'Cloud Storage'
        
        await q.edit_message_text(
            f"? Upload complete! {file_name} is now in your {provider_display}.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    except Exception as e:
        logger.error(f"Error handling transfer back: {e}")
        await q.answer("❌ Failed to return to previous view.")

def register_handlers(app):
    from telegram.ext import CommandHandler, CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(cancel_upload, pattern=r"^cancel_upload$"))
    app.add_handler(CallbackQueryHandler(handle_transfer_link, pattern=r"^transfer_link:.*$"))
    app.add_handler(CallbackQueryHandler(handle_transfer_delete, pattern=r"^transfer_delete:.*$"))
    app.add_handler(CallbackQueryHandler(handle_transfer_confirm_delete, pattern=r"^transfer_confirm_delete:.*$"))
    app.add_handler(CallbackQueryHandler(handle_transfer_back, pattern=r"^transfer_back:.*$"))
