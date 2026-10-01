from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.managers.SessionManager import get_session_manager

logger = get_logger(__name__)

async def _render_session_details(q, session_id, session_manager):
    status = session_manager.get_session_status(session_id)
    if "error" in status:
        await q.edit_message_text(
            f"❌ {status['error']}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_telethon_sessions")]])
        )
        return
    text = f"📱 <b>Session {session_id} Details</b>\n\n"
    text += f"<b>Name:</b> <code>{status['name']}</code>\n"
    text += f"<b>Phone:</b> <code>{status['phone'] or 'Not set'}</code>\n"
    text += f"<b>API ID:</b> <code>{status['api_id'] or 'Not set'}</code>\n"
    text += f"<b>API Hash:</b> <code>{'Set' if status['api_hash'] else 'Not set'}</code>\n"
    text += f"<b>Status:</b> {'✅ Active' if status['authenticated'] and not status['is_expired'] else '❌ Inactive'}\n"
    if status['is_expired']:
        text += "<b>Expired:</b> ❌ Yes\n"
    text += f"<b>Last Check:</b> <code>{status['last_check']}</code>\n"
    text += f"<b>Expiry Date:</b> <code>{status['expiry_date']}</code>\n"
    text += f"<b>Session File:</b> {'✅ Exists' if status['session_file_exists'] else '❌ Missing'}\n"
    buttons = []
    if status['authenticated']:
        buttons.append([InlineKeyboardButton("⏳ Check Expiry", callback_data=f"check_session:{session_id}")])
        buttons.append([InlineKeyboardButton("🚪 Logout Session", callback_data=f"logout_session:{session_id}")])
        buttons.append([InlineKeyboardButton("🔑 Update API Credentials", callback_data=f"set_api_credentials:{session_id}")])
    else:
        buttons.append([InlineKeyboardButton("🔐 Authenticate", callback_data=f"authenticate_session:{session_id}")])
    buttons.append([InlineKeyboardButton("Back", callback_data="manage_telethon_sessions")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def _handle_check_session(q, ctx, session_id, session_manager):
    await q.edit_message_text(
        f"⏳ Checking Session {session_id} validity...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_telethon_sessions")]])
    )
    is_valid, message = await session_manager.check_session_validity(session_id)
    if is_valid:
        result_text = f"✅ <b>Session {session_id} is valid</b>\n\n{message}"
    else:
        result_text = f"❌ <b>Session {session_id} is expired</b>\n\n{message}"
        if "expired" in message.lower():
            await session_manager.send_expiry_alert(ctx, session_id, "Manual validity check", message)
    buttons = [
        [InlineKeyboardButton("🔍 View Details", callback_data=f"session_details:{session_id}")],
        [InlineKeyboardButton("Back", callback_data="manage_telethon_sessions")]
    ]
    await q.edit_message_text(result_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def _handle_logout_session_prompt(q, session_id):
    text = (
        f"🚪 <b>Logout Session {session_id}</b>\n\n"
        f"Are you sure you want to logout this session?\n"
        f"This will remove the session file and require re-authentication."
    )
    buttons = [
        [InlineKeyboardButton("🚪 Confirm Logout", callback_data=f"session_confirm_logout:{session_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"session_details:{session_id}")]
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def _handle_confirm_logout(q, session_id, session_manager):
    await q.edit_message_text(
        f"⏳ Logging out Session {session_id}...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_telethon_sessions")]])
    )
    success, message = await session_manager.logout_session(session_id)
    if success:
        result_text = f"✅ <b>Session {session_id} logged out successfully</b>\n\n{message}"
    else:
        result_text = f"❌ <b>Failed to logout Session {session_id}</b>\n\n{message}"
    buttons = [
        [InlineKeyboardButton("Back to Sessions", callback_data="manage_telethon_sessions")]
    ]
    await q.edit_message_text(result_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def _render_sessions_list(q, session_manager):
    sessions_status = session_manager.get_all_sessions_status()
    text = "📱 <b>Telethon Sessions Management</b>\n\n"
    text += "Manage up to 3 concurrent Telethon sessions:\n\n"
    buttons = []
    for status in sessions_status:
        session_id = status['session_id']
        if status['authenticated'] and not status['is_expired']:
            button_text = f"✅ Session {session_id}"
            callback_data = f"session_details:{session_id}"
        elif status['authenticated'] and status['is_expired']:
            button_text = f"❌ Session {session_id} - Expired"
            callback_data = f"session_details:{session_id}"
        else:
            button_text = f"🔐 Authenticate Session {session_id}"
            callback_data = f"authenticate_session:{session_id}"
        buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    buttons.append([InlineKeyboardButton("Refresh", callback_data="manage_telethon_sessions")])
    try:
        if q:
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    except Exception as e:
        if "Message is not modified" not in str(e):
            raise
    return text, InlineKeyboardMarkup(buttons)

async def manage_telethon_sessions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    session_manager = get_session_manager()
    q = update.callback_query
    if not q or not q.from_user:
        return
    await q.answer()
    if ctx.user_data is None:
        ctx.user_data = {}
    if q.data.startswith("session_details:"):
        await _render_session_details(q, int(q.data.split(":")[1]), session_manager)
    elif q.data.startswith("check_session:"):
        await _handle_check_session(q, ctx, int(q.data.split(":")[1]), session_manager)
    elif q.data.startswith("logout_session:"):
        await _handle_logout_session_prompt(q, int(q.data.split(":")[1]))
    elif q.data.startswith("session_confirm_logout:"):
        await _handle_confirm_logout(q, int(q.data.split(":")[1]), session_manager)
    else:
        await _render_sessions_list(q, session_manager)

async def _handle_api_credentials_prompt(q, ctx):
    await q.answer()
    session_id = int(q.data.split(":")[1])
    ctx.user_data['update_api_session_id'] = session_id
    ctx.user_data['awaiting_update_api_id'] = True
    text = (
        f"⚙️ <b>Update API Credentials for Session {session_id}</b>\n\n"
        f"Please enter your new Telegram API ID:\n\n"
        f"<i>You can get your API credentials from:</i>\n"
        f"<code>https://my.telegram.org/apps</code>\n\n"
        f"<b>Step 1:</b> Enter API ID"
    )
    buttons = [
        [InlineKeyboardButton("❌ Cancel", callback_data="manage_telethon_sessions")]
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def _handle_api_credentials_input(m, ctx, text, session_manager):
    session_id = ctx.user_data.get('update_api_session_id')
    if not session_id:
        return
    if ctx.user_data.get('awaiting_update_api_id'):
        api_id = text.strip()
        try:
            int(api_id)
        except ValueError:
            await m.reply_text(
                '❌ Invalid API ID. Please enter a valid numeric API ID.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='manage_telethon_sessions')]])
            )
            return
        ctx.user_data['temp_update_api_id'] = api_id
        ctx.user_data['awaiting_update_api_id'] = False
        ctx.user_data['awaiting_update_api_hash'] = True
        await m.reply_text(
            f'✅ API ID saved: <code>{api_id}</code>\n\n'
            f'<b>Step 2:</b> Now enter your API Hash:',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='manage_telethon_sessions')]]),
            parse_mode="HTML"
        )
    elif ctx.user_data.get('awaiting_update_api_hash'):
        api_hash = text.strip()
        api_id = ctx.user_data.get('temp_update_api_id')
        if len(api_hash) != 32:
            await m.reply_text(
                '❌ Invalid API Hash. API Hash should be 32 characters long.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='manage_telethon_sessions')]])
            )
            return
        success = await session_manager.set_session_api_credentials(session_id, api_id, api_hash)
        if success:
            await m.reply_text(
                f'✅ <b>API Credentials Updated Successfully!</b>\n\n'
                f'Session {session_id} is now configured with:\n'
                f'• API ID: <code>{api_id}</code>\n'
                f'• API Hash: <code>Set</code>\n\n'
                f'ℹ️ <i>Note: You may need to re-authenticate this session.</i>',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton('🔍 View Details', callback_data=f'session_details:{session_id}')]
                ]),
                parse_mode="HTML"
            )
        else:
            await m.reply_text(
                f'❌ Failed to update API credentials for Session {session_id}.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='manage_telethon_sessions')]])
            )
        ctx.user_data.pop('update_api_session_id', None)
        ctx.user_data.pop('awaiting_update_api_id', None)
        ctx.user_data.pop('awaiting_update_api_hash', None)
        ctx.user_data.pop('temp_update_api_id', None)

