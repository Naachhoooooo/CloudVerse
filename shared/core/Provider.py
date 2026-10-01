from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Tuple, Any
from shared.core.Logger import get_logger

logger = get_logger(__name__)

class ProviderInterface(ABC):
    """
    Abstract Base Class for all CloudVerse bot providers (Drive, Mega, Rclone).
    Defines the contract for file management, authentication, and storage info.
    """
    
    @abstractmethod
    async def get_credentials(self, telegram_id: int, account_email: Optional[str] = None) -> Optional[Dict]:
        """Retrieve credentials for the user."""

    @abstractmethod
    async def set_credentials(self, telegram_id: int, account_email: str, credentials_dict: dict):
        """Store credentials for the user."""

    @abstractmethod
    async def remove_credentials(self, telegram_id: int, account_email: Optional[str] = None):
        """Remove credentials for the user."""

    @abstractmethod
    async def get_service(self, telegram_id: int, account_email: Optional[str] = None) -> Any:
        """Get the authenticated service client for the provider."""

    @abstractmethod
    async def get_folder_name(self, service: Any, folder_id: str) -> str:
        """Get the name of a folder."""

    @abstractmethod
    async def list_files(self, service: Any, folder_id: str = "root", page_token: Optional[str] = None, page_size: int = 10) -> Tuple[List[Dict], Optional[str]]:
        """List files in a directory. Returns a tuple of (files, next_page_token)."""

    @abstractmethod
    async def list_trashed_files(self, service: Any, page_token: Optional[str] = None, page_size: int = 10) -> Tuple[List[Dict], Optional[str]]:
        """List files in the trash."""

    @abstractmethod
    async def create_folder(self, service: Any, name: str, parent_id: Optional[str] = None) -> Any:
        """Create a new folder."""

    @abstractmethod
    async def rename_file(self, service: Any, file_id: str, new_name: str) -> Any:
        """Rename a file or folder."""

    @abstractmethod
    async def delete_file(self, service: Any, file_id: str) -> bool:
        """Move a file to trash."""

    @abstractmethod
    async def toggle_sharing(self, service: Any, file_id: str) -> None:
        """Toggle the sharing status of a file."""

    @abstractmethod
    async def get_file_link(self, service: Any, file_id: str) -> str:
        """Get the web view link for a file."""

    @abstractmethod
    async def get_storage_info(self, service: Any) -> Dict:
        """Get storage quota information."""

    @abstractmethod
    async def get_file_metadata(self, service: Any, file_id: str) -> Dict:
        """Fetch metadata for a file or folder."""

    @abstractmethod
    async def get_user_info(self, service: Any) -> Dict:
        """Get user profile information."""

    @abstractmethod
    async def restore_file(self, service: Any, file_id: str) -> Any:
        """Restore a file from the trash."""

    @abstractmethod
    async def empty_trash(self, service: Any) -> bool:
        """Empty the trash."""

    @abstractmethod
    async def get_item_size(self, service: Any, item_id: str, is_folder: bool = False) -> int:
        """Get the size of an item in bytes. Return 0 if unknown."""

    @abstractmethod
    async def do_login(self, update: Any, ctx: Any) -> None:
        """
        Handle the full login flow for this provider.
        """

    @abstractmethod
    async def do_logout(self, update: Any, ctx: Any) -> None:
        """
        Handle the full logout flow for this provider.
        """

class ProviderFactory:
    _providers = {}

    @classmethod
    def register_provider(cls, name: str, provider_instance):
        cls._providers[name] = provider_instance
        logger.debug(f"Registered provider: {name}")

    @classmethod
    def get_provider(cls, name: str):
        if name not in cls._providers:
            raise ValueError(f"Provider {name} is not registered.")
        return cls._providers[name]
