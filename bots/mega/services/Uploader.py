"""
Mega Uploader — MegaUploadManager for Mega bot.

Orchestrates the full Telegram→temp→Mega pipeline using mega.py.
"""
import os
import asyncio
import uuid
import logging
from datetime import datetime
from typing import Optional, Callable, Any, Dict

from shared.core.Logger import get_logger
from shared.core.CircuitBreaker import CircuitBreaker
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)

class MegaUploadManager:
    def __init__(self):
        self.active_uploads: Dict[str, Dict] = {}
        self.upload_stats = {
            'total_uploads': 0,
            'successful_uploads': 0,
            'failed_uploads': 0,
            'total_bytes_uploaded': 0,
        }
        self.mega_circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=120)

    async def _get_service(self, ctx: Any, telegram_id: int):
        provider = ctx.bot_data.get('provider') if ctx else None
        if not provider:
            raise ValueError("[MEGA] Provider not found in context.")
        service = await provider.get_service(telegram_id)
        if not service:
            raise ValueError("[MEGA][AUTH] No active Mega session. Have the user re-login via /settings.")
        return service

    async def upload_file_to_drive(
        self,
        file_path: str,
        file_name: str,
        telegram_id: int,
        parent_id: str = "/",
        progress_callback: Optional[Callable] = None,
        upload_id: Optional[str] = None,
        ctx: Optional[Any] = None,
        transfer_repo: Optional[Any] = None,
        transfer_id: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        dest_path = parent_id or "/"
        upload_id = upload_id or f"mega_{uuid.uuid4().hex[:8]}"
        upload_id = upload_id or f"mega_{uuid.uuid4().hex[:8]}"
        
        try:
            service = await self._get_service(ctx, telegram_id)
            file_size, start_time = await self._prepare_upload(service, file_path, file_name, telegram_id, upload_id)
            
            if transfer_repo and transfer_id:
                await transfer_repo.update_status(transfer_id, 'uploading')

            # Determine destination folder node
            dest_node = await self._get_dest_node(service, dest_path)
            
            # Setup progress interception from mega logger
            mega_logger = logging.getLogger("mega.mega")
            previous_propagate = mega_logger.propagate
            mega_logger.propagate = False
            
            class MegaProgressLogHandler(logging.Handler):
                def __init__(self, callback, loop, uploader_instance, upload_id):
                    super().__init__()
                    self.callback = callback
                    self.loop = loop
                    self.uploader = uploader_instance
                    self.upload_id = upload_id
                    import time
                    self.last_update = 0

                def handleError(self, record):
                    import sys
                    t, v, tb = sys.exc_info()
                    if isinstance(v, RuntimeError) and str(v) == "Upload cancelled by user":
                        raise v
                    super().handleError(record)

                def emit(self, record):
                    if self.uploader.active_uploads.get(self.upload_id, {}).get('status') == 'cancelled':
                        raise RuntimeError("Upload cancelled by user")
                        
                    msg = record.getMessage()
                    if " uploaded" in msg:
                        try:
                            import time
                            now = time.time()
                            
                            parts = msg.replace(" uploaded", "").split(" of ")
                            if len(parts) == 2:
                                current = int(parts[0])
                                total = int(parts[1])
                                
                                # Throttle BEFORE waking up the asyncio event loop!
                                # This prevents the event loop from being flooded with thousands of tasks.
                                if current < total and (now - self.last_update) < 5.0:
                                    return
                                self.last_update = now

                                if self.callback:
                                    asyncio.run_coroutine_threadsafe(
                                        self.callback(current, total), self.loop
                                    )
                        except Exception:
                            pass
                    else:
                        if record.levelno >= logging.WARNING:
                            logger.log(record.levelno, f"[MEGA API] {msg}")

            progress_handler = MegaProgressLogHandler(progress_callback, asyncio.get_running_loop(), self, upload_id)
            mega_logger.addHandler(progress_handler)

            try:
                # mega.py upload method
                await asyncio.to_thread(service.upload, file_path, dest_node, dest_filename=file_name)
            finally:
                mega_logger.removeHandler(progress_handler)
                mega_logger.propagate = previous_propagate
            
            return await self._handle_upload_success(
                upload_id, file_name, dest_path, file_size, start_time, transfer_repo, transfer_id
            )

        except Exception as e:
            await self._handle_upload_error(e, upload_id, file_name, transfer_repo, transfer_id)
            raise

        finally:
            self._cleanup_temp_file(file_path, upload_id)
            track_task(self._cleanup_upload_info(upload_id))

    async def _get_dest_node(self, service: Any, dest_path: str):
        if dest_path in ("root", "/", "", None):
            return service.root_id
            
        def _find_or_create():
            service.get_files()
            node_name = dest_path.strip("/")
            res = service.create_folder(node_name, service.root_id)
            
            if isinstance(res, dict):
                return list(res.keys())[0] if res else service.root_id
            elif isinstance(res, list) and res:
                return list(res[0].keys())[0] if isinstance(res[0], dict) else service.root_id
            return res if res else service.root_id

        return await asyncio.to_thread(_find_or_create)

    async def _prepare_upload(self, service: Any, file_path: str, file_name: str, telegram_id: int, upload_id: str) -> tuple[int, datetime]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"[MEGA] Temp file not found: {file_path}")
        if not await self.mega_circuit_breaker.can_execute():
            raise Exception("[MEGA] Mega circuit breaker is open — too many recent failures")

        file_size = os.path.getsize(file_path)
        
        try:
            space = await asyncio.to_thread(service.get_storage_space)
            free_space = space.get("total", 0) - space.get("used", 0)
            if free_space < file_size:
                raise Exception(f"[MEGA] Insufficient quota. File size requires {file_size} bytes, but only {free_space} bytes are free.")
        except Exception as quota_err:
            if "Insufficient quota" in str(quota_err):
                raise
            logger.warning(f"[MEGA] Could not verify quota before upload: {quota_err}")

        start_time = datetime.now()

        self.active_uploads[upload_id] = {
            'file_name': file_name,
            'file_size': file_size,
            'telegram_id': telegram_id,
            'start_time': start_time,
            'status': 'uploading',
        }
        self.upload_stats['total_uploads'] += 1
        logger.info(f"[MEGA][UPLOAD] Upload started: {file_name} ({file_size} bytes) (ID: {upload_id})")
        return file_size, start_time

    async def _handle_upload_success(
        self, upload_id: str, file_name: str, dest_path: str, file_size: int, 
        start_time: datetime, transfer_repo: Any, transfer_id: int
    ) -> Dict[str, Any]:
        duration = (datetime.now() - start_time).total_seconds()
        avg_speed = file_size / duration if duration > 0 else 0

        self.upload_stats['successful_uploads'] += 1
        self.upload_stats['total_bytes_uploaded'] += file_size
        if upload_id in self.active_uploads:
            self.active_uploads[upload_id]['status'] = 'completed'
        await self.mega_circuit_breaker.record_success()

        if transfer_repo and transfer_id:
            await transfer_repo.update_status(
                transfer_id, 'completed', avg_upload_speed=avg_speed, upload_duration=duration
            )

        logger.info(
            f"[MEGA][UPLOAD] Upload completed: {file_name} → {dest_path} "
            f"in {duration:.1f}s at {avg_speed/1024:.1f} KB/s (ID: {upload_id})"
        )

        return {
            'success': True,
            'file_name': file_name,
            'dest_path': dest_path,
            'file_size': file_size,
            'upload_id': upload_id,
            'duration': duration,
            'avg_speed': avg_speed,
        }

    async def _handle_upload_error(self, e: Exception, upload_id: str, file_name: str, transfer_repo: Any, transfer_id: int):
        self.upload_stats['failed_uploads'] += 1
        if upload_id in self.active_uploads:
            self.active_uploads[upload_id]['status'] = 'failed'
        await self.mega_circuit_breaker.record_failure()

        if transfer_repo and transfer_id:
            try:
                await transfer_repo.update_status(transfer_id, 'failed', error_message=str(e))
            except Exception as db_err:
                logger.error(f"[MEGA] Failed to update transfer status in DB: {db_err}")

        logger.error(f"[MEGA][UPLOAD] Upload failed: {file_name} (ID: {upload_id}): {e}", exc_info=True)

    def _cleanup_temp_file(self, file_path: str, upload_id: str):
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.debug(f"[MEGA][UPLOAD] Temp file deleted (ID: {upload_id})")
            except Exception as del_err:
                logger.warning(f"[MEGA] Failed to delete temp file: {del_err}")

    async def _determine_upload_destination(
        self, telegram_id: int, ctx: Optional[Any] = None
    ) -> str:
        try:
            credential_repo = ctx.bot_data.get('credential_repo') if ctx else None
            if credential_repo:
                creds = await credential_repo.get(str(telegram_id))
                if creds and creds.get('default_upload_location'):
                    dest = creds['default_upload_location']
                    logger.debug(f"[MEGA][UPLOAD] Using user default location: {dest}")
                    return dest
        except Exception as e:
            logger.warning(f"[MEGA][UPLOAD] Could not read default location: {e}")

        return "/CloudVerse Transfers"

    async def _cleanup_upload_info(self, upload_id: str):
        await asyncio.sleep(300)
        self.active_uploads.pop(upload_id, None)

    def get_upload_stats(self) -> Dict:
        return dict(self.upload_stats)

    def get_active_uploads(self) -> Dict:
        return dict(self.active_uploads)

    async def cancel_upload(self, upload_id: str) -> bool:
        if upload_id in self.active_uploads:
            self.active_uploads[upload_id]['status'] = 'cancelled'
            logger.info(f"[MEGA] Upload cancelled: {upload_id}")
            return True
        return False


_upload_manager: Optional[MegaUploadManager] = None

def get_upload_manager() -> MegaUploadManager:
    global _upload_manager
    if _upload_manager is None:
        _upload_manager = MegaUploadManager()
    return _upload_manager