async def handle_update_api_credentials(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    session_manager = get_session_manager()
    q = update.callback_query
    m = update.message
    if ctx.user_data is None:
        ctx.user_data = {}
    if q and q.data.startswith("set_api_credentials:"):
        await _handle_api_credentials_prompt(q, ctx)
    elif m:
        text = m.text if m and m.text else None
        telegram_id = m.from_user.id if m and m.from_user else None
        account_repo = ctx.bot_data.get('account_repo')
        if not telegram_id or not account_repo or not await account_repo.is_admin(telegram_id=telegram_id):
            return
        if not text:
            return
        if text.strip().lower() == 'cancel':
            ctx.user_data.pop('update_api_session_id', None)
            ctx.user_data.pop('awaiting_update_api_id', None)
            ctx.user_data.pop('awaiting_update_api_hash', None)
            ctx.user_data.pop('temp_update_api_id', None)
            await m.reply_text(
                '🚫 API credentials update cancelled.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_telethon_sessions")]])
            )
            return
        await _handle_api_credentials_input(m, ctx, text, session_manager)

async def _handle_auth_prompt(q, ctx, session_id, session_manager):
    status = session_manager.get_session_status(session_id)
    if not status.get('has_api_credentials'):
        ctx.user_data['auth_session_id'] = session_id
        ctx.user_data['awaiting_api_id'] = True
        text = (
            f"🔐 <b>Authenticate Session {session_id}</b>\n\n"
            f"<b>Step 1:</b> Please enter your Telegram API ID:\n\n"
            f"<i>You can get your API credentials from:</i>\n"
            f"<code>https://my.telegram.org/apps</code>"
        )
        buttons = [
            [InlineKeyboardButton("❌ Cancel", callback_data="manage_telethon_sessions")]
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return
    ctx.user_data['auth_session_id'] = session_id
    ctx.user_data['awaiting_telethon_phone'] = True
    text = (
        f"🔐 <b>Authenticate Session {session_id}</b>\n\n"
        f"Please enter your phone number with country code:\n"
        f"<i>Example: +1234567890</i>"
    )
    buttons = [
        [InlineKeyboardButton("❌ Cancel", callback_data="manage_telethon_sessions")]
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def _handle_auth_input_api_id(m, ctx, text):
    api_id = text.strip()
    try:
        int(api_id)
    except ValueError:
        await m.reply_text(
            '❌ Invalid API ID. Please enter a valid numeric API ID.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='manage_telethon_sessions')]])
        )
        return
    ctx.user_data['temp_api_id'] = api_id
    ctx.user_data['awaiting_api_id'] = False
    ctx.user_data['awaiting_api_hash'] = True
    await m.reply_text(
        f'✅ API ID saved: <code>{api_id}</code>\n\n'
        f'<b>Step 2:</b> Now enter your API Hash:',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='manage_telethon_sessions')]]),
        parse_mode="HTML"
    )

