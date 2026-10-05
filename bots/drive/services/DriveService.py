import time
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import re
import humanize
import asyncio
from functools import partial
from bots.drive.config import BOT_DB_PATH
from shared.managers.EncryptionManager import get_encryption_manager
from shared.database.repositories.DriveCredentialsRepository import DriveCredentialsRepository
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors

logger = get_logger(__name__)

def _get_credential_repo():
    return DriveCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())

# Folder size TTL cache: folder_id -> (total_bytes, computed_at_unix_timestamp)
# Cached for 5 minutes to avoid repeated recursive Drive API calls for the same folder.
_folder_size_cache: dict[str, tuple[int, float]] = {}
_FOLDER_SIZE_TTL = 300  # seconds


async def _run_in_executor(func, *args, **kwargs):
    """Helper to run blocking functions in an executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


async def get_credentials(telegram_id, account_email=None):
    logger.debug(f"Getting credentials for user {telegram_id}")
    try:
        repo = _get_credential_repo()
        primary = await repo.get(telegram_id=telegram_id)
        if not primary:
            logger.debug(f"No credentials found for user {telegram_id}")
            return None
        return primary.get('drive_credential')
    except Exception as e:
        logger.error(f"Failed to get credentials for user {telegram_id}: {str(e)}", exc_info=True)
        return None


async def set_credentials(telegram_id, account_email, credentials_dict):
    logger.info(f"Setting credentials for user {telegram_id}, email {account_email}")
    try:
        repo = _get_credential_repo()
        # Ensure credentials dict has 'token' to prevent NoneType errors downstream if missing
        await repo.upsert(
            telegram_id=telegram_id, 
            username=None, 
            email_address=account_email, 
            credentials=credentials_dict
        )
        logger.info(f"Successfully set credentials for user {telegram_id}")
    except Exception as e:
        logger.error(f"Failed to set credentials for user {telegram_id}: {str(e)}", exc_info=True)
        raise


async def remove_credentials(telegram_id, account_email):
    try:
        repo = _get_credential_repo()
        return await repo.clear_credentials(telegram_id=telegram_id)
    except Exception as e:
        logger.error(f"Failed to remove credentials for user {telegram_id}: {str(e)}", exc_info=True)
        raise


async def get_drive_service(telegram_id, account_email=None):
    logger.debug(f"Getting Drive service for user {telegram_id}")
    try:
        repo = _get_credential_repo()
        primary = await repo.get(telegram_id=telegram_id)
        if not primary:
            logger.debug(f"No account found for user {telegram_id}")
            return None
        account_email = primary.get('email_address')
        creds_info = primary.get('drive_credential')

        if not creds_info:
            logger.debug(f"No credentials found for user {telegram_id}, account {account_email}")
            return None
        
        from google.oauth2.credentials import Credentials
        credentials = Credentials(
            token=creds_info.get('token'),
            refresh_token=creds_info.get('refresh_token'),
            token_uri=creds_info.get('token_uri'),
            client_id=creds_info.get('client_id'),
            client_secret=creds_info.get('client_secret'),
            scopes=creds_info.get('scopes')
        )
       
        if credentials.expired and credentials.refresh_token:
            logger.debug(f"Refreshing expired credentials for user {telegram_id}")
            # Refresh is blocking network call
            await _run_in_executor(credentials.refresh, Request())
            await set_credentials(telegram_id, account_email, {
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'client_id': credentials.client_id,
                'client_secret': credentials.client_secret,
                'scopes': credentials.scopes
            })
       
        # Build is technically blocking but fast; service creation generally OK? 
        # Ideally run build in executor if it does discovery immediately.
        # But 'build' with static discovery or cache is better. For now, wrap it.
        service = await _run_in_executor(build, 'drive', 'v3', credentials=credentials)
        logger.debug(f"Successfully created Drive service for user {telegram_id}")
        return service
       
    except Exception as e:
        logger.error(f"Failed to get Drive service for user {telegram_id}: {str(e)}", exc_info=True)
        return None

async def get_folder_name(service, folder_id):
    if folder_id == "root":
        return "My Drive"
    try:
        def _execute_get():
            return service.files().get(fileId=folder_id, fields="name").execute()
        
        folder = await _run_in_executor(_execute_get)
        return folder["name"]
    except Exception as e:
        logger.warning(f"Failed to get folder name for {folder_id}: {e}")
        return "Unknown"


async def list_files(service, folder_id="root", page_token=None, page_size=10):
    if service is None:
        raise ValueError("Google Drive service is not available. Check credentials and login flow.")
   
    query = f"'{folder_id}' in parents and trashed=false"
    
    def _execute_list():
        return service.files().list(
            q=query,
            pageToken=page_token,
            pageSize=page_size,
            orderBy="folder,name",
            fields="nextPageToken, files(id,name,mimeType)"
        ).execute()

    res = await _run_in_executor(_execute_list)
   
    return res.get("files", []), res.get("nextPageToken")


async def list_trashed_files(service, page_token=None, page_size=1000):
    query = "trashed=true"
    
    def _execute_list():
        return service.files().list(
            q=query,
            pageToken=page_token,
            pageSize=page_size,
            fields="nextPageToken, files(id,name,mimeType,size)"
        ).execute()

    res = await _run_in_executor(_execute_list)
   
    return res.get("files", []), res.get("nextPageToken")


async def create_folder(service, name, parent_id=None):
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    if parent_id:
        metadata["parents"] = [parent_id]
   
    def _execute_create():
        return service.files().create(body=metadata, fields="id").execute()

    return await _run_in_executor(_execute_create)


async def rename_file(service, file_id, new_name):
    def _execute_update():
        return service.files().update(
            fileId=file_id,
            body={"name": new_name},
            fields="id,name"
        ).execute()

    return await _run_in_executor(_execute_update)


async def delete_file(service, file_id):
    def _execute_delete():
        service.files().update(fileId=file_id, body={"trashed": True}).execute()
        return True

    await _run_in_executor(_execute_delete)
    return True


async def toggle_sharing(service, file_id):
    def _execute_toggle():
        # Get current permissions to check sharing status
        permissions = service.permissions().list(fileId=file_id).execute().get('permissions', [])
        has_public = any(perm['type'] == 'anyone' for perm in permissions)
    
        if has_public:
            # Remove public access (make private)
            for perm in permissions:
                if perm['type'] == 'anyone':
                    service.permissions().delete(fileId=file_id, permissionId=perm['id']).execute()
        else:
            # Add public read access
            permissions_body = {'type': 'anyone', 'role': 'reader'}
            service.permissions().create(fileId=file_id, body=permissions_body).execute()

    await _run_in_executor(_execute_toggle)


async def get_file_link(service, file_id):
    def _execute_get():
        file = service.files().get(fileId=file_id, fields="webViewLink").execute()
        return file.get("webViewLink", "")

    return await _run_in_executor(_execute_get)


def extract_drive_file_id(url):
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None

async def get_storage_info(service):
    def _execute_about():
        return service.about().get(fields="storageQuota").execute()
    return await _run_in_executor(_execute_about)


async def get_user_info(service):
    def _execute_about():
        return service.about().get(fields="user").execute()
    return await _run_in_executor(_execute_about)


async def restore_file(service, file_id):
    def _execute_restore():
        return service.files().update(fileId=file_id, body={"trashed": False}, fields="id,name").execute()
    return await _run_in_executor(_execute_restore)


async def empty_trash(service):
    def _execute_empty():
        service.files().emptyTrash().execute()
        return True
    
    await _run_in_executor(_execute_empty)
    return True


async def list_trashed_files(service, page_token=None, page_size=100):
    def _execute_list():
        results = service.files().list(
            q="trashed=true",
            spaces='drive',
            fields="nextPageToken, files(id, name, mimeType, size)",
            pageToken=page_token,
            pageSize=page_size
        ).execute()
        return results.get('files', []), results.get('nextPageToken', None)
    return await _run_in_executor(_execute_list)


def _get_folder_size_sync(service, folder_id: str) -> int:
    """Recursively sum file sizes for a Drive folder.

    Traverses folder tree depth-first using Drive's files().list API.
    Only non-folder files contribute bytes; sub-folders are recursed into.
    Not an API-efficient approach for huge trees, but correct and safe for
    pre-production scale with per-user folder structures.
    """
    total = 0
    page_token = None
    while True:
        kwargs = dict(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, mimeType, size)",
            pageSize=1000,
        )
        if page_token:
            kwargs["pageToken"] = page_token
        res = service.files().list(**kwargs).execute()
        for f in res.get("files", []):
            if f["mimeType"] == "application/vnd.google-apps.folder":
                total += _get_folder_size_sync(service, f["id"])
            else:
                try:
                    total += int(f.get("size", 0) or 0)
                except (ValueError, TypeError):
                    pass
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return total


async def size_fetcher(update, ctx):
    """Handle file_size: and folder_size: callback queries.

    Folder sizes are computed recursively via the Drive API and cached for
    _FOLDER_SIZE_TTL seconds to avoid repeated expensive traversals.
    """

    @handle_errors
    async def _size_fetcher_impl(update, ctx):
        ctx.user_data = ctx.user_data or {}
        q = update.callback_query
        if not q or not q.from_user or not q.data:
            return

        is_file, item_id = _parse_size_query(q.data)
        if not item_id:
            return

        service = await _init_drive_service_for_query(q, ctx)
        if not service:
            return

        try:
            if is_file:
                await _handle_file_size_query(service, q, item_id)
            else:
                await _handle_folder_size_query(service, q, item_id)
        except Exception as e:
            kind = "file" if is_file else "folder"
            logger.error(f"[DRIVE] Failed to get {kind} size for {item_id}: {e}", exc_info=True)
            await q.edit_message_text(f"Failed to get {kind} size: {e}")

    async def _init_drive_service_for_query(q, ctx):
        telegram_id = q.from_user.id
        current_account = ctx.user_data.get("current_account")
        creds = await get_credentials(telegram_id, current_account)

        if not creds:
            await q.edit_message_text("Service unavailable. Please try again later.")
            return None

        credentials_obj = Credentials(
            token=creds.get('token'), refresh_token=creds.get('refresh_token'),
            token_uri=creds.get('token_uri'), client_id=creds.get('client_id'),
            client_secret=creds.get('client_secret'), scopes=creds.get('scopes')
        )
        return await _run_in_executor(build, "drive", "v3", credentials=credentials_obj)

    def _parse_size_query(data: str) -> tuple[bool, str]:
        if data.startswith("file_size:"):
            return True, data.split(":")[1]
        elif data.startswith("folder_size:"):
            return False, data.split(":")[1]
        return False, ""

    async def _handle_file_size_query(service, q, item_id):
        def _get_size():
            return service.files().get(fileId=item_id, fields="name,size").execute()

        file = await _run_in_executor(_get_size)
        size = int(file.get('size', 0) or 0)
        size_str = humanize.naturalsize(size, binary=True)
        await q.edit_message_text(f"📄 File size: <b>{size_str}</b>", parse_mode='HTML')

    async def _handle_folder_size_query(service, q, item_id):
        cached = _folder_size_cache.get(item_id)
        if cached and (time.time() - cached[1]) < _FOLDER_SIZE_TTL:
            total_bytes, _ = cached
            logger.debug(f"[DRIVE] Folder size cache hit for {item_id}")
        else:
            await q.edit_message_text("⏳ Calculating folder size...")
            logger.info(f"[DRIVE] Computing recursive folder size for {item_id}")
            total_bytes = await _run_in_executor(_get_folder_size_sync, service, item_id)
            _folder_size_cache[item_id] = (total_bytes, time.time())
            logger.info(f"[DRIVE] Folder {item_id} size: {total_bytes} bytes")

        size_str = humanize.naturalsize(total_bytes, binary=True)
        await q.edit_message_text(f"📁 Folder size: <b>{size_str}</b>", parse_mode='HTML')

    return await _size_fetcher_impl(update, ctx)
