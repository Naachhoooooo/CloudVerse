import os
import mimetypes
import asyncio
import gc
import uuid
from datetime import datetime
from typing import Optional, Callable, Any, Dict
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from googleapiclient.errors import HttpError
from io import BytesIO

from bots.drive.services.DriveService import get_drive_service, get_storage_info
from shared.managers.ServerManager import get_server_manager
from shared.managers.TransferManager.AdaptiveChunker import get_upload_adaptive_chunk_size, adjust_upload_chunk_size
import humanize
from shared.core.Logger import get_logger
from shared.core.CircuitBreaker import CircuitBreaker
from shared.core.UserState import UserStateEnum
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)


class UploadManager:
    """Centralized upload manager for all Google Drive upload operations."""
    
    def __init__(self):
        self.active_uploads: Dict[str, Dict] = {}
        self.upload_stats = {
            'total_uploads': 0,
            'successful_uploads': 0,
            'failed_uploads': 0,
            'total_bytes_uploaded': 0
        }
        self.chunk_size_cache: Dict[int, int] = {}  # Cache adaptive chunk sizes per user
        
        # Circuit breaker for Google Drive API
        self.drive_circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=120)
        
        # Streaming thresholds for memory optimization
        self.STREAMING_THRESHOLD = 100 * 1024 * 1024  # 100MB
        self.MAX_CHUNK_SIZE = 32 * 1024 * 1024  # 32MB max chunk size
        self.MIN_CHUNK_SIZE = 2 * 1024 * 1024   # 2MB min chunk size
    
    async def upload_file_to_drive(self, file_path: str, file_name: str, telegram_id: int,
                                 parent_id: Optional[str] = None, mime_type: Optional[str] = None,
                                 progress_callback: Optional[Callable] = None,
                                 upload_id: Optional[str] = None, ctx: Optional[Any] = None) -> Dict[str, Any]:
        """Upload a file to Google Drive with streaming for large files and progress tracking."""
        upload_id = upload_id or f"upload_{uuid.uuid4().hex[:8]}"
        file_size = await self._prepare_drive_upload(file_path, file_name, telegram_id, upload_id)
        
        try:
            service = await self._get_verified_drive_service(telegram_id)
            parent_id = parent_id or await self._determine_upload_destination(telegram_id, ctx)
            mime_type = mime_type or self._guess_mime_type(file_name)
            
            if file_size > self.STREAMING_THRESHOLD:
                return await self._stream_upload_large_file(
                    service, file_path, file_name, parent_id, mime_type, 
                    telegram_id, progress_callback, upload_id
                )
            
            return await self._perform_standard_upload(
                service, file_path, file_name, parent_id, mime_type, telegram_id, file_size, progress_callback, upload_id
            )
            
        except Exception as e:
            await self._handle_drive_upload_error(e, upload_id)
            raise
        finally:
            track_task(self._cleanup_upload_info(upload_id))

    async def _prepare_drive_upload(self, file_path: str, file_name: str, telegram_id: int, upload_id: str) -> int:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        if not await self.drive_circuit_breaker.can_execute():
            raise Exception("Google Drive API circuit breaker is open - too many recent failures")
        
        file_size = os.path.getsize(file_path)
        self.active_uploads[upload_id] = {
            'type': 'file',
            'file_path': file_path,
            'file_name': file_name,
            'file_size': file_size,
            'telegram_id': telegram_id,
            'start_time': datetime.now(),
            'status': 'uploading',
            'uploaded_bytes': 0
        }
        
        self.upload_stats['total_uploads'] += 1
        logger.info(f"Starting Drive upload: {file_name} ({humanize.naturalsize(file_size, binary=True)}) (ID: {upload_id})")
        return file_size

    async def _get_verified_drive_service(self, telegram_id: int):
        service = await get_drive_service(telegram_id, "default_account")
        if not service:
            raise Exception("Could not get Google Drive service")
        return service

    async def _perform_standard_upload(
        self, service, file_path: str, file_name: str, parent_id: str, mime_type: str, 
        telegram_id: int, file_size: int, progress_callback: Optional[Callable], upload_id: str
    ) -> Dict[str, Any]:
        chunk_size = get_upload_adaptive_chunk_size(
            telegram_id, file_size, self.chunk_size_cache, self.MIN_CHUNK_SIZE, self.MAX_CHUNK_SIZE
        )
        
        media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True, chunksize=chunk_size)
        file_metadata = {'name': file_name, 'parents': [parent_id]}
        
        request = service.files().create(
            body=file_metadata, media_body=media, fields='id,name,size,mimeType,webViewLink'
        )
        
        response = await self._run_upload_loop(
            request=request, upload_id=upload_id, telegram_id=telegram_id,
            file_size=file_size, chunk_size=chunk_size, progress_callback=progress_callback
        )
        
        await self.drive_circuit_breaker.record_success()
        return await self._handle_upload_success(
            response=response, upload_id=upload_id, file_size=file_size, file_name=file_name, mime_type=mime_type
        )

    async def _handle_drive_upload_error(self, e: Exception, upload_id: str):
        self.upload_stats['failed_uploads'] += 1
        if upload_id in self.active_uploads:
            self.active_uploads[upload_id]['status'] = 'failed'
            self.active_uploads[upload_id]['error'] = str(e)
        
        await self.drive_circuit_breaker.record_failure()
        logger.error(f"Drive upload failed: {e} (ID: {upload_id})")
    
    async def _execute_stream_upload(self, service, file_metadata, media, upload_id, telegram_id, file_size, file_name, mime_type, progress_callback):
        loop = asyncio.get_running_loop()
        if media.resumable:
            request = service.files().create(
                body=file_metadata, media_body=media, fields='id,name,size,mimeType,webViewLink'
            )
            response = await self._run_upload_loop(
                request=request, upload_id=upload_id, telegram_id=telegram_id,
                file_size=file_size, chunk_size=None, progress_callback=progress_callback
            )
        else:
            def _do_upload():
                return service.files().create(
                    body=file_metadata, media_body=media, fields='id,name,size,mimeType,webViewLink'
                ).execute()
            response = await loop.run_in_executor(None, _do_upload)
        
        return await self._handle_upload_success(
            response=response, upload_id=upload_id, file_size=file_size,
            file_name=file_name, mime_type=mime_type
        )

    async def upload_stream_to_drive(self, stream: BytesIO, file_name: str, telegram_id: int,
                                   parent_id: Optional[str] = None, mime_type: Optional[str] = None,
                                   progress_callback: Optional[Callable] = None,
                                   upload_id: Optional[str] = None, ctx: Optional[Any] = None) -> Dict[str, Any]:
        """Upload a stream/buffer to Google Drive."""
        if not upload_id:
            upload_id = f"stream_{uuid.uuid4().hex[:8]}"
        
        stream.seek(0, 2)
        file_size = stream.tell()
        stream.seek(0)
        
        self.active_uploads[upload_id] = {
            'type': 'stream', 'file_name': file_name, 'file_size': file_size,
            'telegram_id': telegram_id, 'start_time': datetime.now(),
            'status': 'uploading', 'uploaded_bytes': 0
        }
        
        try:
            self.upload_stats['total_uploads'] += 1
            logger.info(f"Starting Drive stream upload: {file_name} ({humanize.naturalsize(file_size, binary=True)}) (ID: {upload_id})")
            
            service = await get_drive_service(telegram_id, "default_account")
            if not service:
                raise Exception("Could not get Google Drive service")
            
            if not parent_id:
                parent_id = await self._determine_upload_destination(telegram_id, ctx)
                if not parent_id:
                    raise Exception("Could not determine upload location")
            
            if not mime_type:
                mime_type = self._guess_mime_type(file_name)
            
            media = MediaIoBaseUpload(
                stream, mimetype=mime_type, resumable=True if file_size > 5 * 1024 * 1024 else False
            )
            file_metadata = {'name': file_name, 'parents': [parent_id]}
            
            return await self._execute_stream_upload(
                service, file_metadata, media, upload_id, telegram_id, file_size, file_name, mime_type, progress_callback
            )
            
        except Exception as e:
            self.upload_stats['failed_uploads'] += 1
            self.active_uploads[upload_id]['status'] = 'failed'
            self.active_uploads[upload_id]['error'] = str(e)
            logger.error(f"Drive stream upload failed: {e} (ID: {upload_id})")
            raise
        finally:
            track_task(self._cleanup_upload_info(upload_id))
    
    
    async def _determine_upload_destination(self, telegram_id: int, ctx: Optional[Any] = None) -> str:
        """Determine the upload destination based on file manager context or default settings."""
        try:
            # Check if file manager is currently open and user is browsing
            if ctx and hasattr(ctx, 'user_data') and ctx.user_data:
                user_state = ctx.user_data.get('state')
                if user_state and getattr(user_state, 'is_state', lambda x: False)(UserStateEnum.FILE_MANAGER):
                    # Get current account and folder from file manager context
                    current_account = ctx.user_data.get('current_account', 'default_account')
                    account_data = ctx.user_data.get('account_data', {})
                    
                    if current_account in account_data:
                        current_folder = account_data[current_account].get('current_folder', 'root')
                        if current_folder and current_folder != 'root':
                            logger.info(f"Using file manager current folder for upload: {current_folder}")
                            return current_folder
            
            # File manager not open or at root - use default location or CloudVerse Transfers
            # Using DriveCredentialsRepository locally
            from bots.drive.config import BOT_DB_PATH
            from shared.managers.EncryptionManager import get_encryption_manager
            from shared.database.repositories.DriveCredentialsRepository import DriveCredentialsRepository
            repo = DriveCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())
            default_location = await repo.get_default_location(telegram_id=telegram_id)
            
            if default_location and default_location != 'root':
                logger.info(f"Using user's default upload location: {default_location}")
                return default_location
            
            # No default location set - create/use CloudVerse Transfers folder
            cloudverse_folder_id = await self._ensure_cloudverse_transfers_folder(telegram_id)
            logger.info(f"Using CloudVerse Transfers folder: {cloudverse_folder_id}")
            return cloudverse_folder_id
            
        except Exception as e:
            logger.error(f"Error determining upload destination for {telegram_id}: {e}")
            # Fallback to root if all else fails
            return 'root'
    
    async def _ensure_cloudverse_transfers_folder(self, telegram_id: int) -> str:
        """Ensure CloudVerse Transfers folder exists in the primary account root."""
        try:
            # AWAIT get_drive_service
            service = await get_drive_service(telegram_id, "default_account")
            if not service:
                raise Exception("Could not get Google Drive service")
            
            loop = asyncio.get_running_loop()

            # Search for existing CloudVerse Transfers folder in root
            query = "name='CloudVerse Transfers' and parents in 'root' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            
            def _search_folder():
                return service.files().list(q=query, fields='files(id, name)').execute()
            
            results = await loop.run_in_executor(None, _search_folder)
            folders = results.get('files', [])
            
            if folders:
                # Folder exists, return its ID
                folder_id = folders[0]['id']
                logger.info(f"Found existing CloudVerse Transfers folder: {folder_id}")
                return folder_id
            
            # Folder doesn't exist, create it
            folder_metadata = {
                'name': 'CloudVerse Transfers',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': ['root']
            }
            
            def _create_folder():
                return service.files().create(
                    body=folder_metadata,
                    fields='id,name'
                ).execute()

            folder = await loop.run_in_executor(None, _create_folder)
            
            folder_id = folder['id']
            logger.info(f"Created CloudVerse Transfers folder: {folder_id}")
            return folder_id
            
        except Exception as e:
            logger.error(f"Error ensuring CloudVerse Transfers folder for {telegram_id}: {e}")
            # Fallback to root if folder creation fails
            return 'root'
    
    def _guess_mime_type(self, file_name: str) -> str:
        """Guess MIME type from file extension."""
        try:
            mime_type, _ = mimetypes.guess_type(file_name)
            return mime_type or 'application/octet-stream'
        except Exception as e:
            logger.error(f"Error guessing MIME type for {file_name}: {e}")
            return 'application/octet-stream'
    
    async def _cleanup_upload_info(self, upload_id: str):
        """Clean up upload info after a delay."""
        await asyncio.sleep(300)  # 5 minutes
        self.active_uploads.pop(upload_id, None)
    
    def get_upload_stats(self) -> Dict:
        """Get upload statistics."""
        return dict(self.upload_stats)
    
    def get_active_uploads(self) -> Dict:
        """Get currently active uploads."""
        return dict(self.active_uploads)
    
    async def cancel_upload(self, upload_id: str) -> bool:
        """Cancel an active upload."""
        if upload_id in self.active_uploads:
            self.active_uploads[upload_id]['status'] = 'cancelled'
            logger.info(f"Upload cancelled: {upload_id}")
            return True
        return False
    
    async def get_user_storage_info(self, telegram_id: int) -> Dict:
        """Get user's Google Drive storage information."""
        try:
            # AWAIT get_drive_service
            service = await get_drive_service(telegram_id, "default_account")
            if not service:
                raise Exception("Could not get Google Drive service")
            
            # AWAIT get_storage_info
            storage_info = await get_storage_info(service)
            return storage_info
        except Exception as e:
            logger.error(f"Failed to get storage info for user {telegram_id}: {e}")
            return {}
    
    async def create_folder(self, folder_name: str, telegram_id: int, 
                          parent_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a folder in Google Drive."""
        try:
            # AWAIT get_drive_service
            service = await get_drive_service(telegram_id, "default_account")
            if not service:
                raise Exception("Could not get Google Drive service")
            
            if not parent_id:
                # Using DriveCredentialsRepository locally
                from bots.drive.config import BOT_DB_PATH
                from shared.managers.EncryptionManager import get_encryption_manager
                from shared.database.repositories.DriveCredentialsRepository import DriveCredentialsRepository
                repo = DriveCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())
                parent_id = await repo.get_default_location(telegram_id=telegram_id)
            
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id] if parent_id else []
            }
            
            loop = asyncio.get_running_loop()

            def _create_folder():
                return service.files().create(
                    body=folder_metadata,
                    fields='id,name,webViewLink'
                ).execute()

            folder = await loop.run_in_executor(None, _create_folder)
            
            logger.info(f"Folder created: {folder_name} (ID: {folder['id']})")
            
            return {
                'success': True,
                'folder_id': folder['id'],
                'folder_name': folder['name'],
                'web_view_link': folder.get('webViewLink')
            }
            
        except Exception as e:
            logger.error(f"Failed to create folder {folder_name}: {e}")
            raise

    async def _stream_upload_large_file(self, service, file_path: str, file_name: str,
                                      parent_id: str, mime_type: str, telegram_id: int,
                                      progress_callback: Optional[Callable], upload_id: str) -> Dict[str, Any]:
        """Stream upload large files in chunks to reduce memory usage."""
        file_size = os.path.getsize(file_path)
        chunk_size = get_upload_adaptive_chunk_size(telegram_id, file_size, self.chunk_size_cache, self.MIN_CHUNK_SIZE, self.MAX_CHUNK_SIZE)
        
        # Create file metadata
        file_metadata = {
            'name': file_name,
            'parents': [parent_id]
        }
        
        # Use resumable upload for large files
        media = MediaFileUpload(
            file_path,
            mimetype=mime_type,
            resumable=True,
            chunksize=chunk_size
        )
        
        request = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,name,size,mimeType,webViewLink'
        )
        
        response = await self._run_upload_loop(
            request=request,
            upload_id=upload_id,
            telegram_id=telegram_id,
            file_size=file_size,
            chunk_size=chunk_size,
            progress_callback=progress_callback,
            force_gc=True
        )
        
        return {
            'success': True,
            'file_id': response['id'],
            'file_name': response['name'],
            'file_size': int(response.get('size', file_size)),
            'mime_type': response.get('mimeType', mime_type),
            'web_view_link': response.get('webViewLink'),
            'upload_id': upload_id
        }

    async def _run_upload_loop(self, request, upload_id: str, telegram_id: int, file_size: int,
                              chunk_size: Optional[int], progress_callback: Optional[Callable],
                              force_gc: bool = False):
        """Runs the upload next_chunk loop."""
        response = None
        loop = asyncio.get_running_loop()
        import time
        while response is None:
            # Check for cancellation
            if self.active_uploads.get(upload_id, {}).get('status') == 'cancelled':
                logger.info(f"Upload loop aborted for cancelled upload: {upload_id}")
                raise Exception("Upload cancelled by user or administrator.")
                
            try:
                start_time = time.time()
                status, response = await loop.run_in_executor(None, request.next_chunk)
                duration = time.time() - start_time
                if status:
                    uploaded_bytes = status.resumable_progress
                    if upload_id in self.active_uploads:
                        self.active_uploads[upload_id].update({
                            'uploaded_bytes': uploaded_bytes,
                            'progress_percent': (uploaded_bytes / file_size * 100) if file_size else 0
                        })
                    if progress_callback:
                        await progress_callback(uploaded_bytes, file_size)
                    if chunk_size:
                        adjust_upload_chunk_size(telegram_id, chunk_size, duration, self.chunk_size_cache, self.MIN_CHUNK_SIZE, self.MAX_CHUNK_SIZE)
                    if force_gc and uploaded_bytes % (50 * 1024 * 1024) == 0:
                        gc.collect()
            except HttpError as e:
                if e.resp.status in [500, 502, 503, 504]:
                    logger.warning(f"Retrying upload due to server error: {e}")
                    await asyncio.sleep(2)
                    continue
                else:
                    raise
        return response

    async def _handle_upload_success(self, response, upload_id: str, file_size: int, file_name: str, mime_type: str) -> Dict[str, Any]:
        """Handles successful upload logging and statistics."""
        self.upload_stats['successful_uploads'] += 1
        self.upload_stats['total_bytes_uploaded'] += file_size
        self.active_uploads[upload_id]['status'] = 'completed'
        
        try:
            await get_server_manager().update_bandwidth_usage(file_size, bot_name='drive', direction='upload')
        except Exception as e:
            logger.warning(f"Failed to update bandwidth usage: {e}")
        
        logger.info(f"Drive upload completed: {file_name} (ID: {upload_id})")
        
        return {
            'success': True,
            'file_id': response['id'],
            'file_name': response['name'],
            'file_size': int(response.get('size', file_size)),
            'mime_type': response.get('mimeType', mime_type),
            'web_view_link': response.get('webViewLink'),
            'upload_id': upload_id
        }


# Global upload manager instance
_upload_manager = None

def get_upload_manager() -> UploadManager:
    """Get the global upload manager instance."""
    global _upload_manager
    if _upload_manager is None:
        _upload_manager = UploadManager()
    return _upload_manager
