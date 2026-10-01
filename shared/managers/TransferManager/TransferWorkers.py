"""
TransferWorkers — background tasks for the transfer pipeline.

Contains the queue admission worker, queued-transfer processor,
and the periodic stale-transfer cleanup task.
"""
import asyncio
from typing import Dict

from shared.core.Logger import get_logger
from .TransferTracker import get_transfer_tracker

logger = get_logger(__name__)


async def queue_admission_worker(app):
    while True:
        try:
            admitted = await get_transfer_tracker().admit_waiting_users()
            for item in admitted:
                uid = item.get('telegram_id')
                lane = item.get('lane')
                transfer_data = item.get('transfer_data')
                
                if transfer_data:
                    try:
                        await _process_queued_transfer(app, uid, transfer_data)
                    except Exception as e:
                        logger.error(f"Failed to auto-start transfer for user {uid}: {e}")
                        try:
                            await app.bot.send_message(chat_id=uid, text=(
                                "? Auto-transfer failed. Please send your file/URL again."
                            ))
                        except Exception as notify_error:
                            logger.debug(f"Failed to send fallback notification: {notify_error}")
                else:
                    try:
                        await app.bot.send_message(chat_id=uid, text=(
                            f"? Slot available in {lane} lane. You can start your upload now."
                        ))
                    except Exception as e:
                        logger.debug(f"Failed to notify user about slot availability: {e}")
            try:
                queue_event = get_transfer_tracker()._queue.queue_event
                await asyncio.wait_for(queue_event.wait(), timeout=60.0)
                queue_event.clear()
            except asyncio.TimeoutError:
                pass
        except Exception as e:
            logger.error(f"[TRANSFER] Queue admission worker error: {e}", exc_info=True)
            await asyncio.sleep(5)


async def _process_queued_transfer(app, telegram_id: int, transfer_data: Dict):
    """Process a queued transfer automatically when slot becomes available"""
    try:
        from .TransferExecutor import TransferExecutor
        
        class MockUser:
            def __init__(self, uid, uname):
                self.id = uid
                self.username = uname

        class MockDocument:
            def __init__(self, doc_dict):
                self.file_id = doc_dict.get('file_id')
                self.file_name = doc_dict.get('file_name', 'Unknown File')
                self.file_size = doc_dict.get('file_size')
                self.mime_type = doc_dict.get('mime_type')

        class MockMessage:
            def __init__(self, uid, uname, app_obj, doc, txt):
                self.from_user = MockUser(uid, uname)
                self.document = MockDocument(doc) if doc else None
                self.text = txt
                self._app = app_obj
                self._uid = uid
                
            async def reply_text(self, text, **kwargs):
                return await self._app.bot.send_message(chat_id=self._uid, text=text, **kwargs)

        class MockUpdate:
            def __init__(self, app_obj, uid, uname, doc, txt):
                self.message = MockMessage(uid, uname, app_obj, doc, txt)

        class MockContext:
            def __init__(self, app_obj):
                self.bot = app_obj.bot
                self.bot_data = app_obj.bot_data
                self.user_data = {}

        username = transfer_data.get('username', '')
        transfer_type = transfer_data.get('type')
        tracker = get_transfer_tracker()
        
        ctx = MockContext(app)

        if transfer_type == 'file':
            file_info = transfer_data.get('file_info')
            if file_info:
                update = MockUpdate(app, telegram_id, username, doc=file_info, txt=None)
                await app.bot.send_message(
                    chat_id=telegram_id,
                    text=f"🔄 Automatic upload started: {file_info.get('file_name', 'Unknown File')}"
                )
                await TransferExecutor.handle_file_transfer(tracker, update, ctx)
                
        elif transfer_type == 'url':
            url = transfer_data.get('url')
            if url:
                update = MockUpdate(app, telegram_id, username, doc=None, txt=url)
                await app.bot.send_message(
                    chat_id=telegram_id,
                    text=f"🔄 Automatic download started: {url}"
                )
                await TransferExecutor.handle_url_transfer(tracker, update, ctx)
        
    except Exception as e:
        logger.error(f"Error processing queued transfer: {e}")
        raise


async def upload_tracker_cleanup_task():
    upload_tracker = get_transfer_tracker()
    while True:
        try:
            await asyncio.sleep(3600)
            logger.debug("Running upload tracker cleanup")
            await upload_tracker.cleanup_stale_transfers(max_age_hours=24)
           
            stats = await upload_tracker.get_system_stats()
            logger.info(f"Upload tracker stats: {stats}")
        except Exception as e:
            logger.error(f"Error in upload tracker cleanup task: {str(e)}", exc_info=True)
