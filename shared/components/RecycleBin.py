from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.utils.emoji_maker import emoji_maker

from shared.utils.pagination import Paginator
from shared.core.Logger import get_logger
from shared.managers.AccessManager import access_required

logger = get_logger(__name__)


@access_required
async def handle_bin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Main recycle bin display function."""
    try:
        if ctx.user_data is None:
            ctx.user_data = {}
        if update.message and update.message.from_user:
            telegram_id = update.message.from_user.id
            is_command = True
        elif update.callback_query and update.callback_query.from_user:
            telegram_id = update.callback_query.from_user.id
            is_command = False
            await update.callback_query.answer()
        else:
            return

        if ctx.bot_data.get('provider_name') == 'rclone':
            if not is_command and update.callback_query:
                await update.callback_query.answer()
            return

        provider = ctx.bot_data["provider"]
        service = await provider.get_service(telegram_id)
        if not service:
            if is_command and update.message:
                await update.message.reply_text("⚠️ Please login first.")
            elif not is_command and update.callback_query:
                await update.callback_query.edit_message_text("⚠️ Please login first.")
            return

        files, next_token = await provider.list_trashed_files(
            service,
            ctx.user_data.get("bin_page_token")
            if isinstance(ctx.user_data, dict)
            else None,
        )
        page = ctx.user_data.get("bin_page", 0)

        paginator = Paginator(files, page, 10)
        paged_files = paginator.items
        total_pages = paginator.total_pages
        pagination_buttons = paginator.get_buttons("bin_prev_page", "bin_next_page")

        ctx.user_data["current_bin_files"] = paged_files
        ctx.user_data["bin_next_token"] = next_token
        ctx.user_data["bin_page"] = page

        item_count = len(files)
        total_bin_bytes = sum(int(f.get("size", 0)) for f in files if f.get("size"))
        total_bin_size = humanize.naturalsize(total_bin_bytes, binary=True)
        text = (
            f"<b>🗑 Bin</b> — {item_count} item{'s' if item_count != 1 else ''}\n\n"
            f"<b>Total Bin Size:</b> {total_bin_size}"
        )
        if not paged_files:
            text += "\n\n<i>Your recycle bin is currently empty.</i>"

        buttons = []
        if paged_files:
            for f in paged_files:
                emoji = emoji_maker(f["mimeType"], f.get("name", ""))
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"{emoji} {f['name']}", callback_data=f"bin_item:{f['id']}"
                        )
                    ]
                )

        if total_pages > 1:
            if pagination_buttons:
                buttons.append(pagination_buttons)

        buttons.append(
            [InlineKeyboardButton("🗑 Empty Bin", callback_data="empty_bin")]
        )
        if is_command and update.message:
            await update.message.reply_text(
                text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML"
            )
        elif not is_command and update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML"
            )
    except Exception as e:
        logger.error(
            f"Error in handle_bin for user: {locals().get('telegram_id', 'unknown')}: {e}"
        )
        if update.message:
            await update.message.reply_text(
                "❌ Failed to load recycle bin. Please try again later."
            )
        elif update.callback_query:
            await update.callback_query.edit_message_text(
                "❌ Failed to load recycle bin. Please try again later."
            )


@access_required
async def handle_bin_navigation(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle pagination and general bin navigation."""
    try:
        q = update.callback_query
        if q and hasattr(q, "answer"):
            await q.answer()

        data = q.data if q and hasattr(q, "data") else ""
        if q and hasattr(q, "from_user") and q.from_user:
            telegram_id = q.from_user.id
        else:
            return

        provider = ctx.bot_data["provider"]
        service = await provider.get_service(telegram_id)
        if not service:
            await q.edit_message_text("Please login first.")
            return

        if ctx.user_data is None:
            ctx.user_data = {}

        if data == "bin_next_page":
            if isinstance(ctx.user_data, dict):
                ctx.user_data["bin_page"] = ctx.user_data.get("bin_page", 0) + 1
                ctx.user_data["bin_page_token"] = ctx.user_data.get("bin_next_token")
            await handle_bin(update, ctx)

        elif data == "bin_prev_page":
            if isinstance(ctx.user_data, dict):
                ctx.user_data["bin_page"] = max(0, ctx.user_data.get("bin_page", 0) - 1)
                # When going back, we clear page token to re-fetch first segment
                ctx.user_data["bin_page_token"] = None
            await handle_bin(update, ctx)

        elif data == "empty_bin":
            buttons = [
                [
                    InlineKeyboardButton(
                        "⚠️ Yes, Empty", callback_data="confirm_empty_bin"
                    )
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="back")],
            ]
            await q.edit_message_text(
                "<b>⚠️ Are you sure you want to empty the entire recycle bin?</b>\n\n"
                "This will permanently delete all items in the bin.\n"
                "This action cannot be undone.",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )

        elif data == "confirm_empty_bin":
            await provider.empty_trash(service)
            await q.edit_message_text("✅ Recycle bin has been emptied successfully.")

        elif data == "back_to_bin":
            await handle_bin(update, ctx)

        # Dispatch item operations
        elif any(
            data.startswith(prefix)
            for prefix in ["bin_item:", "restore:", "perm_delete:"]
        ):
            # Try file selection logic first
            from .RecycleBin import handle_file_selection, handle_folder_selection

            # We need to peek to decide or just try both.
            # Since we can't easily peek without duplicating logic, we will check user_data.
            # But handle_file_selection implementations check mime type.

            # Simple dispatch approach:
            # If it works, great. If not, try folder.
            # However, handlers might answer query which prevents second handler.
            # We should inspect the item type from user_data if possible.

            file_id = data.split(":", 1)[1]
            current_files = ctx.user_data.get("current_bin_files", [])
            # Also check selected_bin_item if it's a secondary action (restore/delete)
            selected_item = ctx.user_data.get("selected_bin_item")

            target_item = None
            if data.startswith("bin_item:"):
                target_item = next(
                    (f for f in current_files if f["id"] == file_id), None
                )
            else:
                target_item = (
                    selected_item
                    if selected_item and selected_item["id"] == file_id
                    else None
                )

            if target_item:
                if target_item.get("mimeType") == "application/vnd.google-apps.folder":
                    await handle_folder_selection(update, ctx)
                else:
                    await handle_file_selection(update, ctx)
            else:
                # Fallback if item not found in context (might be stale)
                # Try both sequentially, banking on internal checks
                await handle_file_selection(update, ctx)
                await handle_folder_selection(update, ctx)

    except Exception as e:
        logger.error(
            f"Error in handle_bin_navigation for user: {locals().get('telegram_id', 'unknown')}: {e}"
        )
        if q and hasattr(q, "edit_message_text"):
            await q.edit_message_text(
                "❌ An error occurred while processing your request. Please try again later."
            )


