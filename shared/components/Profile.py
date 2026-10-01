from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
# credential_management removed - using ctx.bot_data['credential_repo']
from datetime import datetime, timedelta
import humanize
from shared.core.Logger import get_logger
from shared.managers.AccessManager import access_required

logger = get_logger(__name__)

def generate_progress_bar(used, total, length=10):
    if total == 0:
        percentage = 0
    else:
        percentage = (used / total) * 100
    filled_squares = int((used / total) * length) if total > 0 else 0
    empty_squares = length - filled_squares
    # Improved with colored emojis for visual appeal
    progress_bar = "🟢" * filled_squares + "⚪" * empty_squares
    return progress_bar, percentage

def calculate_time_until_reset():
    now = datetime.now()
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    time_diff = tomorrow - now
    hours = time_diff.seconds // 3600
    minutes = (time_diff.seconds % 3600) // 60
    seconds = time_diff.seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def format_quota_info(quota_info, is_user_admin=False):
    if is_user_admin:
        return (
            f"🔋 <b>Daily Upload Quota</b>\n\n"
            f"Unlimited Uploads\n\n"
            f"<i>No daily limits for administrators</i>\n"
        )
    used = quota_info['daily_used']
    total = quota_info['daily_limit']
    if total == 0:
        return (
            f"🔋 <b>Daily Upload Quota</b>\n\n"
            f"Used quota: {used} | Unlimited\n\n"
            f"<i>Unlimited uploads granted</i>\n"
        )
    balance = total - used
    progress_bar, percentage = generate_progress_bar(used, total)
    time_until_reset = calculate_time_until_reset()
    return (
        f"🔋 <b>Daily Upload Quota</b>\n\n"
        f"Used {used} of {total}, Balance {balance}\n\n"
        f"{progress_bar} ({percentage:.1f}%)\n\n"
        f"Your quota will reset in {time_until_reset}.\n"
    )

async def get_user_role(telegram_id, ctx):
    account_repo = ctx.bot_data['account_repo']
    if await account_repo.is_super_admin(telegram_id=telegram_id):
        return "👑 Super Admin"
    elif await account_repo.is_admin(telegram_id=telegram_id):
        return "⚡ Admin"
    elif await account_repo.is_whitelisted(telegram_id=telegram_id):
        return "✅ Whitelisted"
    elif await account_repo.is_blacklisted(telegram_id=telegram_id):
        return "🚫 Blacklisted"
    else:
        account = await account_repo.get(telegram_id=telegram_id)
        if account:
            role = account.get('role', '')
            if role == 'pending':
                return "⏳ Pending"
            elif role == 'rejected':
                return "❌ Rejected"
    return "👤 Guest"

def emails_profile(user_info, username, telegram_id, role, emails, primary_email, default_folder_name, parallel_uploads, monthly_bandwidth_bytes, overall_bandwidth_bytes, daily_bandwidth_bytes, weekly_bandwidth_bytes, account_creation_date, quota_info_text):
    monthly_bandwidth_str = humanize.naturalsize(monthly_bandwidth_bytes, binary=True) if monthly_bandwidth_bytes > 0 else "0 B"
    lifetime_bandwidth_str = humanize.naturalsize(overall_bandwidth_bytes, binary=True) if overall_bandwidth_bytes > 0 else "0 B"
    daily_bandwidth_str = humanize.naturalsize(daily_bandwidth_bytes, binary=True) if daily_bandwidth_bytes > 0 else "0 B"
    weekly_bandwidth_str = humanize.naturalsize(weekly_bandwidth_bytes, binary=True) if weekly_bandwidth_bytes > 0 else "0 B"
  
    # Format account creation date
    creation_date_str = "N/A"
    if account_creation_date:
        try:
            if isinstance(account_creation_date, str):
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d']:
                    try:
                        parsed_date = datetime.strptime(account_creation_date, fmt)
                        creation_date_str = parsed_date.strftime("%B %d, %Y")
                        break
                    except ValueError:
                        continue
            else:
                creation_date_str = account_creation_date.strftime("%B %d, %Y")
        except Exception:
            creation_date_str = str(account_creation_date)
  
    if default_folder_name.lower() in ("mega root", "google drive root", "root", "/"):
        default_folder_name = "Default"

    status_str = "Linked" if primary_email != "Not Linked" else "Not Linked"
    account_line = f"Account: <code>{primary_email}</code>\n\n" if status_str == "Linked" else "\n"

    return (
        f"⛉ <b>Profile</b>\n\n"
        f"Username: <code>@{username}</code>\n"
        f"Telegram ID: <code>{telegram_id}</code>\n"
        f"Role: {role}\n\n"
        f"Registration: <code>{creation_date_str}</code>\n\n"
        f"Status: {status_str}\n"
        f"{account_line}"
        f"Upload Location: <code>{default_folder_name}</code>\n"
        f"Parallel Transfer: {parallel_uploads}\n\n"
        f"⚡ <b>Usage Statistics</b>\n\n"
        f"Usage Today: {daily_bandwidth_str}\n\n"
        f"Last Week Usage: {weekly_bandwidth_str}\n"
        f"Last Month Usage: {monthly_bandwidth_str}\n"
        f"Lifetime Usage: {lifetime_bandwidth_str}\n\n"
        f"{quota_info_text}"
    )

