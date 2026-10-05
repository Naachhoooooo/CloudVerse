"""
RemotePicker — UI for browsing and orchestrating rclone cloud-to-cloud transfers.

Flow:
1. handle_rclone_transfer_cmd (triggered by /transfer): Offers Source Remote Picker
2. handle_picker_callback:
     - Select Source Remote -> Browse Source Path
     - Confirm Source Path -> Select Destination Remote
     - Select Destination Remote -> Browse Destination Path
     - Confirm Destination Path -> Start Transfer Stream

Tags: [RCLONE][TRANSFER][UI]
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import asyncio
import humanize

from shared.core.Logger import get_logger
from shared.managers.AccessManager import access_required
from bots.rclone.services.ConfigHelper import user_config
from bots.rclone.services.RcloneService import list_remotes, list_files, copy_file_stream
from shared.utils.pagination import Paginator

logger = get_logger(__name__)

# State structure in ctx.user_data['rclone_picker']:
# {
#    'src_remote': 'gdrive:',
#    'src_path': 'foo/bar',
#    'dst_remote': 'mega:',
#    'dst_path': 'backup',
#    'step': 'src_remote' | 'src_path' | 'dst_remote' | 'dst_path'
# }

@access_required
async def handle_rclone_transfer_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Triggered by /transfer. Initiates the Source Remote picker."""
    if ctx.user_data is None:
         ctx.user_data = {}
    
    telegram_id = update.effective_user.id
    ctx.user_data['rclone_picker'] = {'step': 'src_remote', 'src_path': '', 'dst_path': ''}

    # Verify user has config
    credential_repo = ctx.bot_data.get('credential_repo')
    if not credential_repo:
        await update.message.reply_text("❌ Authentication subsystem error.")
        return

    creds = await credential_repo.get_full_credentials(str(telegram_id))
    if not creds or not creds.get("rclone_credential"):
        await update.message.reply_text("❌ No rclone config found. Use /login to upload your rclone.conf file.")
        return

    # Render Source RemotePicker
    await _render_remote_picker(update, ctx, "Source")

