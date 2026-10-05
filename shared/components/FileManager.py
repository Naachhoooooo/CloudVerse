from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import humanize
import asyncio

from shared.utils.breadcrumb_utils import get_breadcrumb
from shared.core.CallbackDataCache import shorten_id, resolve_id
from shared.utils.emoji_maker import emoji_maker

from shared.utils.pagination import Paginator
from shared.core.CacheUtils import fm_cache
from shared.core.UserState import UserStateEnum, UserState
from shared.core.Logger import get_logger
from shared.managers.AccessManager import access_required
from shared.core.ErrorHandler import handle_errors
from shared.core.CacheUtils import invalidate_folder_cache

_fetch_locks = {}

logger = get_logger(__name__)

def sanitize_id(item_id: str) -> str:
    """Validate and sanitize a cloud provider file/folder ID. Rejects path traversal."""
    if not isinstance(item_id, str) or not item_id:
        return ""
    if ".." in item_id or item_id.startswith("/") or item_id.startswith("\\"):
        raise ValueError("Path traversal or invalid format detected in ID")
    return item_id


def sanitize_name(name: str) -> str:
    """Validate and sanitize a user-typed folder/file name.
    Allows spaces and most printable characters. Rejects path separators and traversal.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Name cannot be empty")
    forbidden = ["/", "\\", ".."]
    for char in forbidden:
        if char in name:
            raise ValueError(f"Name contains forbidden character: {char!r}")
    return name.strip()


def extract_id(ctx, data: str, prefix: str) -> str:
    raw_id = data.split(prefix, 1)[1]
    resolved = resolve_id(ctx, raw_id)
    return sanitize_id(resolved)



@access_required
async def handle_file_manager(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if ctx.user_data is None:
            ctx.user_data = {}
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        m = update.message if hasattr(update, 'message') and update.message else None
        telegram_id = None
        if q and q.from_user:
            telegram_id = q.from_user.id
        elif m and m.from_user:
            telegram_id = m.from_user.id

        elif m and m.from_user:
            telegram_id = m.from_user.id
        else:
            return
        if "account_data" not in ctx.user_data or ctx.user_data["account_data"] is None:
            ctx.user_data["account_data"] = {}
        current_account = ctx.user_data.get("current_account") or "default_account"
        account_data = ctx.user_data["account_data"].setdefault(current_account, {
            "current_folder": "root", "folder_stack": [], "folder_pages": {}
        })

        # Reset to root if opened via /filemanager command or main menu button
        if (m and m.text and m.text.startswith("/filemanager")) or (q and q.data == "FILE_MGR"):
            account_data["current_folder"] = "root"
            account_data["folder_stack"] = []
            ctx.user_data.pop("in_def_location", None)
        if "folder_pages" not in account_data or account_data["folder_pages"] is None:
            account_data["folder_pages"] = {}
        current_folder = account_data["current_folder"]
        import time
        account_data["last_accessed"] = time.time()
        
        provider = ctx.bot_data['provider']
        service = await provider.get_service(telegram_id, current_account)
        
        if not service:
            # User has no authenticated session with this cloud provider
            msg = (
                "⚠️ <b>Please login first.</b>\n\n"
                "Use /login to connect your account before accessing the File Manager."
            )
            if q and hasattr(q, 'edit_message_text'):
                await q.edit_message_text(msg, parse_mode="HTML")
            elif m:
                await m.reply_text(msg, parse_mode="HTML")
            return

        cache_key = f"list_files_{provider.__class__.__name__}_{current_account}_{current_folder}"
        cached_files = fm_cache.get(cache_key)
        if cached_files is not None:
            files = cached_files
        else:
            lock = _fetch_locks.setdefault(cache_key, asyncio.Lock())
            async with lock:
                cached_files = fm_cache.get(cache_key)
                if cached_files is not None:
                    files = cached_files
                else:
                    files, _ = await provider.list_files(service, current_folder, page_size=1000)
                    fm_cache.set(cache_key, files)
            _fetch_locks.pop(cache_key, None)

        folders = [f for f in files if f["mimeType"] == "application/vnd.google-apps.folder"]
        files_list = [f for f in files if f["mimeType"] != "application/vnd.google-apps.folder"]
        # Paginate folders and files if needed
        page = ctx.user_data.get("fm_page", 0)
        
        if ctx.user_data.get("in_def_location"):
            all_items = folders
        else:
            all_items = folders + files_list
            
        paginator = Paginator(all_items, page, 10)
        paged_items = paginator.items
        total_pages = paginator.total_pages
        pagination_buttons = paginator.get_buttons("fm_prev_page", "fm_next_page")
        
        paged_folders = [item for item in paged_items if item["mimeType"] == "application/vnd.google-apps.folder"]
        paged_files = [item for item in paged_items if item["mimeType"] != "application/vnd.google-apps.folder"]
        
        # await get_breadcrumb
        breadcrumb = await get_breadcrumb(service, account_data['folder_stack'], current_folder, provider.get_folder_name, provider_name=provider.__class__.__name__)
        
        if ctx.user_data.get("in_def_location"):
            text = f"<b>📂 Select Your Upload Location</b>\n\nLocation: <code>{breadcrumb}</code>\n\n<i>Note: Only folders are listed here.</i>\n\n"
        else:
            text = f"<b>📂 Browse your folders and files</b>\n\nLocation: <code>{breadcrumb}</code>\n\n"
            
        buttons = []
        if not ctx.user_data.get("in_def_location"):
            buttons.append([InlineKeyboardButton("🗁 Folder Toolkit", callback_data=f"folder_options:{shorten_id(ctx, current_folder)}")])
        buttons.extend([[InlineKeyboardButton(f"{emoji_maker('application/vnd.google-apps.folder')} {f['name']}", callback_data=f"folder:{shorten_id(ctx, f['id'])}")] for f in paged_folders])
        
        if not ctx.user_data.get("in_def_location"):
            buttons.extend([[InlineKeyboardButton(f"{emoji_maker(f.get('mimeType', ''), f.get('name', ''))} {f['name']}", callback_data=f"file:{shorten_id(ctx, f['id'])}")] for f in paged_files])
            
        # Add pagination buttons if available
        if pagination_buttons:
            buttons.append(pagination_buttons)
            
        if ctx.user_data.get("in_def_location"):
            buttons.append([InlineKeyboardButton("✅ Select Folder", callback_data="set_def_location")])
            
        if account_data["folder_stack"]:
            buttons.append([InlineKeyboardButton("Back", callback_data="back_folder")])
            
        buttons.append([InlineKeyboardButton("Refresh", callback_data="refresh_folder")])
            
        if q:
            try:
                await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
            except Exception as e:
                import telegram
                if not (isinstance(e, telegram.error.BadRequest) and "Message is not modified" in str(e)):
                    await q.edit_message_text("❌ Failed to load file manager. Please try again later.")
        elif m:
            try:
                await m.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
            except Exception as e:
                logger.error(f"Error loading file manager for message: {e}", exc_info=True)
                await m.reply_text("❌ Failed to load file manager. Please try again later.")
        ctx.user_data["state"] = UserState(ctx)
        ctx.user_data["state"].set_state(UserStateEnum.FILE_MANAGER)
        ctx.user_data["fm_total_pages"] = total_pages
        ctx.user_data["fm_page"] = page
    except Exception as e:
        logger.error(f"Error in handle_file_manager: {e}", exc_info=True)
        if 'q' in locals() and q and hasattr(q, 'edit_message_text'):
            await q.edit_message_text("❌ Service unavailable. Please try again later.")
        elif 'm' in locals() and m:
            await m.reply_text("❌ Service unavailable. Please try again later.")


@access_required
async def handle_folder_navigation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if ctx.user_data is None:
            ctx.user_data = {}
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        m = update.message if hasattr(update, 'message') and update.message else None
        if q and q.from_user:
            await q.answer()
            telegram_id = q.from_user.id
        elif m and m.from_user:
            telegram_id = m.from_user.id
        else:
            return
        if "account_data" not in ctx.user_data or ctx.user_data["account_data"] is None:
            ctx.user_data["account_data"] = {}
        current_account = ctx.user_data.get("current_account") or "default_account"
        account_data = ctx.user_data["account_data"].setdefault(current_account, {"current_folder": "root", "folder_stack": [], "folder_pages": {}})
        if "folder_pages" not in account_data or account_data["folder_pages"] is None:
            account_data["folder_pages"] = {}
        
        provider = ctx.bot_data['provider']
        service = await provider.get_service(telegram_id, current_account)
            
        data = q.data if q else (m.text if m else "")
        if not isinstance(data, str):
            return
        if data.startswith("folder:"):
            folder_id = extract_id(ctx, data, "folder:")
            account_data["folder_stack"].append(account_data["current_folder"])
            account_data["current_folder"] = folder_id
            account_data["folder_pages"].pop(folder_id, None)
            await handle_file_manager(update, ctx)
        elif data == "back_folder":
            if account_data["folder_stack"]:
                account_data["current_folder"] = account_data["folder_stack"].pop()
                await handle_file_manager(update, ctx)
        elif data == "refresh_folder":
            cache_key = f"list_files_{provider.__class__.__name__}_{current_account}_{account_data['current_folder']}"
            fm_cache.delete(cache_key)
            await handle_file_manager(update, ctx)
        elif data == "back_to_folder":
            await handle_file_manager(update, ctx)

    except Exception as e:
        logger.error(f"Error in handle_folder_navigation: {e}", exc_info=True)
        if 'q' in locals() and q and hasattr(q, 'edit_message_text'):
            await q.edit_message_text("❌ Failed to navigate folders. Please try again later.")
        elif 'm' in locals() and m:
            await m.reply_text("❌ Failed to navigate folders. Please try again later.")


@access_required
async def handle_file_selection(update: Update, ctx: ContextTypes.DEFAULT_TYPE, override_data: str = None):
    try:
        if ctx.user_data is None:
            ctx.user_data = {}
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        m = update.message if hasattr(update, 'message') and update.message else None
        if q and q.from_user:
            await q.answer()
            telegram_id = q.from_user.id
        elif m and m.from_user:
            telegram_id = m.from_user.id
        else:
            return
        current_account = ctx.user_data.get("current_account")
        provider = ctx.bot_data['provider']
        service = await provider.get_service(telegram_id, current_account)
        
        data = override_data or (q.data if q else (m.text if m else ""))
        if isinstance(data, str) and data.startswith("file:"):
            file_id = extract_id(ctx, data, "file:")
            provider_name = ctx.bot_data.get('provider_name')

            if provider_name == 'rclone':
                buttons = [
                    [InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_file:{shorten_id(ctx, file_id)}")],
                    [InlineKeyboardButton("Back", callback_data="back_to_folder")]
                ]
            else:
                buttons = [
                    [InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_file:{shorten_id(ctx, file_id)}")],
                    [InlineKeyboardButton("🔗 Link", callback_data=f"copy_file_link:{shorten_id(ctx, file_id)}")],
                    [InlineKeyboardButton("Back", callback_data="back_to_folder")]
                ]
            if q and hasattr(q, 'edit_message_text'):
                try:
                    file_meta = await provider.get_file_metadata(service, file_id)
                    import html
                    file_name = file_meta.get('name', 'Unknown')
                    display_name = html.escape(file_name)
                    file_size = humanize.naturalsize(int(file_meta.get('size', 0)), binary=True) if file_meta.get('size') else 'N/A'
                    text = (
                        f"📄 <b>File Details</b>\n\n"
                        f"<b>Name:</b> <code>{display_name}</code>\n"
                        f"<b>Size:</b> <code>{file_size}</code>"
                    )
                except Exception as meta_err:
                    logger.warning(f"[BOT] get_file_metadata failed for {file_id}: {meta_err}")
                    text = "📄 <b>File Details unavailable</b>"
                await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error handling file selection: {e}", exc_info=True)
        _back = InlineKeyboardMarkup([[InlineKeyboardButton("Back to Folder", callback_data="back_to_folder")]])
        if 'q' in locals() and q and hasattr(q, 'edit_message_text'):
            await q.edit_message_text("❌ Failed to select file. Please try again later.", reply_markup=_back)
        elif 'm' in locals() and m:
            await m.reply_text("❌ Failed to select file. Please try again later.", reply_markup=_back)


@access_required
async def handle_folder_selection(update: Update, ctx: ContextTypes.DEFAULT_TYPE, override_data: str = None):
    try:
        if ctx.user_data is None:
            ctx.user_data = {}
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        m = update.message if hasattr(update, 'message') and update.message else None
        if q and q.from_user:
            await q.answer()
            telegram_id = q.from_user.id
        elif m and m.from_user:
            telegram_id = m.from_user.id
        else:
            return
        current_account = ctx.user_data.get("current_account")
        provider = ctx.bot_data['provider']
        service = await provider.get_service(telegram_id, current_account)
        
        data = override_data or (q.data if q else (m.text if m else ""))
        if isinstance(data, str) and data.startswith("folder_options:"):
            folder_id = extract_id(ctx, data, "folder_options:")
            if not service:
                if q and hasattr(q, 'edit_message_text'):
                    await q.edit_message_text("⚠️ Please login first.")
                return
            
            provider_name = ctx.bot_data.get('provider_name')

            # Detect if we're at the cloud root — destructive ops are disabled there
            current_account = ctx.user_data.get("current_account") or "default_account"
            account_data = ctx.user_data.get("account_data", {}).get(current_account, {})
            is_root = (folder_id == account_data.get("current_folder") and
                       not account_data.get("folder_stack"))

            # Fetch folder name for the toolkit header
            folder_display_name = "Current Folder"
            try:
                folder_meta = await provider.get_file_metadata(service, folder_id)
                folder_display_name = folder_meta.get('name', 'Current Folder')
            except Exception:
                pass

            # Store name so the rename prompt can reference it
            ctx.user_data["rename_target_name"] = folder_display_name

            import html
            folder_display_name = html.escape(folder_display_name)
            if is_root:
                folder_display_name = f"{folder_display_name} /"
                # Root: only allow creating sub-folders, no destructive or meta ops
                buttons = [
                    [InlineKeyboardButton("+ New Folder", callback_data=f"new_folder:{shorten_id(ctx, folder_id)}")],
                    [InlineKeyboardButton("Back to Folder", callback_data="back_to_folder")]
                ]
                header = f"🗁 <b><code>{folder_display_name}</code></b> — Folder Toolkit"
                if q and hasattr(q, 'edit_message_text'):
                    await q.edit_message_text(
                        header,
                        reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML'
                    )
                return

            if provider_name == 'rclone':
                # rclone: no link generation, layout: [Rename|Delete] / [New Folder] / [Back]
                buttons = [
                    [InlineKeyboardButton("✏️ Rename", callback_data=f"rename_folder:{shorten_id(ctx, folder_id)}"),
                     InlineKeyboardButton("✕ Delete", callback_data=f"delete_folder:{shorten_id(ctx, folder_id)}")],
                    [InlineKeyboardButton("+ New Folder", callback_data=f"new_folder:{shorten_id(ctx, folder_id)}")],
                    [InlineKeyboardButton("Back", callback_data="back_to_folder")]
                ]
            elif provider_name == 'mega':
                # Mega: no folder link generation (EACCESS), layout: [New Folder] / [Rename|Delete] / [Back]
                buttons = [
                    [InlineKeyboardButton("+ New Folder", callback_data=f"new_folder:{shorten_id(ctx, folder_id)}")],
                    [InlineKeyboardButton("✏️ Rename", callback_data=f"rename_folder:{shorten_id(ctx, folder_id)}"),
                     InlineKeyboardButton("✕ Delete", callback_data=f"delete_folder:{shorten_id(ctx, folder_id)}")],
                    [InlineKeyboardButton("Back", callback_data="back_to_folder")]
                ]
            else:
                # Drive: [New Folder|Link] / [Rename|Delete] / [Back]
                buttons = [
                    [InlineKeyboardButton("+ New Folder", callback_data=f"new_folder:{shorten_id(ctx, folder_id)}"),
                     InlineKeyboardButton("🔗 Link", callback_data=f"copy_folder_link:{shorten_id(ctx, folder_id)}")],
                    [InlineKeyboardButton("✏️ Rename", callback_data=f"rename_folder:{shorten_id(ctx, folder_id)}"),
                     InlineKeyboardButton("✕ Delete", callback_data=f"delete_folder:{shorten_id(ctx, folder_id)}")],
                    [InlineKeyboardButton("Back", callback_data="back_to_folder")]
                ]
            if q and hasattr(q, 'edit_message_text'):
                header = f"🗁 <b><code>{folder_display_name}</code></b> — Folder Toolkit"
                await q.edit_message_text(header, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error handling folder selection: {e}", exc_info=True)
        _back = InlineKeyboardMarkup([[InlineKeyboardButton("Back to Folder", callback_data="back_to_folder")]])
        if 'q' in locals() and q and hasattr(q, 'edit_message_text'):
            await q.edit_message_text("❌ Failed to select folder. Please try again later.", reply_markup=_back)
        elif 'm' in locals() and m:
            await m.reply_text("❌ Failed to select folder. Please try again later.", reply_markup=_back)


@access_required
async def handle_file_operation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if ctx.user_data is None:
            ctx.user_data = {}
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        m = update.message if hasattr(update, 'message') and update.message else None
        if q and q.from_user:
            await q.answer()
            telegram_id = q.from_user.id
        elif m and m.from_user:
            telegram_id = m.from_user.id
        else:
            return
        current_account = ctx.user_data.get("current_account")
        provider = ctx.bot_data['provider']
        service = await provider.get_service(telegram_id, current_account)
        
        data = q.data if q else (m.text if m else "")
        if isinstance(data, str) and data.startswith("delete_file:"):
            file_id = extract_id(ctx, data, "delete_file:")
            # Fetch file name for confirmation context
            try:
                file_meta = await provider.get_file_metadata(service, file_id)
                file_name = file_meta.get('name', 'this file')
            except Exception:
                file_name = 'this file'
            
            import html
            safe_name = html.escape(file_name)
            buttons = [
                [InlineKeyboardButton("🗑️ Yes, Delete", callback_data=f"confirm_delete_file:{shorten_id(ctx, file_id)}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"file:{shorten_id(ctx, file_id)}")]
            ]
            if q and hasattr(q, 'edit_message_text'):
                await q.edit_message_text(
                    f"⚠️ Are you sure you want to delete:\n\n<b><code>{safe_name}</code></b>\n\nIt will move to the Recycle Bin.",
                    reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML'
                )
        elif isinstance(data, str) and data.startswith("confirm_delete_file:"):
            file_id = extract_id(ctx, data, "confirm_delete_file:")
            # Fetch the name before deletion for the confirmation message
            deleted_name = "the file"
            try:
                meta = await provider.get_file_metadata(service, file_id)
                name = meta.get('name', 'the file')
                deleted_name = f"<b>{name}</b>"
            except Exception:
                pass
            account_data = ctx.user_data.get("account_data", {}).get(current_account, {})
            current_folder = account_data.get("current_folder", "root")
            await provider.delete_file(service, file_id)
            invalidate_folder_cache(current_folder)
            if q and hasattr(q, 'edit_message_text'):
                await q.edit_message_text(
                    f"\u2705 {deleted_name} moved to Recycle Bin.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Folder", callback_data="back_to_folder")]]),
                    parse_mode='HTML'
                )
        elif isinstance(data, str) and data.startswith("copy_file_link:"):
            file_id = extract_id(ctx, data, "copy_file_link:")
            link = await provider.get_file_link(service, file_id)
            if q and hasattr(q, 'edit_message_text'):
                _back = InlineKeyboardMarkup([[InlineKeyboardButton("Back to File", callback_data=f"file:{shorten_id(ctx, file_id)}")]])
                if link:
                    await q.edit_message_text(
                        f"🔗 <b>File Link</b>\n\n<code>{link}</code>\n\n<i>Tap the link to copy.</i>",
                        reply_markup=_back, parse_mode='HTML'
                    )
                else:
                    await q.edit_message_text("❌ Failed to generate link. The file may not support public sharing.", reply_markup=_back)
        elif isinstance(data, str) and data.startswith("file_size:"):
            file_id = extract_id(ctx, data, "file_size:")
            if q and hasattr(q, 'edit_message_text'):
                await q.edit_message_text("⏳ Calculating file size...")
            size = await provider.get_item_size(service, file_id, is_folder=False)
            import humanize
            size_str = humanize.naturalsize(size, binary=True)
            if "cached_sizes" not in ctx.user_data:
                ctx.user_data["cached_sizes"] = {}
            ctx.user_data["cached_sizes"][file_id] = size_str
            await handle_file_selection(update, ctx, override_data=f"file:{shorten_id(ctx, file_id)}")
    except Exception as e:
        logger.error(f"Error performing file operation: {e}", exc_info=True)
        if 'q' in locals() and q and hasattr(q, 'edit_message_text'):
            await q.edit_message_text("❌ Failed to perform file operation. Please try again later.")
        elif 'm' in locals() and m:
            await m.reply_text("❌ Failed to perform file operation. Please try again later.")


@access_required
async def handle_folder_operation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if ctx.user_data is None:
            ctx.user_data = {}
        q = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        m = update.message if hasattr(update, 'message') and update.message else None
        if q and q.from_user:
            await q.answer()
            telegram_id = q.from_user.id
        elif m and m.from_user:
            telegram_id = m.from_user.id
        else:
            return
        current_account = ctx.user_data.get("current_account")
        provider = ctx.bot_data['provider']
        service = await provider.get_service(telegram_id, current_account)
        
        data = q.data if q else (m.text if m else "")
        if isinstance(data, str) and data.startswith("rename_folder:"):
            folder_id = extract_id(ctx, data, "rename_folder:")
            # Fetch current name to show in the prompt
            current_name = ctx.user_data.pop("rename_target_name", None)
            if not current_name:
                try:
                    meta = await provider.get_file_metadata(service, folder_id)
                    current_name = meta.get('name', '')
                except Exception:
                    current_name = ''
            prompt = f"✏️ Rename <b>{current_name}</b>\n\nEnter the new folder name:"
            if q and hasattr(q, 'edit_message_text'):
                await q.edit_message_text(prompt,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"folder_options:{shorten_id(ctx, folder_id)}")]]),
                    parse_mode='HTML')
            ctx.user_data["next_action"] = f"rename_folder:{folder_id}"
        elif isinstance(data, str) and data.startswith("delete_folder:"):
            folder_id = extract_id(ctx, data, "delete_folder:")
            folder_name = "this folder"
            import html
            try:
                meta = await provider.get_file_metadata(service, folder_id)
                folder_name = f"<b><code>{html.escape(meta.get('name', 'this folder'))}</code></b>"
            except Exception:
                pass
            buttons = [
                [InlineKeyboardButton("🗑️ Yes, Delete", callback_data=f"confirm_delete_folder:{shorten_id(ctx, folder_id)}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"folder_options:{shorten_id(ctx, folder_id)}")]
            ]
            if q and hasattr(q, 'edit_message_text'):
                await q.edit_message_text(
                    f"⚠️ Are you sure you want to delete:\n\n{folder_name}\n\nIt will move to the Recycle Bin.",
                    reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML'
                )
        elif isinstance(data, str) and data.startswith("confirm_delete_folder:"):
            folder_id = extract_id(ctx, data, "confirm_delete_folder:")
            deleted_name = "the folder"
            import html
            try:
                meta = await provider.get_file_metadata(service, folder_id)
                name = meta.get('name', 'the folder')
                deleted_name = f"<b><code>{html.escape(name)}</code></b>"
            except Exception:
                pass
            account_data = ctx.user_data.get("account_data", {}).get(current_account, {})
            current_folder = account_data.get("current_folder", "root")
            await provider.delete_file(service, folder_id)
            invalidate_folder_cache(current_folder)
            if q and hasattr(q, 'edit_message_text'):
                await q.edit_message_text(
                    f"\u2705 {deleted_name} moved to Recycle Bin.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Folder", callback_data="back_to_folder")]]),
                    parse_mode='HTML'
                )
        elif isinstance(data, str) and data.startswith("copy_folder_link:"):
            folder_id = extract_id(ctx, data, "copy_folder_link:")
            link = await provider.get_file_link(service, folder_id)
            if q and hasattr(q, 'edit_message_text'):
                _back = InlineKeyboardMarkup([[InlineKeyboardButton("Back to Folder Toolkit", callback_data=f"folder_options:{shorten_id(ctx, folder_id)}")]])
                if link:
                    await q.edit_message_text(
                        f"🔗 <b>Folder Link</b>\n\n<code>{link}</code>\n\n<i>Tap the link to copy.</i>",
                        reply_markup=_back, parse_mode='HTML'
                    )
                else:
                    await q.edit_message_text("❌ Failed to generate link. The folder may not support public sharing.", reply_markup=_back)
        elif isinstance(data, str) and data.startswith("new_folder:"):
            folder_id = extract_id(ctx, data, "new_folder:")
            if q and hasattr(q, 'edit_message_text'):
                await q.edit_message_text("✏️ Enter new folder name:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"folder_options:{shorten_id(ctx, folder_id)}")]]))
            ctx.user_data["next_action"] = f"create_folder:{folder_id}"
    except Exception as e:
        logger.error(f"Error performing folder operation: {e}", exc_info=True)
        if 'q' in locals() and q and hasattr(q, 'edit_message_text'):
            await q.edit_message_text("❌ Failed to perform folder operation. Please try again later.")
        elif 'm' in locals() and m:
            await m.reply_text("❌ Failed to perform folder operation. Please try again later.")




@access_required
async def handle_file_manager_pagination(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        q = getattr(update, 'callback_query', None)
        if not q:
            return
        await q.answer()
        data = getattr(q, 'data', None)
        if data == "fm_prev_page":
            ctx.user_data["fm_page"] = max(0, ctx.user_data.get("fm_page", 0) - 1)
        elif data == "fm_next_page":
            ctx.user_data["fm_page"] = min(ctx.user_data.get("fm_total_pages", 1) - 1, ctx.user_data.get("fm_page", 0) + 1)
        await handle_file_manager(update, ctx)
    except Exception as e:
        logger.error(f"Error handling file manager pagination: {e}", exc_info=True)
        if q and hasattr(q, 'edit_message_text'):
            await q.edit_message_text("❌ Failed to navigate pages.")

def register_handlers(app):
    from telegram.ext import CommandHandler, CallbackQueryHandler
    app.add_handler(CommandHandler("filemanager", handle_file_manager))
    app.add_handler(CallbackQueryHandler(handle_file_manager, pattern=r"^FILE_MGR$"))
    app.add_handler(CallbackQueryHandler(handle_file_manager_pagination, pattern=r"^(fm_prev_page|fm_next_page)$"))
    app.add_handler(CallbackQueryHandler(handle_folder_navigation, pattern=r"^(folder:.*|back_folder|back_to_folder)$"))
    app.add_handler(CallbackQueryHandler(handle_file_selection, pattern=r"^file:.*$"))
    app.add_handler(CallbackQueryHandler(handle_folder_selection, pattern=r"^folder_options:.*$"))
    app.add_handler(CallbackQueryHandler(handle_file_operation, pattern=r"^(delete_file:.*|confirm_delete_file:.*|copy_file_link:.*)$"))
    app.add_handler(CallbackQueryHandler(handle_folder_operation, pattern=r"^(rename_folder:.*|delete_folder:.*|confirm_delete_folder:.*|copy_folder_link:.*|new_folder:.*)$"))