@access_required
async def handle_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if update.callback_query and update.callback_query.from_user:
            await update.callback_query.answer()
            telegram_id = update.callback_query.from_user.id
            username = update.callback_query.from_user.username or 'N/A'
        elif update.message and update.message.from_user:
            telegram_id = update.message.from_user.id
            username = update.message.from_user.username or 'N/A'
        else:
            return
      
        # Get role
        role = await get_user_role(telegram_id, ctx)
      
        provider = ctx.bot_data['provider']
        service = await provider.get_service(telegram_id)
        
        # Default unauthenticated values
        google_user_info = None
        parallel_uploads = 1
        default_folder_name = "Not Linked"
        primary_email = {"email": "Not Linked"}
        
        if service:
            try:
                user_info_res = await provider.get_user_info(service)
                google_user_info = user_info_res["user"]
                
                # Get credentials info using credential_repo
                cred = await ctx.bot_data['credential_repo'].get(telegram_id=telegram_id)
                if cred and 'email_address' in cred:
                    primary_email = {'email': cred['email_address']}
                
                parallel_uploads = await ctx.bot_data['credential_repo'].get_parallel_uploads(telegram_id=telegram_id)
                default_folder_id = await ctx.bot_data['credential_repo'].get_default_location(telegram_id=telegram_id)
                default_folder_name = await provider.get_folder_name(service, default_folder_id)
            except Exception as e:
                provider_name = ctx.bot_data.get('provider_name', 'drive').capitalize()
                logger.error(f"Error fetching {provider_name} data for user {telegram_id}: {e}")
      
        # Get bandwidth using quota_manager
        from shared.managers.QuotaManager import get_quota_manager
        quota_manager = get_quota_manager()
        
        monthly_bandwidth_bytes = await quota_manager.get_transferred_bytes(telegram_id=telegram_id, period='month')
        overall_bandwidth_bytes = await quota_manager.get_transferred_bytes(telegram_id=telegram_id, period='lifetime')
        daily_bandwidth_bytes = await quota_manager.get_transferred_bytes(telegram_id=telegram_id, period='day')
        weekly_bandwidth_bytes = await quota_manager.get_transferred_bytes(telegram_id=telegram_id, period='week')
      
        # Get creation date
        account_creation_date = await ctx.bot_data['account_repo'].first_registered(telegram_id=telegram_id)
      
        # Get quota info
        quota_limit = await quota_manager.get_limit(telegram_id=telegram_id)
        quota_used = await quota_manager.get_usage(telegram_id=telegram_id)
        quota_info = {'daily_used': quota_used, 'daily_limit': quota_limit or 0}
        is_user_admin = await ctx.bot_data['account_repo'].is_admin(telegram_id=telegram_id) or await ctx.bot_data['account_repo'].is_super_admin(telegram_id=telegram_id)
        quota_info_text = format_quota_info(quota_info, is_user_admin)
      
        # Primary email is statically set or resolved above
        primary_email_str = primary_email.get('email', 'Not Linked')
        if primary_email_str != 'Not Linked' and primary_email_str:
            primary_email_str = primary_email_str[0].upper() + primary_email_str[1:]
      
        text = emails_profile(
            google_user_info,
            username,
            telegram_id,
            role,  # Pass role to the function
            None, # Legacy emails array parameter
            primary_email_str,
            default_folder_name,
            parallel_uploads,
            monthly_bandwidth_bytes or 0,
            overall_bandwidth_bytes or 0,
            daily_bandwidth_bytes or 0,
            weekly_bandwidth_bytes or 0,
            account_creation_date,
            quota_info_text
        )
      
        buttons = [
            [InlineKeyboardButton("Refresh", callback_data="refresh_profile")]
        ]
        markup = InlineKeyboardMarkup(buttons)
      
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
        elif update.message:
            await update.message.reply_text(text, reply_markup=markup, parse_mode='HTML')
          
    except Exception as e:
        logger.error(f"Error in handle_profile for user: {locals().get('telegram_id', 'unknown')}: {e}")
        error_msg = "Failed to load profile. Please try again later."
        if update.message:
            await update.message.reply_text(error_msg)
        elif update.callback_query:
            await update.callback_query.edit_message_text(error_msg)

@access_required
async def handle_refresh_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer("Refreshing profile...")
        await handle_profile(update, ctx)

def register_handlers(app):
    from telegram.ext import CommandHandler, CallbackQueryHandler
    app.add_handler(CommandHandler("profile", handle_profile))
    app.add_handler(CallbackQueryHandler(handle_profile, pattern=r"^PROFILE$"))
    app.add_handler(CallbackQueryHandler(handle_refresh_profile, pattern=r"^refresh_profile$"))