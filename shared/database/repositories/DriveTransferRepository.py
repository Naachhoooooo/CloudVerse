"""
DriveTransferRepository — manages cloudverse_transfers in drive.db.
"""
from shared.database.repositories.BaseFileTransferRepository import BaseFileTransferRepository


class DriveTransferRepository(BaseFileTransferRepository):
    def __init__(self, db_path: str):
        super().__init__(db_path, log_tag="DRIVE")
