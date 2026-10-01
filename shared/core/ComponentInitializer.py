def init_shared_components(config: dict):
    """
    Wire module-level globals into all shared components.

    config keys:
        BOT_DB_PATH      — path to bot's independent database (all tables live here)
        GROUP_CHAT_ID, ACCESS_TOPIC_ID, FLAGS_TOPIC_ID, REPORTS_TOPIC_ID,
        BROADCASTS_TOPIC_ID, ALERTS_TOPIC_ID, BACKUP_TOPIC_ID, SUPER_ADMIN_ID, CIPHER
    """
    db_path = config.get('BOT_DB_PATH')

    from shared.components import TeamCloudverse
    TeamCloudverse.TeamCloudverse_GROUP_CHAT_ID = config.get('GROUP_CHAT_ID')
    TeamCloudverse.Access_TOPIC_ID = config.get('ACCESS_TOPIC_ID')
    TeamCloudverse.Flags_TOPIC_ID = config.get('FLAGS_TOPIC_ID')
    TeamCloudverse.Broadcasts_TOPIC_ID = config.get('BROADCASTS_TOPIC_ID')
    TeamCloudverse.Management_TOPIC_ID = config.get('MANAGEMENT_TOPIC_ID')
    TeamCloudverse.Alerts_TOPIC_ID = config.get('ALERTS_TOPIC_ID')
    TeamCloudverse.Bugs_TOPIC_ID = config.get('BUGS_TOPIC_ID')
    TeamCloudverse.BACKUP_TOPIC_ID = config.get('BACKUP_TOPIC_ID')

    pass

def register_shared_handlers(app, include_bin=True, include_telethon=True):
    from shared.components import Start, FileManager, Profile, Storage, Settings, Policy, TeamCloudverse, Support
    from shared.managers import DomainManager, LogManager, ServerManager
    from shared.managers.TransferManager import TransferHandlers

    modules = [
        Start, FileManager, Profile, Storage,
        Settings, Policy, TeamCloudverse, Support,
        DomainManager, LogManager, ServerManager, TransferHandlers
    ]
    
    if include_bin:
        from shared.components import RecycleBin
        modules.append(RecycleBin)
        
    if include_telethon:
        from shared.managers import SessionManager
        modules.append(SessionManager)
        
    for module in modules:
        if hasattr(module, 'register_handlers'):
            module.register_handlers(app)
