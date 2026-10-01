"""
TransferManager — shared transfer orchestration package.

Sub-modules:
  - TransferTracker: core class, lane constants, singleton
  - TransferHandlers: Telegram UI callback handlers
  - TransferWorkers: background tasks (queue admission, cleanup)

This __init__.py provides only the Dependency Injection infrastructure
(TransferServiceProvider). All other symbols are imported directly
from their respective sub-modules by consumers.
"""


class TransferServiceProvider:
    """Abstract interface that each bot implements to wire its upload/download managers."""
    def get_download_manager(self): raise NotImplementedError
    def get_upload_manager(self): raise NotImplementedError
    async def get_drive_service(self, telegram_id): raise NotImplementedError
    async def get_file_link(self, service, file_id): raise NotImplementedError
    async def get_file_metadata(self, service, file_id): raise NotImplementedError
    async def delete_file(self, service, file_id): raise NotImplementedError


_service_provider = None


def set_service_provider(provider):
    global _service_provider
    _service_provider = provider


def get_service_provider():
    if _service_provider is None:
        raise ValueError('TransferServiceProvider not configured. Call set_service_provider() at startup.')
    return _service_provider
