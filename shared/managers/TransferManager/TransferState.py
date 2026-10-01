import asyncio
from typing import Dict, Set

from shared.core.Logger import get_logger

logger = get_logger(__name__)

class TransferState:
    """Tracks active transfers states decoupled from capacity/system handling."""
    def __init__(self):
        self._active_transfers: Dict[int, Set[str]] = {}
        self._transfer_info: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()
        
    def get_lock(self):
        return self._lock
        
    def add_transfer(self, telegram_id: int, transfer_id: str, file_name: str, file_size: int = None):
        if telegram_id not in self._active_transfers:
            self._active_transfers[telegram_id] = set()
        self._active_transfers[telegram_id].add(transfer_id)
        
        self._transfer_info[transfer_id] = {
            'telegram_id': telegram_id,
            'file_name': file_name,
            'file_size': file_size,
            'status': 'started',
            'progress': 0,
            'speed': 0,
            'eta': 0
        }
    
    def remove_transfer(self, transfer_id: str):
        if transfer_id in self._transfer_info:
            telegram_id = self._transfer_info[transfer_id].get('telegram_id')
            if telegram_id and telegram_id in self._active_transfers:
                if transfer_id in self._active_transfers[telegram_id]:
                    self._active_transfers[telegram_id].remove(transfer_id)
                if not self._active_transfers[telegram_id]:
                    del self._active_transfers[telegram_id]
            del self._transfer_info[transfer_id]
            
    def get_active_count(self, telegram_id: int) -> int:
        return len(self._active_transfers.get(telegram_id, set()))

    def get_user_transfers(self, telegram_id: int) -> Set[str]:
        return self._active_transfers.get(telegram_id, set()).copy()

    def get_transfer_info(self, transfer_id: str) -> Dict:
        return self._transfer_info.get(transfer_id, {})
        
    def set_transfer_status(self, transfer_id: str, status: str, result_msg: str = None):
        if transfer_id in self._transfer_info:
            self._transfer_info[transfer_id]['status'] = status
            if result_msg:
                self._transfer_info[transfer_id]['result_message'] = result_msg
                
    def update_progress(self, transfer_id: str, current_bytes: int, total_bytes: int, message_text: str):
        if transfer_id in self._transfer_info:
            self._transfer_info[transfer_id].update({
                'progress': current_bytes,
                'file_size': total_bytes,
                'last_message': message_text
            })
