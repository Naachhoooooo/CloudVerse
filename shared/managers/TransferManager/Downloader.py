import os
import tempfile
import asyncio
import aiohttp
import subprocess  # nosec B404
import uuid
from datetime import datetime
from typing import Optional, Callable, Any, Dict, Type
from shared.managers.SessionManager import get_session_manager
from shared.database.repositories.SessionRepository import SessionRepository
from shared.managers.ServerManager import get_server_manager
import humanize
from shared.core.Logger import get_logger
from shared.core.CircuitBreaker import CircuitBreaker
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)


class DownloadManager:
    """Centralized, provider-agnostic download manager for all file download operations."""
    
    def __init__(self, db_path: str, cipher: bytes, transfer_repo_class: Type, log_tag: str = "[DOWNLOADER]"):
        self.db_path = db_path
        self.cipher = cipher
        self.transfer_repo_class = transfer_repo_class
        self.log_tag = log_tag
        
        self.download_stats = {
            'total_downloads': 0,
            'successful_downloads': 0,
            'failed_downloads': 0,
            'total_bytes_downloaded': 0
        }
        self.active_downloads: Dict[str, Dict] = {}
        self.http_circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=300)
        self.telegram_circuit_breaker = CircuitBreaker(failure_threshold=3, timeout=600)
        self.ytdlp_circuit_breaker = CircuitBreaker(failure_threshold=3, timeout=300)
        # Streaming thresholds for memory optimization
        self.STREAMING_THRESHOLD = 100 * 1024 * 1024  # 100MB
        self.CHUNK_SIZE = 8 * 1024 * 1024  # 8MB chunks for better memory usage
    
    async def download_http_to_tempfile(self, url: str, progress_callback: Optional[Callable] = None, 
                                      download_id: Optional[str] = None) -> str:
        """Download a URL to a temporary file asynchronously using aiohttp."""
        if not download_id:
            download_id = f"http_{uuid.uuid4().hex[:8]}"
        
        temp_file_path = None
        self.active_downloads[download_id] = {
            'type': 'http',
            'url': url,
            'start_time': datetime.now(),
            'status': 'downloading'
        }
        
        try:
            self.download_stats['total_downloads'] += 1
            logger.info(f"{self.log_tag}[UPLOAD] Starting HTTP download: {url} (ID: {download_id})")
            
            timeout = aiohttp.ClientTimeout(total=3600)
            connector = aiohttp.TCPConnector(limit=300, limit_per_host=50, ttl_dns_cache=300)
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    total_size = int(resp.headers.get('Content-Length', 0)) or None
                    downloaded = 0
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp:
                        temp_file_path = tmp.name
                        chunk_size = self._get_optimal_chunk_size(total_size)
                        
                        while True:
                            chunk = await resp.content.read(chunk_size)
                            if not chunk:
                                break
                            tmp.write(chunk)
                            downloaded += len(chunk)
                            
                            self.active_downloads[download_id].update({
                                'downloaded_bytes': downloaded,
                                'total_bytes': total_size,
                                'progress_percent': (downloaded / total_size * 100) if total_size else 0
                            })
                            
                            if progress_callback and total_size:
                                await progress_callback(downloaded, total_size)
            
            self.download_stats['successful_downloads'] += 1
            self.download_stats['total_bytes_downloaded'] += downloaded
            self.active_downloads[download_id]['status'] = 'completed'
            
            try:
                bot_name = self.log_tag.strip('[]').lower()
                if bot_name == 'downloader' or not bot_name: bot_name = 'unknown'
                await get_server_manager().update_bandwidth_usage(downloaded, bot_name=bot_name, direction='download')
            except Exception as e:
                logger.warning(f"{self.log_tag} Failed to update bandwidth usage: {e}")
            
            logger.info(f"{self.log_tag}[UPLOAD] HTTP download completed: {humanize.naturalsize(downloaded, binary=True)} (ID: {download_id})")
            return temp_file_path
            
        except Exception as e:
            self.download_stats['failed_downloads'] += 1
            self.active_downloads[download_id]['status'] = 'failed'
            self.active_downloads[download_id]['error'] = str(e)
            
            await self.http_circuit_breaker.record_failure()
            
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception as e:
                    logger.debug(f"{self.log_tag} Failed to cleanup temp file: {e}")
            
            logger.error(f"{self.log_tag}[UPLOAD] HTTP download failed: {e} (ID: {download_id})", exc_info=True)
            raise
        else:
            await self.http_circuit_breaker.record_success()
        finally:
            track_task(self._cleanup_download_info(download_id))

    async def download_from_telegram(self, telegram_id: int, message_id: int, chat_id: int, file_size: int, 
                                   file_name: str, progress_callback: Optional[Callable] = None,
                                   download_id: Optional[str] = None) -> Optional[str]:
        """Download file from Telegram using Telethon with streaming for large files."""
        if not download_id:
            download_id = f"telegram_{uuid.uuid4().hex[:8]}"
        
        session_manager = get_session_manager()
        repo = SessionRepository(self.db_path, self.cipher)
        available_sessions = await repo.get_best_available()
        
        if not available_sessions:
            raise ValueError(f"{self.log_tag} No active Telethon sessions with API credentials found. Please contact Team CloudVerse via the bot.")
        
        active_session = session_manager.select_optimal_session(available_sessions)
        logger.info(f"{self.log_tag}[UPLOAD] Selected session {active_session['session_id']} for download (used: {active_session['used']} times, {len(available_sessions)} sessions available)")
        
        if not await self.telegram_circuit_breaker.can_execute():
            raise Exception(f"{self.log_tag} Telegram API circuit breaker is open")
        
        _transfer_repo = self.transfer_repo_class(self.db_path)
        await _transfer_repo.create(
            telegram_id=str(telegram_id), file_name=file_name,
            file_size=file_size, method="telegram_download",
            status="downloading",
        )
        
        self.active_downloads[download_id] = {
            'telegram_id': telegram_id,
            'file_name': file_name,
            'file_size': file_size,
            'start_time': datetime.now(),
            'status': 'downloading'
        }
        
        client = await session_manager.create_client(active_session['session_id'])
        if not client:
            raise ValueError(f"{self.log_tag} Failed to create Telethon client for session {active_session['session_id']}")
        
        try:
            self.download_stats['total_downloads'] += 1
            logger.info(f"{self.log_tag}[UPLOAD] Starting Telegram download: {file_name} (ID: {download_id})")
            
            await client.connect()
            
            if not await client.is_user_authorized():
                raise ValueError("❌ Telethon session is not authenticated\n\nPlease contact Team CloudVerse to authenticate the Telegram sessions for proper bot functionality\n\nRemember its a Contribution, and Welcome to CloudVerse Family")
            
            msg = await client.get_messages(chat_id, ids=message_id)
            if isinstance(msg, list):
                msg = msg[0] if msg else None
            if not msg:
                raise ValueError(f"{self.log_tag} Could not find the original message for this file")

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.tmp')
            temp_file_path = temp_file.name
            temp_file.close()
            
            # Use Telethon's highly optimized native download_media instead of custom iter_download
            await client.download_media(msg, file=temp_file_path, progress_callback=progress_callback)
            
            self.download_stats['successful_downloads'] += 1
            
            actual_size = os.path.getsize(temp_file_path) if os.path.exists(temp_file_path) else 0
            self.download_stats['total_bytes_downloaded'] += actual_size
            
            try:
                bot_name = self.log_tag.strip('[]').lower()
                if bot_name == 'downloader' or not bot_name: bot_name = 'unknown'
                await get_server_manager().update_bandwidth_usage(actual_size, bot_name=bot_name, direction='download')
            except Exception as e:
                logger.warning(f"{self.log_tag} Failed to update bandwidth usage: {e}")
            
            self.active_downloads[download_id]['status'] = 'completed'
            logger.info(f"{self.log_tag}[UPLOAD] Telegram download completed: {file_name} (ID: {download_id})")
            return temp_file_path
            
        except Exception as e:
            self.download_stats['failed_downloads'] += 1
            self.active_downloads[download_id]['status'] = 'failed'
            self.active_downloads[download_id]['error'] = str(e)
            logger.error(f"{self.log_tag}[UPLOAD] Telegram download failed: {e} (ID: {download_id})", exc_info=True)
            raise
        finally:
            await client.disconnect()
            track_task(self._cleanup_download_info(download_id))

    async def download_with_ytdlp(self, url: str, temp_dir: Optional[str] = None, 
                          download_id: Optional[str] = None) -> str:
        """Download from streaming sites using yt-dlp."""
        if not download_id:
            download_id = f"ytdlp_{uuid.uuid4().hex[:8]}"
        
        if temp_dir is None:
            temp_dir = tempfile.gettempdir()
        
        self.active_downloads[download_id] = {
            'type': 'ytdlp',
            'url': url,
            'start_time': datetime.now(),
            'status': 'downloading'
        }
        
        try:
            self.download_stats['total_downloads'] += 1
            logger.info(f"{self.log_tag}[UPLOAD] Starting yt-dlp download: {url} (ID: {download_id})")
            
            output_template = f"{temp_dir}/%(title)s.%(ext)s"
            cmd = [
                "yt-dlp",
                "-f", "bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "--no-warnings",
                "--restrict-filenames",
                "--no-check-certificates",
                "--concurrent-fragments", "16",
                "-o", output_template,
                url
            ]
            
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3600)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise Exception("yt-dlp failed: Timeout expired")
            
            if proc.returncode != 0:
                raise Exception(f"yt-dlp failed: {stderr.decode('utf-8', errors='replace')}")
            
            import glob
            files = glob.glob(f"{temp_dir}/*")
            if not files:
                raise Exception("yt-dlp did not produce any output file.")
            
            downloaded_file = max(files, key=os.path.getmtime)
            file_size = os.path.getsize(downloaded_file)
            
            self.download_stats['successful_downloads'] += 1
            self.download_stats['total_bytes_downloaded'] += file_size
            
            try:
                bot_name = self.log_tag.strip('[]').lower()
                if bot_name == 'downloader' or not bot_name: bot_name = 'unknown'
                await get_server_manager().update_bandwidth_usage(file_size, bot_name=bot_name, direction='download')
            except Exception as e:
                logger.warning(f"{self.log_tag} Failed to update bandwidth usage: {e}")
            
            self.active_downloads[download_id].update({
                'status': 'completed',
                'file_size': file_size,
                'output_file': downloaded_file
            })
            
            logger.info(f"{self.log_tag}[UPLOAD] yt-dlp download completed: {humanize.naturalsize(file_size, binary=True)} (ID: {download_id})")
            return downloaded_file
            
        except Exception as e:
            self.download_stats['failed_downloads'] += 1
            self.active_downloads[download_id]['status'] = 'failed'
            self.active_downloads[download_id]['error'] = str(e)
            logger.error(f"{self.log_tag}[UPLOAD] yt-dlp download failed: {e} (ID: {download_id})", exc_info=True)
            raise
        finally:
            track_task(self._cleanup_download_info(download_id))

    async def download_telegram_file(self, file_obj: Any, progress_callback: Optional[Callable] = None,
                                   download_id: Optional[str] = None) -> str:
        """Download file from Telegram bot API."""
        if not download_id:
            download_id = f"tg_api_{uuid.uuid4().hex[:8]}"
        
        temp_file_path = None
        self.active_downloads[download_id] = {
            'type': 'telegram_api',
            'file_path': file_obj.file_path,
            'start_time': datetime.now(),
            'status': 'downloading'
        }
        
        try:
            self.download_stats['total_downloads'] += 1
            logger.info(f"{self.log_tag}[UPLOAD] Starting Telegram API download: {file_obj.file_path} (ID: {download_id})")
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as temp_file:
                temp_file_path = temp_file.name
                downloaded_bytes = 0
                
                timeout = aiohttp.ClientTimeout(total=3600)
                connector = aiohttp.TCPConnector(limit=300, limit_per_host=50, ttl_dns_cache=300)
                async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                    async with session.get(file_obj.file_path) as resp:
                        resp.raise_for_status()
                        total_size = int(resp.headers.get('Content-Length', 0)) or None
                        
                        while True:
                            chunk = await resp.content.read(1024 * 1024)
                            if not chunk:
                                break
                            temp_file.write(chunk)
                            downloaded_bytes += len(chunk)
                            
                            self.active_downloads[download_id].update({
                                'downloaded_bytes': downloaded_bytes,
                                'total_bytes': total_size,
                                'progress_percent': (downloaded_bytes / total_size * 100) if total_size else 0
                            })
                            
                            if progress_callback and total_size:
                                await progress_callback(downloaded_bytes, total_size)
            
            self.download_stats['successful_downloads'] += 1
            self.download_stats['total_bytes_downloaded'] += downloaded_bytes
            
            try:
                bot_name = self.log_tag.strip('[]').lower()
                if bot_name == 'downloader' or not bot_name: bot_name = 'unknown'
                await get_server_manager().update_bandwidth_usage(downloaded_bytes, bot_name=bot_name, direction='download')
            except Exception as e:
                logger.warning(f"{self.log_tag} Failed to update bandwidth usage: {e}")
            
            self.active_downloads[download_id]['status'] = 'completed'
            logger.info(f"{self.log_tag}[UPLOAD] Telegram API download completed: {humanize.naturalsize(downloaded_bytes, binary=True)} (ID: {download_id})")
            return temp_file_path
            
        except Exception as e:
            self.download_stats['failed_downloads'] += 1
            self.active_downloads[download_id]['status'] = 'failed'
            self.active_downloads[download_id]['error'] = str(e)
            
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception as cleanup_err:
                    logger.debug(f"{self.log_tag} Failed to cleanup temp file: {cleanup_err}")
            
            logger.error(f"{self.log_tag}[UPLOAD] Telegram API download failed: {e} (ID: {download_id})", exc_info=True)
            raise
        finally:
            track_task(self._cleanup_download_info(download_id))
    
    async def _cleanup_download_info(self, download_id: str):
        """Clean up download info after a delay."""
        await asyncio.sleep(300)
        self.active_downloads.pop(download_id, None)
    
    def get_download_stats(self) -> Dict:
        """Get download statistics."""
        return dict(self.download_stats)
    
    def get_active_downloads(self) -> Dict:
        """Get currently active downloads."""
        return dict(self.active_downloads)
    
    def _get_optimal_chunk_size(self, total_size: Optional[int]) -> int:
        """Calculate optimal chunk size based on file size for memory efficiency."""
        if not total_size:
            return self.CHUNK_SIZE
        if total_size < 10 * 1024 * 1024:
            return 1024 * 1024
        elif total_size < 100 * 1024 * 1024:
            return 4 * 1024 * 1024
        else:
            return self.CHUNK_SIZE
    
    async def _stream_telegram_download(self, client, msg, file_size: int, 
                                      progress_callback: Optional[Callable], download_id: str) -> str:
        """Stream download large Telegram files to reduce memory usage."""
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.tmp')
        temp_file_path = temp_file.name
        
        try:
            downloaded = 0
            chunk_size = self._get_optimal_chunk_size(file_size)
            
            async for chunk in client.iter_download(msg.media, chunk_size=chunk_size):
                temp_file.write(chunk)
                downloaded += len(chunk)
                
                self.active_downloads[download_id].update({
                    'downloaded_bytes': downloaded,
                    'total_bytes': file_size,
                    'progress_percent': (downloaded / file_size * 100) if file_size else 0
                })
                
                if progress_callback:
                    await progress_callback(downloaded, file_size)
            
            temp_file.close()
            return temp_file_path
            
        except Exception as e:
            temp_file.close()
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            raise e
    
    async def cancel_download(self, download_id: str) -> bool:
        """Cancel an active download."""
        if download_id in self.active_downloads:
            self.active_downloads[download_id]['status'] = 'cancelled'
            logger.info(f"{self.log_tag} Download cancelled: {download_id}")
            return True
        return False