async def _handle_auth_input_api_hash(m, ctx, text, session_id, session_manager):
    api_hash = text.strip()
    api_id = ctx.user_data.get('temp_api_id')
    if len(api_hash) != 32:
        await m.reply_text(
            '❌ Invalid API Hash. API Hash should be 32 characters long.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='manage_telethon_sessions')]])
        )
        return
    success = await session_manager.set_session_api_credentials(session_id, api_id, api_hash)
    if success:
        ctx.user_data.pop('awaiting_api_hash', None)
        ctx.user_data.pop('temp_api_id', None)
        ctx.user_data['awaiting_telethon_phone'] = True
        await m.reply_text(
            '✅ <b>API Credentials Set!</b>\n\n'
            '<b>Step 3:</b> Please enter your phone number with country code:\n'
            '<i>Example: +1234567890</i>',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='manage_telethon_sessions')]]),
            parse_mode="HTML"
        )
    else:
        await m.reply_text(
            f'❌ Failed to save API credentials for Session {session_id}.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='manage_telethon_sessions')]])
        )
        ctx.user_data.pop('auth_session_id', None)
        ctx.user_data.pop('awaiting_api_hash', None)
        ctx.user_data.pop('temp_api_id', None)

async def _handle_auth_input_phone(m, ctx, text, session_id, session_manager):
    phone = text.strip()
    success, result = await session_manager.authenticate_session(session_id, phone)
    if success:
        ctx.user_data['telethon_auth'] = {
            'phone': phone,
            'phone_code_hash': result,
            'session_id': session_id
        }
        ctx.user_data['awaiting_telethon_phone'] = False
        ctx.user_data['awaiting_telethon_code'] = True
        await m.reply_text(
            f'📩 Code sent to {phone}\n\nPlease enter the verification code:',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='manage_telethon_sessions')]])
        )
    else:
        await m.reply_text(
            f'❌ Failed to send code: {result}',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='manage_telethon_sessions')]])
        )