@access_required
async def handle_file_selection(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle file-specific operations in recycle bin."""
    try:
        q = update.callback_query
        if q and hasattr(q, "answer"):
            await q.answer()

        data = q.data if q and hasattr(q, "data") else ""
        if q and hasattr(q, "from_user") and q.from_user:
            telegram_id = q.from_user.id
        else:
            return

        provider = ctx.bot_data["provider"]
        service = await provider.get_service(telegram_id)
        if not service:
            await q.edit_message_text("Please login first.")
            return

        if ctx.user_data is None:
            ctx.user_data = {}

        if isinstance(data, str) and data.startswith("bin_item:"):
            if isinstance(ctx.user_data, dict) and "current_bin_files" in ctx.user_data:
                file_id = data.split(":", 1)[1]
                file = next(
                    (
                        f
                        for f in ctx.user_data["current_bin_files"]
                        if isinstance(f, dict)
                        and f.get("id") == file_id
                        and f.get("mimeType") != "application/vnd.google-apps.folder"
                    ),
                    None,
                )
                if file:
                    ctx.user_data["selected_bin_item"] = file
                    buttons = [
                        [
                            InlineKeyboardButton(
                                "♻️ Restore", callback_data=f"restore:{file_id}"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "🔥 Delete Permanently",
                                callback_data=f"perm_delete:{file_id}",
                            )
                        ],
                        [InlineKeyboardButton("Back", callback_data="back_to_bin")],
                    ]
                    if q and hasattr(q, "edit_message_text"):
                        name = file['name']
                        display_name = name
                        await q.edit_message_text(
                            f"📄 <b>File Details</b>\n\n<b>Name:</b> {display_name}\n\nChoose an action:",
                            reply_markup=InlineKeyboardMarkup(buttons),
                            parse_mode="HTML"
                        )

        elif isinstance(data, str) and data.startswith("restore:"):
            if isinstance(ctx.user_data, dict):
                file = ctx.user_data.get("selected_bin_item")
                if (
                    file
                    and file.get("mimeType") != "application/vnd.google-apps.folder"
                ):
                    await provider.restore_file(service, file.get("id"))
                    if q and hasattr(q, "edit_message_text"):
                        name = file.get('name', 'file')
                        display_name = name if len(name) <= 45 else name[:42] + "..."
                        await q.edit_message_text(
                            f"✅ <b>{display_name}</b> successfully restored",
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton(
                                            "Back to Bin", callback_data="back_to_bin"
                                        )
                                    ]
                                ]
                            ),
                        )

        elif isinstance(data, str) and data.startswith("perm_delete:"):
            if isinstance(ctx.user_data, dict):
                file = ctx.user_data.get("selected_bin_item")
                if (
                    file
                    and file.get("mimeType") != "application/vnd.google-apps.folder"
                ):
                    if q and hasattr(q, "edit_message_text"):
                        await q.edit_message_text(
                            f"⚠️ Are you sure you want to permanently delete:\n\n"
                            f"<b>{file.get('name')}</b>\n\n"
                            "This action cannot be undone.\n"
                            'Type "DELETE" to confirm:',
                            parse_mode='HTML'
                        )
                    ctx.user_data["expecting_delete_confirmation"] = file

    except Exception as e:
        logger.error(
            f"Error in handle_file_selection for user: {locals().get('telegram_id', 'unknown')}: {e}"
        )
        if q and hasattr(q, "edit_message_text"):
            await q.edit_message_text(
                "❌ An error occurred while processing your request. Please try again later."
            )


@access_required
async def handle_folder_selection(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle folder-specific operations in recycle bin."""
    try:
        q = update.callback_query
        if q and hasattr(q, "answer"):
            await q.answer()

        data = q.data if q and hasattr(q, "data") else ""
        if q and hasattr(q, "from_user") and q.from_user:
            telegram_id = q.from_user.id
        else:
            return

        provider = ctx.bot_data["provider"]
        service = await provider.get_service(telegram_id)
        if not service:
            await q.edit_message_text("Please login first.")
            return

        if ctx.user_data is None:
            ctx.user_data = {}

        if isinstance(data, str) and data.startswith("bin_item:"):
            if isinstance(ctx.user_data, dict) and "current_bin_files" in ctx.user_data:
                file_id = data.split(":", 1)[1]
                file = next(
                    (
                        f
                        for f in ctx.user_data["current_bin_files"]
                        if isinstance(f, dict)
                        and f.get("id") == file_id
                        and f.get("mimeType") == "application/vnd.google-apps.folder"
                    ),
                    None,
                )
                if file:
                    ctx.user_data["selected_bin_item"] = file
                    buttons = [
                        [
                            InlineKeyboardButton(
                                "♻️ Restore", callback_data=f"restore:{file_id}"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "🔥 Delete Permanently",
                                callback_data=f"perm_delete:{file_id}",
                            )
                        ],
                        [InlineKeyboardButton("Back", callback_data="back_to_bin")],
                    ]
                    if q and hasattr(q, "edit_message_text"):
                        name = file['name']
                        display_name = name if len(name) <= 45 else name[:42] + "..."
                        await q.edit_message_text(
                            f"📁 <b>Folder Details</b>\n\n<b>Name:</b> <code>{display_name}</code>\n\nChoose an action:",
                            reply_markup=InlineKeyboardMarkup(buttons),
                            parse_mode="HTML"
                        )

        elif isinstance(data, str) and data.startswith("restore:"):
            if isinstance(ctx.user_data, dict):
                file = ctx.user_data.get("selected_bin_item")
                if (
                    file
                    and file.get("mimeType") == "application/vnd.google-apps.folder"
                ):
                    await provider.restore_file(service, file.get("id"))
                    if q and hasattr(q, "edit_message_text"):
                        name = file.get('name', 'folder')
                        display_name = name if len(name) <= 45 else name[:42] + "..."
                        await q.edit_message_text(
                            f"✅ <b>{display_name}</b> successfully restored",
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton(
                                            "Back to Bin", callback_data="back_to_bin"
                                        )
                                    ]
                                ]
                            ),
                        )

        elif isinstance(data, str) and data.startswith("perm_delete:"):
            if isinstance(ctx.user_data, dict):
                file = ctx.user_data.get("selected_bin_item")
                if (
                    file
                    and file.get("mimeType") == "application/vnd.google-apps.folder"
                ):
                    if q and hasattr(q, "edit_message_text"):
                        await q.edit_message_text(
                            f"⚠️ Are you sure you want to permanently delete:\n\n"
                            f"<b>{file.get('name')}</b>\n\n"
                            "This action cannot be undone.\n"
                            'Type "DELETE" to confirm:',
                            parse_mode='HTML'
                        )
                    ctx.user_data["expecting_delete_confirmation"] = file

    except Exception as e:
        logger.error(
            f"Error in handle_folder_selection for user: {locals().get('telegram_id', 'unknown')}: {e}"
        )
        if q and hasattr(q, "edit_message_text"):
            await q.edit_message_text(
                "❌ An error occurred while processing your request. Please try again later."
            )

def register_handlers(app):
    from telegram.ext import CommandHandler, CallbackQueryHandler
    app.add_handler(CommandHandler("recyclebin", handle_bin))
    app.add_handler(CallbackQueryHandler(handle_bin, pattern=r"^BIN$"))
    app.add_handler(CallbackQueryHandler(handle_bin_navigation, pattern=r"^(bin_next_page|bin_prev_page|bin_item:.*|restore:.*|perm_delete:.*|confirm_empty_bin|back_to_bin|empty_bin)$"))
