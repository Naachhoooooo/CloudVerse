import humanize
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from shared.core.ErrorHandler import handle_errors
from shared.managers.AccessManager import access_required
from shared.core.UserState import UserState, UserStateEnum
from shared.components.Settings import handle_login

@handle_errors
@access_required
async def handle_user_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Central text-input state machine for all bots.
    Handles every named UserState that a shared component sets, so all three
    bots (Drive, Mega, rclone) resolve text input correctly without duplicating
    this logic in each main.py.
    """
    if ctx.user_data is None:
        ctx.user_data = {}

    if "state" not in ctx.user_data or not isinstance(ctx.user_data["state"], UserState):
        ctx.user_data["state"] = UserState(ctx)

    user_state: UserState = ctx.user_data["state"]
    m = update.message
    if not m or not m.from_user:
        return

    telegram_id = m.from_user.id
    text = (m.text or "").strip()
    
    # ── Universal Cancel Interceptor ───────────────────────────────────────────
    current_state_val = ctx.user_data.get("current_state")
    if text.lower() == "/cancel":
        if current_state_val or any(ctx.user_data.get(k) for k in ["awaiting_new_domain", "awaiting_delete_typein", "awaiting_api_id", "awaiting_api_hash", "awaiting_telethon_phone", "awaiting_telethon_code", "awaiting_telethon_2fa", "awaiting_approve_message", "awaiting_reject_message", "awaiting_limit_hours", "awaiting_policy_update", "awaiting_broadcast_message", "awaiting_quota_input"]):
            user_state.reset()
            keys_to_clear = ["awaiting_new_domain", "awaiting_delete_typein", "awaiting_api_id", "awaiting_api_hash", "awaiting_telethon_phone", "awaiting_telethon_code", "awaiting_telethon_2fa", "awaiting_approve_message", "awaiting_reject_message", "awaiting_limit_hours", "awaiting_policy_update", "awaiting_broadcast_message", "awaiting_quota_input"]
            for k in keys_to_clear:
                ctx.user_data.pop(k, None)
                
            from shared.components.Start import start
            await m.reply_text("❌ Action cancelled.")
            await start(update, ctx)
            return

    # ── Policy Update Routing ──────────────────────────────────────────
    if ctx.user_data.get("awaiting_policy_update"):
        from shared.components.Policy import handle_policy_update
        await handle_policy_update(update, ctx)
        return

    # ── Auth flows (Drive, Mega) ─────────────────────────────────────────────
    if user_state.is_state(UserStateEnum.EXPECTING_CODE) or \
       user_state.is_state(UserStateEnum.EXPECTING_MEGA_EMAIL) or \
       user_state.is_state(UserStateEnum.EXPECTING_MEGA_PASSWORD):
        await handle_login(update, ctx)
        return

    # ── Approval message relay (admin messaging a user) ───────────────────────
    if user_state.is_state(UserStateEnum.EXPECTING_APPROVAL_MESSAGE):
        data = user_state.data
        target_user_id = data.get("user_id")
        if target_user_id and text:
            await ctx.bot.send_message(chat_id=target_user_id, text=text)
            await m.reply_text("✅ Message sent to the user.")
        else:
            await m.reply_text("⚠️ Could not relay message — user ID missing or empty text.")
        user_state.reset()
        return


    # ── Parallel transfers input ───────────────────────────────────────────────
    if user_state.is_state(UserStateEnum.EXPECTING_PARALLEL_TRANSFERS):
        if text.isdigit():
            num = int(text)
            if 1 <= num <= 3:
                credential_repo = ctx.bot_data.get('credential_repo')
                if credential_repo and await credential_repo.update_parallel_uploads(telegram_id=str(telegram_id), parallel=num):
                    await m.reply_text(f"✅ Parallel transfer limit updated to {num}.")
                else:
                    await m.reply_text("❌ Failed to update parallel transfer limit.")
                user_state.reset()
                return
        await m.reply_text("⚠️ Enter a number between 1 and 3.")
        return

    # ── File/folder rename & create (FileManager) ─────────────────────────────
    next_action = ctx.user_data.get("next_action")
    if next_action:
        del ctx.user_data["next_action"]
        current_account = ctx.user_data.get("current_account")
        provider = ctx.bot_data.get("provider")
        if provider:
            from shared.components.FileManager import sanitize_name, extract_id, shorten_id
            from shared.core.CacheUtils import invalidate_folder_cache
            service = await provider.get_service(telegram_id, current_account)
            current_folder = ctx.user_data.get("account_data", {}).get(current_account, {}).get("current_folder", "root")
            try:
                # Use sanitize_name (not sanitize_id) — user names can have spaces
                clean_text = sanitize_name(text)
            except ValueError as e:
                await m.reply_text(f"❌ {e}")
                return
                
            if next_action.startswith("rename_folder:"):
                folder_id = extract_id(ctx, next_action, ":")
                await provider.rename_file(service, folder_id, clean_text)
                invalidate_folder_cache(current_folder)
                buttons = [[InlineKeyboardButton("Back to Folder Options", callback_data=f"folder_options:{shorten_id(ctx, folder_id)}")]]
                import html
                await m.reply_text(f"✅ Folder renamed to <b>{html.escape(clean_text)}</b>.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
            elif next_action.startswith("create_folder:"):
                parent_id = extract_id(ctx, next_action, ":")
                await provider.create_folder(service, clean_text, parent_id)
                invalidate_folder_cache(current_folder)
                buttons = [[InlineKeyboardButton("Back to Folder Options", callback_data=f"folder_options:{shorten_id(ctx, parent_id)}")]]
                import html
                await m.reply_text(f"✅ Folder <b>{html.escape(clean_text)}</b> created.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        return