async def _handle_auth_input_code(m, ctx, text, session_id, session_manager):
    code = text.strip()
    auth_data = ctx.user_data.get('telethon_auth', {})
    success, result = await session_manager.authenticate_session(
        session_id,
        auth_data.get('phone'),
        code,
        phone_code_hash=auth_data.get('phone_code_hash')
    )
    if success:
        ctx.user_data.pop('awaiting_telethon_code', None)
        ctx.user_data.pop('telethon_auth', None)
        ctx.user_data.pop('auth_session_id', None)
        await m.reply_text(
            f'✅ Session {session_id} authenticated successfully!',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔍 View Details', callback_data=f'session_details:{session_id}')]])
        )
    elif result == "2FA_REQUIRED":
        ctx.user_data['awaiting_telethon_code'] = False
        ctx.user_data['awaiting_telethon_2fa'] = True
        ctx.user_data['telethon_auth']['code'] = code
        await m.reply_text(
            '🔒 Two-factor authentication is enabled.\n\nPlease enter your 2FA password:',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data='manage_telethon_sessions')]])
        )
    else:
        await m.reply_text(
            f'❌ Authentication failed: {result}',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='manage_telethon_sessions')]])
        )

async def _handle_auth_input_2fa(m, ctx, text, session_id, session_manager):
    password = text.strip()
    auth_data = ctx.user_data.get('telethon_auth', {})
    success, result = await session_manager.authenticate_session(
        session_id,
        auth_data.get('phone'),
        auth_data.get('code'),
        password,
        auth_data.get('phone_code_hash')
    )
    if success:
        ctx.user_data.pop('awaiting_telethon_2fa', None)
        ctx.user_data.pop('telethon_auth', None)
        ctx.user_data.pop('auth_session_id', None)
        await m.reply_text(
            f'✅ Session {session_id} authenticated with 2FA successfully!',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔍 View Details', callback_data=f'session_details:{session_id}')]])
        )
    else:
        await m.reply_text(
            f'❌ 2FA authentication failed: {result}',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='manage_telethon_sessions')]])
        )
    ctx.user_data.pop('awaiting_telethon_2fa', None)
    ctx.user_data.pop('telethon_auth', None)
    ctx.user_data.pop('auth_session_id', None)

async def handle_authenticate_telethon_sessions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    session_manager = get_session_manager()
    q = update.callback_query
    m = update.message
    if ctx.user_data is None:
        ctx.user_data = {}
    if q:
        await q.answer()
        session_id = int(q.data.split(":")[1])
        await _handle_auth_prompt(q, ctx, session_id, session_manager)
    elif m:
        text = m.text if m and m.text else None
        telegram_id = m.from_user.id if m and m.from_user else None
        account_repo = ctx.bot_data.get('account_repo')
        if not telegram_id or not account_repo or not await account_repo.is_admin(telegram_id=telegram_id):
            return
        if not text:
            return
        if text.strip().lower() == 'cancel':
            ctx.user_data.pop('auth_session_id', None)
            ctx.user_data.pop('awaiting_api_id', None)
            ctx.user_data.pop('awaiting_api_hash', None)
            ctx.user_data.pop('temp_api_id', None)
            ctx.user_data.pop('awaiting_telethon_phone', None)
            ctx.user_data.pop('awaiting_telethon_code', None)
            ctx.user_data.pop('awaiting_telethon_2fa', None)
            ctx.user_data.pop('telethon_auth', None)
            await m.reply_text(
                '🚫 Authentication cancelled.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_telethon_sessions")]])
            )
            return
        session_id = ctx.user_data.get('auth_session_id')
        if not session_id:
            return
        if ctx.user_data.get('awaiting_api_id'):
            await _handle_auth_input_api_id(m, ctx, text)
        elif ctx.user_data.get('awaiting_api_hash'):
            await _handle_auth_input_api_hash(m, ctx, text, session_id, session_manager)
        elif ctx.user_data.get('awaiting_telethon_phone'):
            await _handle_auth_input_phone(m, ctx, text, session_id, session_manager)
        elif ctx.user_data.get('awaiting_telethon_code'):
            await _handle_auth_input_code(m, ctx, text, session_id, session_manager)
        elif ctx.user_data.get('awaiting_telethon_2fa'):
            await _handle_auth_input_2fa(m, ctx, text, session_id, session_manager)

async def handle_session_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.message.from_user.id
    if not await ctx.bot_data["account_repo"].is_admin(telegram_id=telegram_id):
        await update.message.reply_text("You don't have permission to access Admin Controls.")
        return

    session_manager = get_session_manager()
    text, markup = await _render_sessions_list(None, session_manager)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")

async def handle_session_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    
    telegram_id = q.from_user.id
    if not await ctx.bot_data["account_repo"].is_admin(telegram_id=telegram_id):
        await q.answer("You don't have permission.", show_alert=True)
        return
        
    data = q.data
    if data.startswith("set_api_credentials:"):
        await handle_update_api_credentials(update, ctx)
    elif data.startswith("authenticate_session:"):
        await handle_authenticate_telethon_sessions(update, ctx)
    else:
        await manage_telethon_sessions(update, ctx)