async def _render_remote_picker(update: Update, ctx: ContextTypes.DEFAULT_TYPE, mode: str):
    """Render the list of remotes for the given mode (Source or Destination)."""
    telegram_id = update.effective_user.id
    q = getattr(update, "callback_query", None)
    m = getattr(update, "message", None)
    
    async with user_config(ctx.bot_data.get('credential_repo'), str(telegram_id)) as config_path:
        if not config_path:
            text = "❌ Could not load your rclone configuration. Please register again via /login."
            if q:
                await q.edit_message_text(text)
            else:
                await m.reply_text(text)
            return
            
        try:
            remotes = await list_remotes(config_path)
            if not remotes:
                text = "❌ No remotes found in your rclone config."
                if q:
                    await q.edit_message_text(text)
                else:
                    await m.reply_text(text)
                return
                
            buttons = []
            for r in remotes:
                callback = f"rclone_pick_remote_src:{r}" if mode == "Source" else f"rclone_pick_remote_dst:{r}"
                buttons.append([InlineKeyboardButton(f"📁 {r}", callback_data=callback)])
                
            if mode == "Destination":
                buttons.append([InlineKeyboardButton("⬅️ Back to Source", callback_data="rclone_picker_back_src")])
                
            text = f"🌐 <b>Rclone Transfer Setup</b>\n\nSelect your <b>{mode} Remote</b>:"
            markup = InlineKeyboardMarkup(buttons)
            
            if q:
                await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            else:
                await m.reply_text(text, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
             logger.error(f"[RCLONE][UI] Failed to list remotes: {e}")
             text = "❌ Failed to fetch remotes. Is your rclone.conf valid?"
             if q:
                 await q.edit_message_text(text)
             else:
                 await m.reply_text(text)

async def _render_path_browser(update: Update, ctx: ContextTypes.DEFAULT_TYPE, mode: str, remote: str, path: str, page: int = 0):
    """Render the files/folders in a given remote/path for the user to pick."""
    q = update.callback_query
    telegram_id = q.from_user.id
    
    async with user_config(ctx.bot_data.get('credential_repo'), str(telegram_id)) as config_path:
        try:
            items = await list_files(config_path, remote, path)
            display_path = f"{remote}{path}" if path else remote
            
            # For Destination, maybe we want to allow picking the *current* folder
            buttons = []
            
            if mode == "Source":
                buttons.append([InlineKeyboardButton("✅ Set as Source", callback_data=f"rclone_confirm_src_path:{path}")])
            else:
                buttons.append([InlineKeyboardButton("✅ Set as Destination", callback_data=f"rclone_confirm_dst_path:{path}")])

            # Parent folder nav
            if path:
                parent = '/'.join(path.strip('/').split('/')[:-1])
                cb_parent = f"rclone_browse_src:{parent}" if mode == "Source" else f"rclone_browse_dst:{parent}"
                buttons.append([InlineKeyboardButton("⏮ Parent Folder", callback_data=cb_parent)])

            formatted_items = []
            for i in items:
                icon = "📁" if i.get("IsDir") else "📄"
                name = i["Name"]
                size_str = f" ({humanize.naturalsize(i.get('Size', 0))})" if not i.get("IsDir") else ""
                label = f"{icon} {name}{size_str}"
                
                # Selecting a folder opens it. Selecting a file (in Source mode) picks the file.
                item_path = f"{path}/{name}".strip('/') if path else name
                if i.get("IsDir"):
                    cb = f"rclone_browse_src:{item_path}" if mode == "Source" else f"rclone_browse_dst:{item_path}"
                else:
                    if mode == "Source":
                        cb = f"rclone_confirm_src_file:{item_path}"
                    else:
                        continue # can't pick a file as destination folder
                formatted_items.append((cb, label))

            # Paginate
            paginator = Paginator(formatted_items, page, 10)
            page_items = paginator.items
            nav_buttons = paginator.get_buttons(
                prev_callback=f"rclone_page_{mode.lower()}_prev",
                next_callback=f"rclone_page_{mode.lower()}_next"
            )
            
            for cb, label in page_items:
                buttons.append([InlineKeyboardButton(label, callback_data=cb)])
                
            if nav_buttons:
                buttons.append(nav_buttons)
                

            back_cb = "rclone_picker_start" if mode == "Source" else "rclone_confirm_src_path:back"
            buttons.append([InlineKeyboardButton(f"⬅️ Back to {mode} Remotes", callback_data=back_cb)])
            
            text = f"📂 <b>Browsing {mode}</b>\n\n<code>{display_path}</code>\n\nNavigate and select your target."
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
            
            picker = ctx.user_data.get('rclone_picker', {})
            picker[f"{mode.lower()}_browser_page"] = page
            
        except Exception as e:
             logger.error(f"Failed to browse {remote}{path}: {e}")
             await q.edit_message_text(f"❌ Failed to read directory: {e}")

@access_required
async def handle_picker_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Router for all interactive picker inline buttons."""
    q = update.callback_query
    data = q.data
    await q.answer()
    
    if "rclone_picker" not in ctx.user_data:
        ctx.user_data["rclone_picker"] = {}
    picker = ctx.user_data["rclone_picker"]
    
    if data == "rclone_picker_start" or data == "rclone_picker_back_src":
        picker['step'] = 'src_remote'
        await _render_remote_picker(update, ctx, "Source")
        
    elif data == "rclone_picker_back_dst":
        picker['step'] = 'dst_remote'
        await _render_remote_picker(update, ctx, "Destination")
        
    elif data.startswith("rclone_pick_remote_src:"):
        remote = data.split(":", 1)[1]
        picker['src_remote'] = remote
        picker['src_browse_path'] = ""
        picker['step'] = 'src_path'
        await _render_path_browser(update, ctx, "Source", remote, "")
        
    elif data.startswith("rclone_browse_src:"):
        path = data.split(":", 1)[1]
        picker['src_browse_path'] = path
        await _render_path_browser(update, ctx, "Source", picker.get('src_remote'), path)

    elif data.startswith("rclone_confirm_src_path:") or data.startswith("rclone_confirm_src_file:"):
        path_type = "folder" if "src_path" in data else "file"
        action_payload = data.split(":", 1)[1]
        
        if action_payload == "back":
             pass # just re-render dst remote picker
        else:
             picker['src_path'] = action_payload
             picker['src_type'] = path_type
             
        picker['step'] = 'dst_remote'
        await _render_remote_picker(update, ctx, "Destination")
        
    elif data.startswith("rclone_pick_remote_dst:"):
        remote = data.split(":", 1)[1]
        picker['dst_remote'] = remote
        picker['dst_browse_path'] = ""
        picker['step'] = 'dst_path'
        await _render_path_browser(update, ctx, "Destination", remote, "")
        
    elif data.startswith("rclone_browse_dst:"):
        path = data.split(":", 1)[1]
        picker['dst_browse_path'] = path
        await _render_path_browser(update, ctx, "Destination", picker.get('dst_remote'), path)

    elif data.startswith("rclone_confirm_dst_path:"):
        picker['dst_path'] = data.split(":", 1)[1]
        
        picker['step'] = 'confirm'
        picker['flags'] = {
            'mode': 'copy',
            'checksum': True
        }
        await _render_transfer_confirmation(update, ctx)

    elif data.startswith("rclone_page_"):
        parts = data.split("_")
        mode_str = parts[2]
        direction = parts[3]
        mode = "Source" if mode_str == "source" else "Destination"
        
        page_key = f"{mode.lower()}_browser_page"
        current_page = picker.get(page_key, 0)
        new_page = max(0, current_page - 1) if direction == "prev" else current_page + 1
        
        remote = picker.get(f"{mode.lower()[:3]}_remote")
        path = picker.get(f"{mode.lower()[:3]}_browse_path", "")
        await _render_path_browser(update, ctx, mode, remote, path, new_page)
        
    elif data == "rclone_start_transfer":
        src = f"{picker['src_remote']}{picker['src_path']}"
        dst = f"{picker['dst_remote']}{picker['dst_path']}"
        
        if picker.get('src_type') == 'file':
            # Rclone copy file to folder keeps the filename in the target folder auto-magically
            pass
            
        await _start_live_transfer(update, ctx, src, dst, picker.get('flags', {}))

async def _render_transfer_confirmation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = getattr(update, "callback_query", None)
    picker = ctx.user_data.get("rclone_picker", {})
    
    src = f"{picker.get('src_remote')}{picker.get('src_path')}"
    dst = f"{picker.get('dst_remote')}{picker.get('dst_path')}"
    
    import html
    from bots.rclone.services.RcloneService import get_size
    from bots.rclone.services.ConfigHelper import user_config
    import humanize
    
    telegram_id = q.from_user.id if q else None
    
    if q:
        await q.edit_message_text(
            f"⚙️ <b>Transfer Configuration</b>\n\n"
            f"<b>Source:</b> <code>{html.escape(src)}</code>\n"
            f"<b>Target:</b> <code>{html.escape(dst)}</code>\n\n"
            f"<i>Calculating total files and size...</i>",
            parse_mode="HTML"
        )
        
    total_files = "Unknown"
    total_size = "Unknown"
    
    if telegram_id:
        async with user_config(ctx.bot_data.get('credential_repo'), str(telegram_id)) as config_path:
            try:
                size_info = await get_size(config_path, src)
                total_files = size_info.get("count", "Unknown")
                size_bytes = size_info.get("bytes", 0)
                total_size = humanize.naturalsize(size_bytes, binary=True) if size_bytes else "0 B"
            except Exception as e:
                pass

    picker['total_files'] = total_files

    text = (
        f"⚙️ <b>Transfer Configuration</b>\n\n"
        f"<b>Source:</b> <code>{html.escape(src)}</code>\n"
        f"<b>Target:</b> <code>{html.escape(dst)}</code>\n\n"
        f"<b>Total Files:</b> {total_files}\n"
        f"<b>Total Size:</b> {total_size}\n"
    )
    
    buttons = [
        [InlineKeyboardButton("🚀 START TRANSFER", callback_data="rclone_start_transfer")],
        [InlineKeyboardButton("⬅️ Back to Destination", callback_data="rclone_picker_back_dst")]
    ]
    if q:
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def _start_live_transfer(update: Update, ctx: ContextTypes.DEFAULT_TYPE, src: str, dst: str, flags: dict):
    q = update.callback_query
    telegram_id = q.from_user.id
    picker = ctx.user_data.get("rclone_picker", {})
    total_files = picker.get("total_files", "Unknown")
    
    import html
    import asyncio
    msg = await q.edit_message_text(
        f"🚀 <b>Starting transfer</b>\n\n"
        f"<b>Source:</b> <code>{html.escape(src)}</code>\n"
        f"<b>Target:</b> <code>{html.escape(dst)}</code>\n\n"
        f"Initializing...", 
        parse_mode="HTML"
    )
    
    async with user_config(ctx.bot_data.get('credential_repo'), str(telegram_id)) as config_path:
        db_id = None
        transfer_repo = ctx.bot_data.get('transfer_repo')
        
        try:
            if transfer_repo:
                from datetime import datetime
                db_id = await transfer_repo.create(
                    telegram_id=str(telegram_id),
                    username=update.effective_user.username or "Unknown",
                    file_id=src,
                    file_name=dst,
                    file_type="rclone_transfer",
                    file_size=0, # Unknown upfront
                    status='uploading',
                    method='rclone',
                    transfer_source='rclone',
                    started=datetime.now().isoformat()
                )
                
            from bots.rclone.services.RcloneService import copy_file_stream
            stream = copy_file_stream(config_path, src, dst, flags)
            
            last_edit = 0
            last_db_check = 0
            start_time = asyncio.get_event_loop().time()
            last_transferred = "0 B"
            last_speed = "0 B/s"
            
            async for progress in stream:
                now = asyncio.get_event_loop().time()
                
                if progress.get('status') == 'completed':
                    duration = asyncio.get_event_loop().time() - start_time
                    if db_id and transfer_repo:
                        await transfer_repo.update_status(db_id, 'completed')
                        
                    await msg.edit_text(
                        f"✅ <b>Transfer Complete</b>\n\n"
                        f"<b>Source:</b> <code>{html.escape(src)}</code>\n"
                        f"<b>Target:</b> <code>{html.escape(dst)}</code>\n\n"
                        f"<b>Total Files Copied:</b> {total_files}\n"
                        f"<b>Total Size Transferred:</b> {last_transferred}\n\n"
                        f"<b>Average Speed:</b> {last_speed}\n"
                        f"<b>Time Taken:</b> {duration:.1f}s",
                        parse_mode="HTML"
                    )
                    break
                    
                if progress.get('status') == 'uploading':
                    last_transferred = progress.get('transferred', last_transferred)
                    last_speed = progress.get('speed', last_speed)
                    
                    if db_id and transfer_repo and (now - last_db_check > 5.0):
                        last_db_check = now
                        record = await transfer_repo.get(db_id)
                        if record and record.get('status') == 'cancelled':
                            raise RuntimeError("Cancelled by administrator")
                            
                    if now - last_edit > 2.0:
                        text = (
                            f"🔄 <b>Transfer In Progress</b>\n\n"
                            f"<b>Source:</b> <code>{html.escape(src)}</code>\n"
                            f"<b>Target:</b> <code>{html.escape(dst)}</code>\n\n"
                            f"📊 <b>Progress:</b> {progress['percent']}%\n"
                            f"📦 <b>Transferred:</b> {progress['transferred']}\n"
                            f"⚡ <b>Speed:</b> {progress['speed']}\n"
                            f"⏳ <b>ETA:</b> {progress['eta']}\n"
                        )
                        try:
                            await msg.edit_text(text, parse_mode="HTML")
                            last_edit = now
                        except Exception as e:
                            logger.debug(f"Failed to edit progress message: {e}")
                            
        except Exception as e:
             if db_id and transfer_repo:
                 await transfer_repo.update_status(db_id, 'failed', error_message=str(e))
                 
             if "Cancelled by administrator" in str(e):
                 logger.info(f"[RCLONE] Transfer was cancelled by administrator.")
                 try:
                     await msg.edit_text(f"❌ <b>Transfer Cancelled</b>\n\nBy Administrator.", parse_mode="HTML")
                 except Exception: pass
             else:
                 logger.error(f"[RCLONE][UI] Transfer failed: {e}", exc_info=True)
                 try:
                     await msg.edit_text(
                         f"❌ <b>Transfer Failed</b>\n\n"
                         f"<b>Source:</b> <code>{html.escape(src)}</code>\n"
                         f"<b>Target:</b> <code>{html.escape(dst)}</code>\n\n"
                         f"Error: {html.escape(str(e))}", 
                         parse_mode="HTML"
                     )
                 except Exception:
                     pass
