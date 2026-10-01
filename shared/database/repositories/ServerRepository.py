from shared.database.repositories.BaseRepository import BaseRepository

class ServerRepository(BaseRepository):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "bandwidth_usage"

    async def get_daily_bandwidth(self, date: str) -> float:
        query = f"SELECT (drive_uploaded + drive_downloaded + mega_uploaded + mega_downloaded + rclone_transferred) as total_mb FROM {self.table_name} WHERE date = ?"
        row = await self.fetch_one(query, (date,))
        return row['total_mb'] if row and row['total_mb'] is not None else 0.0

    async def update_bandwidth(self, date: str, field: str, count_field: str, active_users_field: str, mb_transferred: float, active_users_increment: int):
        query = f"""
        INSERT INTO {self.table_name} (date, {field}, {count_field}, {active_users_field}, updated_at)
        VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(date) DO UPDATE SET
            {field} = {field} + excluded.{field},
            {count_field} = {count_field} + 1,
            {active_users_field} = {active_users_field} + excluded.{active_users_field},
            updated_at = CURRENT_TIMESTAMP
        """
        await self.execute(query, (date, mb_transferred, active_users_increment))

    async def cleanup_old_bandwidth_records(self, days_to_keep: int = 30):
        query = f"DELETE FROM {self.table_name} WHERE date < date('now', '-? days')"
        await self.execute(query, (days_to_keep,))
