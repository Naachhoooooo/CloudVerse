from datetime import datetime
from shared.database.repositories.BaseRepository import BaseRepository

class MaintenanceRepository(BaseRepository):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "cloudverse_maintenance"

    async def initialize_table(self):
        query = f"""CREATE TABLE IF NOT EXISTS {self.table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            maintenance_enabled INTEGER DEFAULT 0,
            enabled_by TEXT,
            enabled_at TIMESTAMP,
            disabled_by TEXT,
            disabled_at TIMESTAMP,
            last_alert_sent_by TEXT,
            last_alert_sent_at TIMESTAMP,
            last_alert_message TEXT,
            custom_notice TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
        await self.execute(query)
        await self.execute(f"CREATE INDEX IF NOT EXISTS idx_maintenance_enabled ON {self.table_name}(maintenance_enabled)")
        await self.execute(f"CREATE INDEX IF NOT EXISTS idx_maintenance_enabled_at ON {self.table_name}(enabled_at)")
        
        # Check if custom_notice column exists before adding
        cursor = await self.db_manager.execute_async_query(f"PRAGMA table_info({self.table_name})", fetch_all=True)
        if cursor:
            columns = [col['name'] for col in cursor] if isinstance(cursor[0], dict) else [col[1] for col in cursor]
            if 'custom_notice' not in columns:
                await self.execute(f"ALTER TABLE {self.table_name} ADD COLUMN custom_notice TEXT")
            
        row = await self.fetch_one(f"SELECT COUNT(*) as count FROM {self.table_name}")
        if row and row['count'] == 0:
            await self.execute(f"INSERT INTO {self.table_name} (maintenance_enabled, created_at) VALUES (0, ?)", (datetime.now(),))

    async def get_custom_notice(self):
        row = await self.fetch_one(f"SELECT custom_notice FROM {self.table_name} ORDER BY id DESC LIMIT 1")
        return row['custom_notice'] if row else None

    async def set_custom_notice(self, notice: str):
        query = f"UPDATE {self.table_name} SET custom_notice = ?, updated_at = ? WHERE id = (SELECT MAX(id) FROM {self.table_name})"
        await self.execute(query, (notice, datetime.now()))
        return True

    async def is_maintenance_enabled(self) -> bool:
        row = await self.fetch_one(f"SELECT maintenance_enabled FROM {self.table_name} ORDER BY id DESC LIMIT 1")
        return bool(row['maintenance_enabled']) if row else False

    async def get_history(self):
        query = f"""SELECT maintenance_enabled, enabled_by, enabled_at, disabled_by, disabled_at, 
                           last_alert_sent_by, last_alert_sent_at, last_alert_message 
                    FROM {self.table_name} ORDER BY id DESC LIMIT 1"""
        return await self.fetch_one(query)

    async def enable_maintenance(self, enabled_by_telegram_id, enabled_by_username):
        now = datetime.now()
        identifier = f"{enabled_by_telegram_id}:{enabled_by_username}"
        query_update = f"""UPDATE {self.table_name} 
                           SET maintenance_enabled = 1, enabled_by = ?, enabled_at = ?, updated_at = ? 
                           WHERE id = (SELECT MAX(id) FROM {self.table_name})"""
        await self.execute(query_update, (identifier, now, now))
        
        # We assume there's always at least one row because of initialize_table
        return True

    async def disable_maintenance(self, disabled_by_telegram_id, disabled_by_username):
        now = datetime.now()
        identifier = f"{disabled_by_telegram_id}:{disabled_by_username}"
        query = f"""UPDATE {self.table_name} 
                    SET maintenance_enabled = 0, disabled_by = ?, disabled_at = ?, updated_at = ? 
                    WHERE id = (SELECT MAX(id) FROM {self.table_name})"""
        await self.execute(query, (identifier, now, now))
        return True

    async def log_alert(self, sent_by_telegram_id, sent_by_username, message_text):
        now = datetime.now()
        identifier = f"{sent_by_telegram_id}:{sent_by_username}"
        query = f"""UPDATE {self.table_name} 
                    SET last_alert_sent_by = ?, last_alert_sent_at = ?, last_alert_message = ?, updated_at = ? 
                    WHERE id = (SELECT MAX(id) FROM {self.table_name})"""
        await self.execute(query, (identifier, now, message_text, now))
