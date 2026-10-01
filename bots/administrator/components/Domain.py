from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger

logger = get_logger(__name__)

from shared.managers.DomainManager import (
    _read_domains,
    add_allowed_domain,
    remove_allowed_domain,
)

async def handle_domain_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    
    # Check admin privileges
    telegram_id = update.message.from_user.id
    if not await ctx.bot_data["account_repo"].is_admin(telegram_id=telegram_id):
        await update.message.reply_text("You don't have permission to access Admin Controls.")
        return

    page = ctx.user_data.get("allowed_domain_page", 0)
    q = None
    
    # Send initial render via message, not query edit
    domains = _read_domains()
    from shared.utils.pagination import Paginator
    paginator = Paginator(domains, page, 10)
    page_domains = paginator.items
    pagination_buttons = paginator.get_buttons("allowed_domain_prev_page", "allowed_domain_next_page")
    
    text = f"🟢 <b>Modify Link Domains</b>\n\n<b>Allowed Domains ({len(domains)} total):</b>\n\n"
    for i, domain in enumerate(page_domains, paginator.start_idx + 1):
        text += f"{i}. <code>{domain}</code>\n"
    if not domains:
        text += "<i>No domains configured</i>\n"

    buttons = []
    if pagination_buttons:
        buttons.append(pagination_buttons)
    buttons.extend([
        [InlineKeyboardButton("➕ Add Domain", callback_data="add_allowed_domain")],
        [InlineKeyboardButton("🗑️ Remove Domain", callback_data="show_remove_domains")],
        [InlineKeyboardButton("Refresh", callback_data="manage_allowed_domains")]
    ])
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def handle_domain_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    telegram_id = q.from_user.id
    if not await ctx.bot_data["account_repo"].is_admin(telegram_id=telegram_id):
        await q.edit_message_text("You don't have permission to access Admin Controls.")
        return

    data = q.data
    page = ctx.user_data.get("allowed_domain_page", 0)
    
    if data == "manage_allowed_domains":
        # Modified local render without 'Back'
        domains = _read_domains()
        from shared.utils.pagination import Paginator
        paginator = Paginator(domains, page, 10)
        page_domains = paginator.items
        pagination_buttons = paginator.get_buttons("allowed_domain_prev_page", "allowed_domain_next_page")
        
        text = f"🟢 <b>Modify Link Domains</b>\n\n<b>Allowed Domains ({len(domains)} total):</b>\n\n"
        for i, domain in enumerate(page_domains, paginator.start_idx + 1):
            text += f"{i}. <code>{domain}</code>\n"
        if not domains:
            text += "<i>No domains configured</i>\n"

        buttons = []
        if pagination_buttons:
            buttons.append(pagination_buttons)
        buttons.extend([
            [InlineKeyboardButton("➕ Add Domain", callback_data="add_allowed_domain")],
            [InlineKeyboardButton("🗑️ Remove Domain", callback_data="show_remove_domains")],
            [InlineKeyboardButton("Refresh", callback_data="manage_allowed_domains")]
        ])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    elif data == "allowed_domain_prev_page":
        ctx.user_data["allowed_domain_page"] = max(0, page - 1)
        q.data = "manage_allowed_domains"
        await handle_domain_callback(update, ctx)
    elif data == "allowed_domain_next_page":
        ctx.user_data["allowed_domain_page"] = page + 1
        q.data = "manage_allowed_domains"
        await handle_domain_callback(update, ctx)
    elif data == "show_remove_domains":
        domains = _read_domains()
        if not domains:
            await q.edit_message_text(
                "❌ <b>No domains to remove</b>\n\nThere are currently no domains in the allowed list.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_allowed_domains")]]),
                parse_mode="HTML",
            )
            return
        text = "🗑️ <b>Remove Domain</b>\n\nSelect a domain to remove:\n\n"
        for i, domain in enumerate(domains, 1):
            text += f"{i}. {domain}\n"
        buttons = [[InlineKeyboardButton(f"🗑️ {d}", callback_data=f"confirm_remove_domain:{d}")] for d in domains]
        buttons.append([InlineKeyboardButton("Back", callback_data="manage_allowed_domains")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    elif data and data.startswith("confirm_remove_domain:"):
        domain = data.split(":", 1)[1]
        text = (
            f"🗑️ <b>Confirm Domain Removal</b>\n\n"
            f"Are you sure you want to remove the domain:\n"
            f"<code>{domain}</code>\n\n"
            f"This action cannot be undone."
        )
        buttons = [
            [InlineKeyboardButton("✅ Yes, Remove", callback_data=f"remove_domain_confirmed:{domain}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="show_remove_domains")],
            [InlineKeyboardButton("Back", callback_data="manage_allowed_domains")],
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    elif data and data.startswith("remove_domain_confirmed:"):
        domain = data.split(":", 1)[1]
        if remove_allowed_domain(domain):
            logger.info(f"[BOT] Domain removed: {domain}")
            text = f"✅ <b>Domain Removed Successfully</b>\n\nThe domain <code>{domain}</code> has been removed from the allowed list."
            buttons = [
                [InlineKeyboardButton("🗑️ Remove Another", callback_data="show_remove_domains")],
                [InlineKeyboardButton("Back to Domains", callback_data="manage_allowed_domains")],
            ]
        else:
            logger.warning(f"[BOT] Tried to remove non-existent domain: {domain}")
            text = f"❌ <b>Domain Not Found</b>\n\nThe domain <code>{domain}</code> was not found in the allowed list."
            buttons = [[InlineKeyboardButton("Back", callback_data="manage_allowed_domains")]]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    elif data == "add_allowed_domain":
        ctx.user_data["awaiting_new_domain"] = True
        await q.edit_message_text(
            "➕ <b>Add New Domain</b>\n\nSend the new domain as a message.\n\n<i>Example: youtube.com</i>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="manage_allowed_domains")]]),
            parse_mode="HTML",
        )

async def handle_new_domain_input(m, ctx):
    new_domain = m.text.strip().lower()

    if not new_domain or " " in new_domain or "." not in new_domain:
        await m.reply_text(
            "❌ <b>Invalid Domain Format</b>\n\nPlease enter a valid domain (e.g., youtube.com)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_allowed_domains")]]),
            parse_mode="HTML",
        )
        ctx.user_data["awaiting_new_domain"] = False
        return

    if add_allowed_domain(new_domain):
        logger.info(f"[BOT] Domain added: {new_domain}")
        await m.reply_text(
            f"✅ <b>Domain Added Successfully</b>\n\nThe domain <code>{new_domain}</code> has been added to the allowed list.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Another", callback_data="add_allowed_domain")],
                [InlineKeyboardButton("Back to Domains", callback_data="manage_allowed_domains")],
            ]),
            parse_mode="HTML",
        )
    else:
        logger.warning(f"[BOT] Duplicate domain add attempt: {new_domain}")
        await m.reply_text(
            f"⚠️ <b>Domain Already Exists</b>\n\nThe domain <code>{new_domain}</code> is already in the allowed list.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_allowed_domains")]]),
            parse_mode="HTML",
        )
    ctx.user_data["awaiting_new_domain"] = False
