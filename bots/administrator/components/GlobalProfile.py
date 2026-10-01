from telegram import InlineKeyboardButton, InlineKeyboardMarkup

async def build_global_user_profile(search_term: str) -> dict:
    from bots.administrator.utils.db_utils import get_account_repo, get_usage_repo
    
    bot_contexts = ['drive', 'mega', 'rclone']
    
    global_user_info = {
        'telegram_id': None,
        'username': None,
        'name': None,
        'bots': {}
    }
    
    for bot_name in bot_contexts:
        account_repo = get_account_repo(bot_name)
        usage_repo = get_usage_repo(bot_name)
        
        user_data = None
        if search_term.startswith('@'):
            username_no_at = search_term[1:]
            results = await account_repo.get_all(filters={'username': username_no_at})
            if results:
                user_data = results[0]
        else:
            user_data = await account_repo.get(telegram_id=search_term)
            
        if user_data:
            if not global_user_info['telegram_id']:
                global_user_info['telegram_id'] = user_data['telegram_id']
                global_user_info['username'] = user_data.get('username')
                global_user_info['name'] = user_data.get('name', 'Unknown')
                
            uid = user_data['telegram_id']
            usage_data = await usage_repo.get_usage_details(str(uid))
            
            global_user_info['bots'][bot_name] = {
                'role': user_data.get('role', 'Unknown'),
                'registered': user_data.get('handled_at') or user_data.get('requested_at') or "Unknown",
                'daily_used': usage_data.get('daily_quota_used', 0) if usage_data else 0,
                'daily_limit': usage_data.get('daily_transfer_limit', 0) if usage_data else 0,
                'today_bytes': usage_data.get('today_transferred', 0) if usage_data else 0,
                'lifetime_bytes': usage_data.get('lifetime_transferred', 0) if usage_data else 0,
            }
            
    return global_user_info

async def _render_global_profile(message, ctx, user_info, query_to_edit=None):
    import humanize
    from shared.utils.role_emoji import get_role_emoji
    
    uid = user_info['telegram_id']
    username = user_info['username']
    name = user_info['name']
    
    uname_display = f"@{username}" if username else "None"
    name_display = name if name else "Unknown"
    
    text = f"👤 <b>Global User Profile</b>\n\nName: {name_display}\nUsername: {uname_display}\nTelegram ID: <code>{uid}</code>\n\n"
    
    buttons = []
    manage_bot_row = []
    
    for bot_name in ['drive', 'mega', 'rclone']:
        text += f"☁️ <b>{bot_name.capitalize()} Bot</b>\n"
        if bot_name in user_info['bots']:
            bot_data = user_info['bots'][bot_name]
            role_str = bot_data['role']
            emoji = get_role_emoji(role_str)
            
            reg_date = bot_data['registered']
            if isinstance(reg_date, str) and ' ' in reg_date:
                reg_date = reg_date.split(' ')[0]
            
            today_str = humanize.naturalsize(bot_data['today_bytes'])
            lifetime_str = humanize.naturalsize(bot_data['lifetime_bytes'])
            
            text += f"├ Role: {role_str.capitalize()}\n"
            text += f"├ Registered: {reg_date}\n"
            text += f"├ Usage Today: {bot_data['daily_used']} / {bot_data['daily_limit']} limits ({today_str})\n"
            text += f"└ Lifetime Data: {lifetime_str}\n\n"
            
            manage_bot_row.append(InlineKeyboardButton(f"{emoji} Manage {bot_name.capitalize()}", callback_data=f"search_manage:{bot_name}:{uid}"))
        else:
            text += "└ ❌ <i>Not Registered</i>\n\n"
            
    if manage_bot_row:
        for i in range(0, len(manage_bot_row), 2):
            buttons.append(manage_bot_row[i:i+2])
            
    buttons.append([InlineKeyboardButton("📊 Manage Quotas", callback_data=f"manage_user_quota:{uid}")])
    buttons.append([InlineKeyboardButton("🗑️ Delete Records", callback_data=f"delete_user_confirm:{uid}")])
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="noop")])
    
    if query_to_edit:
        await query_to_edit.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    else:
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
