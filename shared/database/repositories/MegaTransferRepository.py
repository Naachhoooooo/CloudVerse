"""
MegaTransferRepository — manages cloudverse_transfers in mega.db.
"""
from shared.database.repositories.BaseFileTransferRepository import BaseFileTransferRepository


class MegaTransferRepository(BaseFileTransferRepository):
    def __init__(self, db_path: str):
        super().__init__(db_path, log_tag="MEGA")
