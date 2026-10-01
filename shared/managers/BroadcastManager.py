import asyncio
import time
from telegram.ext import ContextTypes

from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)

class BroadcastManager:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(BroadcastManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        # Prevent re-initialization if already created
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

    async def create_broadcast_request(self, broadcast_repo, requester_telegram_id: str, requester_username: str, message_text: str, media_type: str, media_file_id: str, target_count: int, target_audience: str = 'all') -> str:
        """Create a new broadcast request in the database pending approval."""
        request_id = await broadcast_repo.create(
            requester_telegram_id=requester_telegram_id,
            requester_username=requester_username,
            group_message_id=None,
            message_text=message_text,
            media_type=media_type,
            media_file_id=media_file_id,
            target_count=target_count,
            target_audience=target_audience
        )
        return request_id

    async def process_approval(self, request_id: int, broadcast_repo, approver_telegram_id: str, approver_username: str, is_super_admin: bool) -> dict:
        """Process an approval for a broadcast request. Returns updated status info."""
        request = await broadcast_repo.get(request_id)
        if not request:
            return {"status": "not_found"}

        # Super admin approves instantly
        if is_super_admin:
            await broadcast_repo.update_status(request_id, status="approved", approved_by=approver_username)
            request['status'] = "approved"
            request['approvers'] = [{'id': approver_telegram_id, 'username': approver_username}]
            return {"status": "approved", "request": request, "approvers": request['approvers']}

        # Regular admin logic (requires 2 unique approvers)
        approvers = request.get('approved_by')
        if approvers is None:
            approvers = []
        elif isinstance(approvers, str):
            import json
            try:
                approvers = json.loads(approvers)
            except json.JSONDecodeError:
                approvers = []

        if not any(a['id'] == approver_telegram_id for a in approvers) and len(approvers) < 2:
            approvers.append({'id': approver_telegram_id, 'username': approver_username})
            await broadcast_repo.update_approvers(request_id, approvers)

        if len(approvers) == 2:
            await broadcast_repo.update_status(request_id, status="approved", approved_by=None)
            request['status'] = "approved"
            return {"status": "approved", "request": request, "approvers": approvers}

        return {"status": "pending", "request": request, "approvers": approvers}

    async def reject_broadcast(self, request_id: int, broadcast_repo, rejector_username: str) -> bool:
        """Reject a broadcast request (Super Admin only)."""
        await broadcast_repo.update_status(request_id, status="rejected", approved_by="Super Admin")
        return True

    async def execute_broadcast_task(self, request: dict, target_users: list, approvers: list = None, send_func=None):
        """Asynchronously send broadcast to all target users, yielding progress."""
        start_time = time.time()
        success_count = 0
        failed_count = 0
        
        def format_duration(seconds: float) -> str:
            m, s = divmod(int(seconds), 60)
            h, m = divmod(m, 60)
            if h > 0:
                return f"{h}h {m}m {s}s"
            elif m > 0:
                return f"{m}m {s}s"
            return f"{s}s"
        
        total_users = len(target_users)
        logger.info(f"Starting broadcast request {request.get('request_id', request.get('id', 'unknown'))} to {total_users} users")

        media_type = request.get('media_type', 'text')
        
        approver_text = ""
        if approvers:
            approver_text = "\n\n" + "\n".join([f"✅ <i>Approved by @{a['username']}</i>" for a in approvers])
        else:
            approver_text = "\n\n✅ <i>Approved by Super Admin</i>"

        # Process in chunks of 3 to respect Telegram limits
        chunk_size = 3
        for i in range(0, total_users, chunk_size):
            chunk = target_users[i:i + chunk_size]
            
            for user in chunk:
                if isinstance(user, dict) and 'telegram_id' in user:
                    user_id = user['telegram_id']
                elif isinstance(user, (str, int)):
                    user_id = user
                else:
                    continue

                try:
                    if not user_id:
                        continue
                    
                    if send_func:
                        await send_func(user_id, request, approver_text)
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.debug(f"Failed to send broadcast to {user_id}: {e}")
                
            # Rate limiting: wait 1 second between chunks of 3
            await asyncio.sleep(1.0)
            
            # Progress Update
            processed = min(i + chunk_size, total_users)
            pct = int((processed / total_users) * 100) if total_users > 0 else 0
            elapsed_now = time.time() - start_time
            from datetime import datetime
            initiated_at = request.get('last_updated', 'Unknown')
            if isinstance(initiated_at, str): initiated_at = initiated_at[:16]
            approved_at = request.get('approved_at') or datetime.now().strftime('%Y-%m-%d %H:%M')
            if isinstance(approved_at, str): approved_at = approved_at[:16]
            
            yield {
                "progress_text": (
                    f"📡 <b>Broadcast In Progress...</b>\n\n"
                    f"👤 <b>Requester:</b> @{request.get('requester_username')}\n"
                    f"🎯 <b>Target:</b> <code>{request.get('target_count')}</code> users\n"
                    f"📋 <b>Type:</b> {media_type.capitalize()}\n"
                    f"⏰ <b>Initiated At:</b> {initiated_at}\n"
                    f"✅ <b>Approved At:</b> {approved_at}\n\n"
                    f"🚀 <i>Sending messages...</i>\n"
                    f"⏳ <b>Progress:</b> {processed}/{total_users} ({pct}%)\n"
                    f"⏱️ <b>Elapsed:</b> {format_duration(elapsed_now)}"
                ),
                "processed": processed,
                "total": total_users,
                "success": success_count,
                "failed": failed_count
            }

        elapsed = time.time() - start_time
        logger.info(f"Completed broadcast. Success: {success_count}, Failed: {failed_count} in {elapsed:.1f}s")
        yield {
            "completed": True,
            "success": success_count,
            "failed": failed_count
        }

    async def execute_broadcast_approval(self, broadcast_repo, request_id: int, approver_id: str, approver_username: str, is_super: bool):
        result = await self.process_approval(request_id, broadcast_repo, approver_id, approver_username, is_super)
        return result

def get_broadcast_manager() -> BroadcastManager:
    return BroadcastManager()
