"""
Shared database utilities and schema initialization.

This module provides the shared executor helper used by repositories
and the common schema initialization for tables shared across all bots
(accounts, usage, history) and server tables (broadcasts, sessions, support).
"""
import asyncio
import sqlite3
from functools import partial
from shared.core.Logger import get_logger
logger = get_logger(__name__)


def init_shared_tables(conn: sqlite3.Connection) -> None:
    """Initialize bot-specific shared tables: accounts, usage, and history."""
    cursor = conn.cursor()

    cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_accounts (
        telegram_id TEXT PRIMARY KEY,
        username TEXT UNIQUE,
        name TEXT,
        language_code TEXT,
        is_premium INTEGER DEFAULT 0,
        role TEXT CHECK (role IN ('pending', 'whitelisted', 'blacklisted', 'rejected', 'admin', 'super_admin')),
        is_protected INTEGER DEFAULT 0,
        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        request_message_id TEXT,
        handled_by TEXT,
        handled_at TIMESTAMP,
        access_type TEXT CHECK (access_type IN ('limited', 'permanent')),
        restriction_type TEXT CHECK (restriction_type IN ('temporary', 'permanent')),
        period INTEGER,
        action_by TEXT,
        action_at TIMESTAMP,
        last_active TIMESTAMP,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_account_role ON cloudverse_accounts(role)")

    cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_usage (
        telegram_id TEXT PRIMARY KEY REFERENCES cloudverse_accounts(telegram_id) ON DELETE CASCADE,
        username TEXT UNIQUE,
        daily_transfer_limit INTEGER DEFAULT 5,
        daily_quota_used INTEGER DEFAULT 0,
        transferred_today BIGINT DEFAULT 0,
        transfer_count_today INTEGER DEFAULT 0,
        transferred_this_week BIGINT DEFAULT 0,
        transfer_count_this_week INTEGER DEFAULT 0,
        transferred_this_month BIGINT DEFAULT 0,
        transfer_count_this_month INTEGER DEFAULT 0,
        transferred_this_year BIGINT DEFAULT 0,
        transfer_count_this_year INTEGER DEFAULT 0,
        transferred_lifetime BIGINT DEFAULT 0,
        transfer_count_lifetime INTEGER DEFAULT 0,
        last_reset TIMESTAMP,
        last_transfer TIMESTAMP,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT REFERENCES cloudverse_accounts(telegram_id),
        username TEXT,
        user_role TEXT,
        user_role_after TEXT,
        action_taken TEXT,
        status TEXT,
        admin_telegram_id TEXT,
        admin_username TEXT,
        admin_role TEXT,
        related_message_id TEXT,
        event_details TEXT,
        notes TEXT,
        event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_user_action ON cloudverse_history(telegram_id, action_taken)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_event_time ON cloudverse_history(event_time)")


    cursor.execute("""CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id TEXT REFERENCES cloudverse_accounts(telegram_id),
        topic_id INTEGER,
        status TEXT DEFAULT 'open',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_tid ON support_tickets(telegram_id)")

    cursor.execute("""CREATE TABLE IF NOT EXISTS support_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER REFERENCES support_tickets(id),
        interaction_type TEXT,
        message_text TEXT,
        action_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_support_history_ticket ON support_history(ticket_id)")

    conn.commit()


def server_init_db(db_path: str) -> None:
    """Initialize the central cloudverse_server.db with global tables."""
    logger.info(f"[SERVER][SYSTEM] Initializing server.db at {db_path}")
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()


            cursor.execute("""CREATE TABLE IF NOT EXISTS bandwidth_usage (
                date TEXT PRIMARY KEY,
                drive_uploaded REAL DEFAULT 0.0,
                drive_downloaded REAL DEFAULT 0.0,
                mega_uploaded REAL DEFAULT 0.0,
                mega_downloaded REAL DEFAULT 0.0,
                rclone_transferred REAL DEFAULT 0.0,
                drive_active_users INTEGER DEFAULT 0,
                mega_active_users INTEGER DEFAULT 0,
                rclone_active_users INTEGER DEFAULT 0,
                drive_transfers INTEGER DEFAULT 0,
                mega_transfers INTEGER DEFAULT 0,
                rclone_transfers INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_broadcasts (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_telegram_id TEXT,
                requester_username TEXT,
                group_message_id TEXT,
                message_text TEXT,
                media_type TEXT,
                media_file_id TEXT,
                target_audience TEXT,
                approval_status TEXT CHECK (approval_status IN ('pending', 'approved', 'rejected', 'completed', 'failed', 'cancelled')) DEFAULT 'pending',
                approved_by TEXT,
                approved_at TIMESTAMP,
                target_count INTEGER,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_status ON cloudverse_broadcasts(approval_status)")

            cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name TEXT UNIQUE,
                phone_number TEXT,
                error_count INTEGER DEFAULT 0,
                health_status TEXT CHECK (health_status IN ('active', 'expired', 'invalid')) DEFAULT 'active',
                session_file_path TEXT,
                api_id TEXT,
                api_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT,
                last_validated TIMESTAMP,
                last_used TIMESTAMP,
                used INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_health_status ON cloudverse_sessions(health_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON cloudverse_sessions(created_at)")

            cursor.execute("""CREATE TABLE IF NOT EXISTS support_users (
                telegram_id INTEGER PRIMARY KEY,
                topic_id INTEGER,
        status TEXT DEFAULT 'open',
                source_system TEXT,
                drive_member INTEGER DEFAULT NULL,
                mega_member INTEGER DEFAULT NULL,
                rclone_member INTEGER DEFAULT NULL,
                is_banned INTEGER DEFAULT 0,
                authorized_at TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_support_users_topic_id ON support_users(topic_id)")

            cursor.execute("""CREATE TABLE IF NOT EXISTS support_tickets (
                ticket_code TEXT PRIMARY KEY,
                telegram_id TEXT,
                topic_id INTEGER,
                bot_source TEXT,
                status TEXT DEFAULT 'OPEN',
                note TEXT,
                admin_details TEXT,
                admin_replied INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON support_tickets(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_tid ON support_tickets(telegram_id)")

            cursor.execute("""CREATE TABLE IF NOT EXISTS support_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_code TEXT REFERENCES support_tickets(ticket_code),
                interaction_type TEXT,
                bot_source TEXT,
                message_id TEXT,
                sender_id INTEGER,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_support_history_ticket ON support_history(ticket_code)")

            conn.commit()
            logger.info("[SERVER][SYSTEM] server.db initialized successfully")
    except Exception as e:
        logger.critical(f"[SERVER][SYSTEM] Failed to initialize server.db: {e}", exc_info=True)
        raise


def initialize_database_indexes(conn: sqlite3.Connection) -> None:
    """Initialize performance-critical database indexes for bot databases."""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_transfers_user_status ON cloudverse_transfers(telegram_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_transfers_status_completed ON cloudverse_transfers(status, completed)",
        "CREATE INDEX IF NOT EXISTS idx_transfers_worker ON cloudverse_transfers(worker_id)",
        "CREATE INDEX IF NOT EXISTS idx_usage_transferred_today ON cloudverse_usage(transferred_today)",
        "CREATE INDEX IF NOT EXISTS idx_usage_telegram_id ON cloudverse_usage(telegram_id)",
        "CREATE INDEX IF NOT EXISTS idx_accounts_role_updated ON cloudverse_accounts(role, last_updated)",
        "CREATE INDEX IF NOT EXISTS idx_history_admin_date ON cloudverse_history(admin_telegram_id, event_time)"
    ]
    cursor = conn.cursor()
    for index_query in indexes:
        try:
            table_name = index_query.split(' ON ')[1].split('(')[0].strip()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            table_exists = cursor.fetchone()
            
            if table_exists:
                cursor.execute(index_query)
                logger.info(f"Created index: {index_query.split('idx_')[1].split(' ')[0]}")
            else:
                logger.debug(f"Skipping index (table {table_name} not present)")
        except Exception as e:
            logger.error(f"Failed to create index: {e}")
            
    conn.commit()


def drive_init_db(db_path: str, super_admin_id: str = None) -> None:
    """Initialize cloudverse_drive.db with all tables."""
    logger.info(f"[DRIVE][SYSTEM] Initializing drive.db at {db_path}")
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()

            init_shared_tables(conn)

            cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_credentials (
                telegram_id TEXT PRIMARY KEY,
                username TEXT,
                email_address TEXT,
                drive_credential TEXT,
                default_upload_location TEXT DEFAULT 'root',
                parallel_uploads INTEGER DEFAULT 1,
                is_valid INTEGER DEFAULT 1,
                last_used TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                username TEXT,
                worker_id TEXT,
                file_id TEXT,
                file_name TEXT,
                file_type TEXT,
                file_size BIGINT,
                status TEXT CHECK (status IN ('queued', 'downloading', 'processing', 'uploading', 'completed', 'failed', 'cancelled')),
                error_message TEXT,
                method TEXT,
                source_url TEXT,
                session_used TEXT,
                average_download_speed REAL,
                download_duration REAL,
                average_upload_speed REAL,
                upload_duration REAL,
                transfer_source TEXT,
                bytes_transferred BIGINT DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                started TIMESTAMP,
                completed TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            cursor.execute("PRAGMA optimize")
            conn.commit()
            
            old_isolation = conn.isolation_level
            conn.isolation_level = None
            try:
                cursor.execute("VACUUM")
            except Exception as v_err:
                logger.warning(f"[DRIVE] VACUUM failed: {v_err}")
            finally:
                conn.isolation_level = old_isolation
            
            initialize_database_indexes(conn)
            logger.info("[DRIVE][SYSTEM] drive.db initialized successfully - all tables created")
    except Exception as e:
        logger.critical(f"[DRIVE][SYSTEM] Failed to initialize drive.db: {e}", exc_info=True)
        raise

def mega_init_db(db_path: str, super_admin_id: str = None) -> None:
    """Initialize cloudverse_mega.db with all tables."""
    logger.info(f"[MEGA][SYSTEM] Initializing mega.db at {db_path}")
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()

            init_shared_tables(conn)

            cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_credentials (
                telegram_id TEXT PRIMARY KEY,
                username TEXT,
                email_address TEXT,
                mega_credential TEXT,
                default_upload_location TEXT DEFAULT 'root',
                parallel_uploads INTEGER DEFAULT 1,
                is_valid INTEGER DEFAULT 1,
                last_used TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                username TEXT,
                worker_id TEXT,
                file_id TEXT,
                file_name TEXT,
                file_type TEXT,
                file_size BIGINT,
                status TEXT CHECK (status IN ('queued', 'downloading', 'processing', 'uploading', 'completed', 'failed', 'cancelled')),
                error_message TEXT,
                method TEXT,
                source_url TEXT,
                session_used TEXT,
                average_download_speed REAL,
                download_duration REAL,
                average_upload_speed REAL,
                upload_duration REAL,
                transfer_source TEXT,
                bytes_transferred BIGINT DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                started TIMESTAMP,
                completed TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            cursor.execute("PRAGMA optimize")
            conn.commit()
            
            old_isolation = conn.isolation_level
            conn.isolation_level = None
            try:
                cursor.execute("VACUUM")
            except Exception as v_err:
                logger.warning(f"[MEGA] VACUUM failed: {v_err}")
            finally:
                conn.isolation_level = old_isolation
            
            initialize_database_indexes(conn)
            logger.info("[MEGA][SYSTEM] mega.db initialized successfully - all tables created")
    except Exception as e:
        logger.critical(f"[MEGA][SYSTEM] Failed to initialize mega.db: {e}", exc_info=True)
        raise

def rclone_init_db(db_path: str, super_admin_id: str = None) -> None:
    """Initialize cloudverse_rclone.db with all tables."""
    logger.info(f"[RCLONE][SYSTEM] Initializing rclone.db at {db_path}")
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()

            init_shared_tables(conn)

            cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_credentials (
                telegram_id TEXT PRIMARY KEY,
                username TEXT,
                rclone_credential TEXT,
                parallel_transfers INTEGER DEFAULT 1,
                is_valid INTEGER DEFAULT 1,
                last_used TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            cursor.execute("""CREATE TABLE IF NOT EXISTS cloudverse_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                username TEXT,
                worker_id TEXT,
                job_id TEXT,
                file_count INTEGER,
                transfer_size BIGINT,
                status TEXT CHECK (status IN ('queued', 'downloading', 'processing', 'uploading', 'completed', 'failed', 'cancelled')),
                error_message TEXT,
                source_remote TEXT,
                destination_remote TEXT,
                average_transfer_speed REAL,
                transfer_duration REAL,
                bytes_transferred BIGINT DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                started TIMESTAMP,
                completed TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rclone_tx_job ON cloudverse_transfers(job_id)")

            cursor.execute("PRAGMA optimize")
            conn.commit()
            
            old_isolation = conn.isolation_level
            conn.isolation_level = None
            try:
                cursor.execute("VACUUM")
            except Exception as v_err:
                logger.warning(f"[RCLONE] VACUUM failed: {v_err}")
            finally:
                conn.isolation_level = old_isolation
            
            initialize_database_indexes(conn)
            logger.info("[RCLONE][SYSTEM] rclone.db initialized successfully - all tables created")
    except Exception as e:
        logger.critical(f"[RCLONE][SYSTEM] Failed to initialize rclone.db: {e}", exc_info=True)
        raise
