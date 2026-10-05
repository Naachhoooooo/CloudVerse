"""
TransferExecutor — handles the actual execution pipeline of file/URL transfers.

Decoupled from TransferTracker to ensure Single Responsibility Principle.
Executes the physical download, cloud upload, DB tracking, and UI progress editing.
"""
import asyncio
import os
import html
from time import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from shared.core.Logger import get_logger
from shared.managers.DomainManager import is_streaming_site, is_direct_file_url

logger = get_logger(__name__)


class TransferExecutor:
    
    @classmethod
    async def handle_file_transfer(cls, tracker, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
        """Handle complete file transfer from Telegram using centralized modules."""
        telegram_id = update.message.from_user.id if update.message and update.message.from_user else None
        if not telegram_id:
            return False
        
        msg = update.message
        file = msg.document or msg.video or msg.audio or (msg.photo[-1] if msg.photo else None)
        if not file:
            return False
            
        resolved_file_name = getattr(file, 'file_name', None)
        if not resolved_file_name:
            if msg.photo: resolved_file_name = f"photo_{int(time())}.jpg"
            elif msg.audio: resolved_file_name = f"audio_{int(time())}.mp3"
            elif msg.video: resolved_file_name = f"video_{int(time())}.mp4"
            else: resolved_file_name = "Unknown_File"
            
        file_size_bytes = getattr(file, 'file_size', 0) or 0
        MAX_FILE_SIZE = 4 * 1024 * 1024 * 1024  # 4GB limit for Telethon
        if file_size_bytes > MAX_FILE_SIZE:
            await update.message.reply_text("❌ File exceeds the 4GB maximum size limit.")
            return False
        
        if not await tracker.can_start_transfer(telegram_id):
            transfer_data = {
                'type': 'file',
                'file_info': {
                    'file_id': file.file_id,
                    'file_name': resolved_file_name,
                    'file_size': getattr(file, 'file_size', None),
                    'mime_type': getattr(file, 'mime_type', None)
                },
                'telegram_id': telegram_id,
                'username': update.message.from_user.username
            }
            return await cls._handle_queueing(tracker, update, telegram_id, transfer_data, ctx)
        
        if not await cls._check_quota(update, ctx, telegram_id):
            return False
        
        file_size_bytes = getattr(file, 'file_size', None)
        transfer_id = f"file_{telegram_id}_{int(time())}"
        file_name = resolved_file_name
        username = update.message.from_user.username or "Unknown"
        provider = ctx.bot_data.get('provider_name', 'drive')
        
        if not await tracker.start_transfer(telegram_id, transfer_id, file_name, file_size_bytes, username=username, provider=provider):
            return False
        
        temp_file_path = None
        try:
            from shared.managers.TransferManager import get_service_provider
            db = ctx.bot_data.get('credential_repo')

            file_size_str = f"{file_size_bytes/1024/1024/1024:.2f} GB" if file_size_bytes and file_size_bytes > 1024*1024*1024 else (f"{file_size_bytes/1024/1024:.2f} MB" if file_size_bytes else "Unknown")
            processing_text = "Processing..."
            preparing_message = await update.message.reply_text(
                processing_text, 
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id
            )
            download_manager = get_service_provider().get_download_manager()
            
            import os
            
            message_id = update.message.message_id
            chat_id = update.message.chat_id
            
            local_chat_id_str = os.getenv("LOCAL_CHAT_ID")
            forwarded_msg = None
            if local_chat_id_str:
                try:
                    local_chat_id = int(local_chat_id_str)
                    forwarded_msg = await update.message.forward(chat_id=local_chat_id)
                    chat_id = local_chat_id
                    message_id = forwarded_msg.message_id
                    logger.info(f"Forwarded file to Local Chat {local_chat_id} (msg: {message_id})")
                except Exception as e:
                    logger.warning(f"Failed to forward large file to Local Chat: {e}")
            
            # Phase 1: Download from Telegram using Telethon (or native for small files)
            import html
            display_name = file_name if len(file_name) <= 45 else file_name[:42] + "..."
            download_init_text = (
                "⚡️ <b>Preparing Download...</b>\n\n"
                f"<b>File:</b> <code>{html.escape(display_name)}</code>\n"
                f"<b>Size:</b> <code>{file_size_str}</code>"
            )
            await preparing_message.edit_text(download_init_text, parse_mode="HTML")
            
            # Create DB record at the start
            db_id = ctx.user_data.pop('queued_db_id', None) if ctx and ctx.user_data else None
            transfer_repo = ctx.bot_data.get('transfer_repo')
            if transfer_repo:
                if db_id:
                    await transfer_repo.update_status(db_id, 'downloading')
                else:
                    db_id = await cls._create_transfer_record(
                        ctx, telegram_id, username,
                        getattr(file, 'file_id', None), file_name, getattr(file, 'mime_type', None),
                        file_size_bytes, 'file', 'telegram', 'downloading'
                    )

            temp_file_path = await cls._with_retry(
                cls._execute_telegram_download,
                tracker, transfer_id, download_manager, telegram_id, message_id, chat_id, 
                file_size_bytes, file_name, preparing_message, ctx, getattr(file, 'file_id', None), db_id
            )
            
            if forwarded_msg:
                try:
                    await forwarded_msg.delete()
                except Exception as e:
                    logger.debug(f"Failed to delete forwarded message from Dump Channel: {e}")

            # Phase 2: Upload to Cloud Provider
            upload_manager = get_service_provider().get_upload_manager()
            provider = ctx.bot_data.get('provider_name', 'drive')
            provider_display = 'Google Drive' if provider == 'drive' else 'Mega' if provider == 'mega' else 'Cloud Storage'
            
            parent_id = await db.get_default_location(str(telegram_id))

            import html
            display_name = file_name if len(file_name) <= 45 else file_name[:42] + "..."
            upload_init_text = (
                "☁️ <b>Preparing Upload...</b>\n\n"
                f"<b>File:</b> <code>{html.escape(display_name)}</code>\n"
                f"<b>Size:</b> <code>{file_size_str}</code>"
            )
            await preparing_message.edit_text(upload_init_text, parse_mode="HTML")

            upload_result = await cls._with_retry(
                cls._execute_cloud_upload,
                tracker, transfer_id, upload_manager, temp_file_path, file_name,
                telegram_id, parent_id, getattr(file, 'mime_type', None), preparing_message, provider_display, ctx, db_id
            )
            
            await cls._send_completion_message(
                ctx, preparing_message, upload_result.get('file_id'),
                upload_result['file_name'], provider_display, file_size_bytes
            )
            
            if transfer_repo and db_id:
                await transfer_repo.update_status(db_id, 'completed')
            
            from shared.managers.QuotaManager import get_quota_manager
            await get_quota_manager().consume_quota(str(telegram_id), file_size_bytes)
            await tracker.finish_transfer(transfer_id, success=True)
            return True
            
        except Exception as e:
            if "Cancelled by administrator" in str(e):
                logger.info(f"File transfer {transfer_id} was cancelled by administrator.")
                await update.message.reply_text(f"❌ Transfer cancelled by administrator.")
            else:
                logger.error(f"File transfer failed: {e}")
                import re
                error_str = re.sub(r"^\[.*?\]\s*", "", str(e))
                msg = (
                    "❌ <b>Transfer Failed</b>\n\n"
                    "We encountered an issue while processing your transfer request. "
                    "The process has been safely halted.\n\n"
                    f"<b>Reason:</b> <code>{error_str}</code>\n\n"
                    "<i>If this issue persists, please use the /support command to contact our team.</i>"
                )
                await update.message.reply_text(msg, parse_mode="HTML")
            if 'db_id' in locals() and db_id and ctx.bot_data.get('transfer_repo'):
                await ctx.bot_data['transfer_repo'].update_status(db_id, 'failed', error_message=str(e))
                
            await tracker.finish_transfer(transfer_id, success=False)
            return False
        finally:
            cls._cleanup_temp_file(temp_file_path)


    @classmethod
    async def handle_url_transfer(cls, tracker, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
        """Handle complete URL transfer using centralized modules."""
        url = update.message.text if update.message and update.message.text else None
        if not url:
            return False
        
        if not is_streaming_site(url) and not is_direct_file_url(url):
            await update.message.reply_text(
                "❌ **URL Not Allowed**\n\n"
                "This is either an restricted domain or is not a direct file link.\n\n"
                "Contact **Team CloudVerse** for assistance.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Contact Team CloudVerse", callback_data=f"contact_team:{update.message.from_user.id}")]])
            )
            return False
        
        telegram_id = update.message.from_user.id if update.message and update.message.from_user else None
        if not telegram_id:
            return False
        
        if not await tracker.can_start_transfer(telegram_id):
            transfer_data = {
                'type': 'url',
                'url': url,
                'telegram_id': telegram_id,
                'username': update.message.from_user.username
            }
            return await cls._handle_queueing(tracker, update, telegram_id, transfer_data, ctx)
        
        if not await cls._check_quota(update, ctx, telegram_id):
            return False
        
        transfer_id = f"url_{telegram_id}_{int(time())}"
        file_name = os.path.basename(url) or "Downloaded File"
        
        if not await tracker.start_transfer(telegram_id, transfer_id, file_name, None):
            return False
        
        temp_file_path = None
        try:
            from shared.managers.TransferManager import get_service_provider

            preparing_message = await update.message.reply_text('Preparing download...')
            download_manager = get_service_provider().get_download_manager()
            
            # Create DB record at the start
            db_id = ctx.user_data.pop('queued_db_id', None) if ctx and ctx.user_data else None
            transfer_repo = ctx.bot_data.get('transfer_repo')
            if transfer_repo:
                if db_id:
                    await transfer_repo.update_status(db_id, 'downloading')
                else:
                    db_id = await cls._create_transfer_record(
                        ctx, telegram_id, update.message.from_user.username or "Unknown",
                        None, file_name, "application/octet-stream",
                        None, 'url', url, 'downloading'
                    )
            
            # Phase 1: Download from URL
            temp_file_path = await cls._with_retry(
                cls._execute_url_download,
                tracker, transfer_id, download_manager, url, preparing_message, ctx, db_id
            )
            
            file_name = os.path.basename(temp_file_path)
            file_size_bytes = os.path.getsize(temp_file_path)
            
            # Phase 2: Upload to Cloud
            upload_manager = get_service_provider().get_upload_manager()
            provider = ctx.bot_data.get('provider_name', 'drive')
            provider_display = 'Google Drive' if provider == 'drive' else 'Mega.nz' if provider == 'mega' else 'Cloud Storage'

            credential_repo = ctx.bot_data.get('credential_repo')
            parent_id = await credential_repo.get_default_location(telegram_id=str(telegram_id)) if credential_repo else 'root'

            upload_result = await cls._with_retry(
                cls._execute_cloud_upload,
                tracker, transfer_id, upload_manager, temp_file_path, file_name,
                telegram_id, parent_id, "application/octet-stream", preparing_message, provider_display, ctx, db_id
            )
            
            await cls._send_completion_message(
                ctx, preparing_message, upload_result.get('file_id'),
                upload_result['file_name'], provider_display, file_size_bytes
            )
            
            if transfer_repo and db_id:
                await transfer_repo.update_status(db_id, 'completed', bytes_transferred=file_size_bytes)
            
            from shared.managers.QuotaManager import get_quota_manager
            await get_quota_manager().consume_quota(str(telegram_id), file_size_bytes)
            await tracker.finish_transfer(transfer_id, success=True)
            return True
            
        except Exception as e:
            if "Cancelled by administrator" in str(e):
                logger.info(f"URL transfer {transfer_id} was cancelled by administrator.")
                await update.message.reply_text(f"❌ Transfer cancelled by administrator.")
            else:
                logger.error(f"URL transfer failed: {e}")
                import re
                error_str = re.sub(r"^\[.*?\]\s*", "", str(e))
                msg = (
                    "❌ <b>Transfer Failed</b>\n\n"
                    "We encountered an issue while processing your transfer request. "
                    "The process has been safely halted.\n\n"
                    f"<b>Reason:</b> <code>{error_str}</code>\n\n"
                    "<i>If this issue persists, please use the /support command to contact our team.</i>"
                )
                await update.message.reply_text(msg, parse_mode="HTML")
            if 'db_id' in locals() and db_id and ctx.bot_data.get('transfer_repo'):
                await ctx.bot_data['transfer_repo'].update_status(db_id, 'failed', error_message=str(e))
                
            await tracker.finish_transfer(transfer_id, success=False)
            return False
        finally:
            cls._cleanup_temp_file(temp_file_path)

    # ── Helper Sub-routines ───────────────────────────────────────────────────

    @classmethod
    async def _with_retry(cls, func, *args, max_attempts=3, **kwargs):
        """Execute a function with exponential backoff retries."""
        for attempt in range(1, max_attempts + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if attempt == max_attempts:
                    logger.error(f"[TRANSFER] Network Operation {func.__name__} failed after {max_attempts} attempts: {e}")
                    raise
                wait_time = 5 * (2 ** (attempt - 1))  # 5s, 10s, 20s
                logger.warning(f"[TRANSFER] Network Operation {func.__name__} failed (attempt {attempt}/{max_attempts}): {e}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)


    @classmethod
    async def _execute_telegram_download(cls, tracker, transfer_id, download_manager, telegram_id, message_id, chat_id, file_size, file_name, preparing_message, ctx=None, file_id=None, db_id=None):
        dl_start = datetime.now()
        bot_uname = f"@{ctx.bot.username}" if ctx and ctx.bot.username else "@CloudVerseBot"
        transfer_repo = ctx.bot_data.get('transfer_repo') if ctx else None
        
        last_db_check = [0]
        
        async def tg_progress(downloaded, total):
            now = time()
            if db_id and transfer_repo and (now - last_db_check[0] > 5):
                last_db_check[0] = now
                record = await transfer_repo.get(db_id)
                if record and record.get('status') == 'cancelled':
                    raise Exception("Cancelled by administrator")
                    
            await tracker.update_transfer_progress(
                transfer_id, downloaded, total,
                "Downloading",
                lambda text, **kwargs: preparing_message.edit_text(text, parse_mode="HTML", **kwargs),
                phase_start_time=dl_start,
                bot_username=bot_uname
            )

        return await download_manager.download_from_telegram(
            telegram_id=telegram_id,
            message_id=message_id,
            chat_id=chat_id,
            file_size=file_size,
            file_name=file_name,
            progress_callback=tg_progress,
            download_id=f"tg_{transfer_id}"
        )

    @classmethod
    async def _execute_url_download(cls, tracker, transfer_id, download_manager, url, preparing_message, ctx=None, db_id=None):
        transfer_repo = ctx.bot_data.get('transfer_repo') if ctx else None
        last_db_check = [0]
        
        async def http_progress(downloaded, total):
            now = time()
            if db_id and transfer_repo and (now - last_db_check[0] > 5):
                last_db_check[0] = now
                record = await transfer_repo.get(db_id)
                if record and record.get('status') == 'cancelled':
                    raise Exception("Cancelled by administrator")
                    
            await tracker.update_transfer_progress(
                transfer_id, downloaded, total,
                "Downloading",
                lambda text, **kwargs: preparing_message.edit_text(text, parse_mode="HTML", **kwargs),
            )

        if is_streaming_site(url):
            return await download_manager.download_with_ytdlp(
                url, 
                None,
                f"ytdlp_{transfer_id}"
            )
        else:
            return await download_manager.download_http_to_tempfile(
                url, 
                http_progress,
                download_id=f"http_{transfer_id}"
            )

    @classmethod
    async def _execute_cloud_upload(cls, tracker, transfer_id, upload_manager, temp_file_path, file_name, 
                                    telegram_id, parent_id, mime_type, preparing_message, provider_display, ctx, db_id=None):
        await preparing_message.edit_text(
            f"<b>Uploading to {provider_display}…</b>"
        )
        ul_start = datetime.now()
        bot_uname = f"@{ctx.bot.username}" if ctx and ctx.bot.username else "@CloudVerseBot"
        transfer_repo = ctx.bot_data.get('transfer_repo') if ctx else None
        
        if transfer_repo and db_id:
            await transfer_repo.update_status(db_id, 'uploading')
            
        last_db_check = [0]
        
        async def up_progress(uploaded, total):
            now = time()
            if db_id and transfer_repo and (now - last_db_check[0] > 5):
                last_db_check[0] = now
                record = await transfer_repo.get(db_id)
                if record and record.get('status') == 'cancelled':
                    raise Exception("Cancelled by administrator")
                    
            await tracker.update_transfer_progress(
                transfer_id, uploaded, total,
                "Uploading",
                lambda text, **kwargs: preparing_message.edit_text(text, parse_mode="HTML", **kwargs),
                phase_start_time=ul_start,
                bot_username=bot_uname
            )

        return await upload_manager.upload_file_to_drive(
            temp_file_path,
            file_name,
            telegram_id,
            parent_id=parent_id,
            mime_type=mime_type,
            progress_callback=up_progress,
            upload_id=f"up_{transfer_id}",
            ctx=ctx
        )

    @classmethod
    def _cleanup_temp_file(cls, temp_file_path: str):
        if temp_file_path:
            try:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
            except Exception as e:
                logger.debug(f"Failed to cleanup temp file: {e}")

    @classmethod
    async def _handle_queueing(cls, tracker, update: Update, telegram_id: int, transfer_data: dict, ctx: ContextTypes.DEFAULT_TYPE = None) -> bool:
        # Create a queued record in the database
        db_id = None
        if ctx and ctx.bot_data.get('transfer_repo'):
            try:
                db_id = await cls._create_transfer_record(
                    ctx, telegram_id, update.message.from_user.username or "Unknown",
                    transfer_data.get('file_info', {}).get('file_id') if transfer_data['type'] == 'file' else None,
                    transfer_data.get('file_info', {}).get('file_name') if transfer_data['type'] == 'file' else os.path.basename(transfer_data.get('url', 'Unknown')),
                    transfer_data.get('file_info', {}).get('mime_type') if transfer_data['type'] == 'file' else "application/octet-stream",
                    transfer_data.get('file_info', {}).get('file_size') if transfer_data['type'] == 'file' else None,
                    transfer_data['type'],
                    transfer_data.get('url') if transfer_data['type'] == 'url' else 'telegram',
                    'queued'
                )
                if db_id:
                    transfer_data['db_id'] = db_id
            except Exception as e:
                logger.warning(f"Failed to record queued transfer in db: {e}")

        queue_info = await tracker.register_waiting(telegram_id, transfer_data)
        position = queue_info.get('your_position', 'unknown')
        lane = queue_info.get('lane', 'public')
        eta_minutes = queue_info.get('eta_seconds', 0) / 60
        
        lane_display = "Public Lane" if lane == "public" else "Private Lane"
        text = (f"🕒 Transfer queued in {lane_display}\n\n"
                f"📍 Position: {position}\n"
                f"⏳ Estimated wait: {eta_minutes:.1f} minutes\n\n")
        
        if transfer_data['type'] == 'url':
            text += "Your download will start automatically when a slot becomes available."
        else:
            text += "Your upload will start automatically when a slot becomes available."
            
        await update.message.reply_text(text)
        return False

    @classmethod
    async def _check_quota(cls, update: Update, ctx: ContextTypes.DEFAULT_TYPE, telegram_id: int) -> bool:
        from shared.managers.QuotaManager import get_quota_manager
        quota_manager = get_quota_manager()
        
        is_admin = False
        if ctx.bot_data.get('account_repo'):
            is_admin = await ctx.bot_data['account_repo'].is_super_admin(str(telegram_id)) or await ctx.bot_data['account_repo'].is_admin(str(telegram_id))
            
        allowed, msg = await quota_manager.check_quota(str(telegram_id), is_admin)
        if not allowed:
            await update.message.reply_text(f"❌ {msg}")
            return False
        return True

    @classmethod
    async def _create_transfer_record(cls, ctx: ContextTypes.DEFAULT_TYPE, telegram_id: int, username: str, 
                               file_id: str, file_name: str, file_type: str, file_size: int, 
                               method: str, transfer_source: str, status: str) -> int:
        transfer_repo = ctx.bot_data.get('transfer_repo')
        if transfer_repo:
            try:
                return await transfer_repo.create(
                    telegram_id=str(telegram_id),
                    username=username,
                    file_id=file_id,
                    file_name=file_name,
                    file_type=file_type,
                    file_size=file_size,
                    status=status,
                    method=method,
                    transfer_source=transfer_source,
                    started=datetime.now().isoformat()
                )
            except Exception as db_err:
                logger.warning(f"Failed to record transfer in bot database: {db_err}")
        return 0

    @classmethod
    async def _send_completion_message(cls, ctx: ContextTypes.DEFAULT_TYPE, preparing_message, file_id: str, file_name: str, provider_display: str, file_size: int = None):
        from shared.core.CallbackDataCache import shorten_id
        short_id = shorten_id(ctx, file_id)
        file_size_str = f"{file_size/1024/1024/1024:.2f} GB" if file_size and file_size > 1024*1024*1024 else (f"{file_size/1024/1024:.2f} MB" if file_size else "Unknown")
        buttons = [
            [
                InlineKeyboardButton("🔗 Link", callback_data=f"transfer_link:{short_id}"),
                InlineKeyboardButton("✘ Delete", callback_data=f"transfer_delete:{short_id}")
            ]
        ]
        import html
        await preparing_message.edit_text(
            f"<b>File:</b> {html.escape(file_name)}\n"
            f"<b>Size:</b> {file_size_str}\n\n"
            f"<b>File Successfully Uploaded to Your {provider_display}</b>",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )
