from pathlib import Path
from shared.database.DatabaseConnectionManager import get_db_manager
from shared.database.repositories.AccountRepository import AccountRepository
from shared.database.repositories.HistoryRepository import HistoryRepository

# Assuming all DBs are located in data/databases
def get_bot_db_path(bot_name: str) -> Path:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return project_root / "data" / "databases" / f"cloudverse_{bot_name.lower()}.db"

def get_account_repo(bot_name: str) -> AccountRepository:
    db_path = get_bot_db_path(bot_name)
    return AccountRepository(str(db_path))

def get_server_db_path() -> Path:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return project_root / "data" / "databases" / "cloudverse_server.db"

def get_broadcast_repo(bot_name: str = None):
    db_path = get_server_db_path()
    from shared.database.repositories.BroadcastRepository import BroadcastRepository
    return BroadcastRepository(str(db_path), log_tag="ADMIN")

def get_history_repo(bot_name: str) -> HistoryRepository:
    db_path = get_bot_db_path(bot_name)
    return HistoryRepository(str(db_path))

def get_transfer_repo(bot_name: str):
    db_path = get_bot_db_path(bot_name)
    if bot_name.lower() == "drive":
        from shared.database.repositories.DriveTransferRepository import DriveTransferRepository
        return DriveTransferRepository(str(db_path))
    elif bot_name.lower() == "mega":
        from shared.database.repositories.MegaTransferRepository import MegaTransferRepository
        return MegaTransferRepository(str(db_path))
    elif bot_name.lower() == "rclone":
        from shared.database.repositories.RcloneTransferRepository import RcloneTransferRepository
        return RcloneTransferRepository(str(db_path))
    return None

def get_usage_repo(bot_name: str):
    db_path = get_bot_db_path(bot_name)
    from shared.database.repositories.UsageRepository import UsageRepository
    return UsageRepository(str(db_path))

def get_active_bot(ctx) -> str:
    """Returns the currently active bot context for this user, defaults to 'drive'."""
    if ctx.user_data is None:
        ctx.user_data = {}
    return ctx.user_data.get('filter_bot', 'drive').lower()

def cycle_active_bot(ctx) -> str:
    """Cycles the active bot context in the order: all -> drive -> mega -> rclone -> all."""
    current = get_active_bot(ctx)
    if current == 'all':
        nxt = 'drive'
    elif current == 'drive':
        nxt = 'mega'
    elif current == 'mega':
        nxt = 'rclone'
    else:
        nxt = 'all'
    ctx.user_data['filter_bot'] = nxt
    return nxt

def get_filter_button_text(active_bot: str) -> str:
    return f"🗃️ Database: {active_bot.capitalize()}"

async def get_users_by_role_filtered(role: str, active_bot: str) -> list:
    """Fetches users by role, supporting 'all' to fetch and deduplicate across all databases."""
    users_dict = {}
    bots_to_check = ['drive', 'mega', 'rclone'] if active_bot == 'all' else [active_bot]
    
    for b in bots_to_check:
        repo = get_account_repo(b)
        users = await repo.get_by_role(role=role)
        for u in users:
            # Attach the role emoji for the UI list
            from shared.utils.role_emoji import get_role_emoji
            u['bot_role_emoji'] = get_role_emoji(u.get('role', 'Unknown'))
            if u['telegram_id'] not in users_dict:
                users_dict[u['telegram_id']] = u
                
    return list(users_dict.values())

async def get_all_active_users_filtered(active_bot: str) -> list:
    """Fetches all users (admin, super_admin, whitelisted), supporting 'all' mode."""
    users_dict = {}
    bots_to_check = ['drive', 'mega', 'rclone'] if active_bot == 'all' else [active_bot]
    
    for b in bots_to_check:
        repo = get_account_repo(b)
        whitelisted = await repo.get_by_role(role="whitelisted")
        admins = await repo.get_by_role(role="admin")
        super_admins = await repo.get_by_role(role="super_admin")
        
        for u in (whitelisted + admins + super_admins):
            from shared.utils.role_emoji import get_role_emoji
            u['bot_role_emoji'] = get_role_emoji(u.get('role', 'Unknown'))
            if u['telegram_id'] not in users_dict:
                users_dict[u['telegram_id']] = u
                
    return list(users_dict.values())

async def get_global_user_stats() -> dict:
    """Aggregates user statistics across all active bot databases with deduplication."""
    stats = {
        "total_users": 0, "super_admins": 0, "admins": 0,
        "whitelisted": 0, "blacklisted": 0, "pending": 0,
        "registered_today": 0
    }
    
    try:
        from datetime import datetime
        all_users = {}
        for b in ['drive', 'mega', 'rclone']:
            try:
                repo = get_account_repo(b)
                users = await repo.get_all()
                for u in users:
                    all_users[u['telegram_id']] = u
            except Exception:
                pass
                
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        for u in all_users.values():
            stats["total_users"] += 1
            role = u.get('role', '')
            if role == 'super_admin': stats["super_admins"] += 1
            elif role == 'admin': stats["admins"] += 1
            elif role == 'whitelisted': stats["whitelisted"] += 1
            elif role == 'blacklisted': stats["blacklisted"] += 1
            elif role == 'pending': stats["pending"] += 1
            
            req_at = u.get('requested_at')
            if req_at and str(req_at).startswith(today_str):
                stats["registered_today"] += 1
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Failed to get global stats: {e}")
        
    return stats

async def get_all_active_transfers() -> list:
    """Fetches all active transfers across all bot databases."""
    active_transfers = []
    for bot_name in ['drive', 'mega', 'rclone']:
        try:
            repo = get_transfer_repo(bot_name)
            if not repo: continue
            
            # Use raw query since BaseFileTransferRepository doesn't have get_active directly
            query = f"SELECT * FROM {repo.table_name} WHERE status IN ('queued', 'downloading', 'processing', 'uploading')"
            transfers = await repo.fetch_all(query, [])
            for t in transfers:
                t['bot_provider'] = bot_name
                active_transfers.append(t)
        except Exception:
            pass
    return active_transfers

async def signal_kill_transfer(bot_name: str, transfer_id: int):
    """Sets transfer status to 'cancelled' in DB so the bot process will pick it up and abort."""
    try:
        repo = get_transfer_repo(bot_name)
        if repo:
            query = f"UPDATE {repo.table_name} SET status = 'cancelled', error_message = 'Cancelled by administrator' WHERE id = ?"
            await repo.execute(query, (transfer_id,))
            return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to kill transfer {transfer_id} in {bot_name}: {e}")
    return False
