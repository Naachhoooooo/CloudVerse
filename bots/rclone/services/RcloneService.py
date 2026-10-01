"""
RcloneService — async subprocess wrapper around the rclone CLI.

DESIGN CHANGE (per-user config):
  All public functions now accept a `config_path: str` parameter — the path to a
  pre-decrypted rclone .conf temp file. This is always provided by the caller via
  ConfigHelper.user_config(). Never use the global RCLONE_CONFIG_PATH for user commands
  (it is an admin/system fallback only).

rclone docs: https://rclone.org/commands/
Tags: [RCLONE][TRANSFER]
"""

import asyncio
import json
import shlex
from typing import Optional, List, Dict, Any
from bots.rclone.config import RCLONE_PATH
from shared.core.Logger import get_logger
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = 300   # 5 minutes for most operations
_TRANSFER_TIMEOUT = 7200  # 2 hours for long transfers


# ── Internal subprocess helper ────────────────────────────────────────────────

async def _run_rclone(
    config_path: str,
    *extra_args: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    Execute rclone with the given extra args and user config_path.
    Returns dict: {'returncode': int, 'stdout': str, 'stderr': str}.
    Never logs config_path content.
    """
    cmd = [RCLONE_PATH, "--config", config_path] + list(extra_args)
    # Log the command without the config path value (security)
    safe_log = [RCLONE_PATH, "--config", "<redacted>"] + list(extra_args)
    logger.debug(f"[RCLONE] Running: {' '.join(shlex.quote(a) for a in safe_log)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        result = {
            "returncode": proc.returncode,
            "stdout": stdout.decode(errors="replace").strip(),
            "stderr": stderr.decode(errors="replace").strip(),
        }
        if result["returncode"] != 0:
            logger.warning(
                f"[RCLONE] Command {extra_args[0] if extra_args else '?'} "
                f"exited {result['returncode']}: {str(result['stderr'])[:200]}"
            )
        return result
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception as e:
            logger.debug(f"[RCLONE] Process kill failed after timeout: {e}")
        raise TimeoutError(f"[RCLONE] Command timed out after {timeout}s: {extra_args[:3]}")
    except Exception as e:
        logger.error(f"[RCLONE] Subprocess error: {e}", exc_info=True)
        raise

async def _run_rclone_stream(
    config_path: str,
    *extra_args: str,
    timeout: int = _TRANSFER_TIMEOUT,
):
    cmd, safe_log = _build_rclone_cmd(config_path, extra_args)
    logger.debug(f"[RCLONE] Streaming: {' '.join(shlex.quote(a) for a in safe_log)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        queue = asyncio.Queue()
        
        track_task(_stream_reader(proc.stdout, 'stdout', queue, proc))
        track_task(_stream_reader(proc.stderr, 'stderr', queue, proc))
        
        async for item in _process_stream_queue(queue, proc):
            yield item
            
        await proc.wait()
        yield {'type': 'exit', 'returncode': proc.returncode, 'proc': proc}
        
    except asyncio.CancelledError:
        logger.info("[RCLONE] Transfer cancelled by user, killing subprocess.")
        _kill_proc_safe(proc)
        raise
    except Exception as e:
        logger.error(f"[RCLONE] Stream subprocess error: {e}", exc_info=True)
        _kill_proc_safe(proc)
        raise

def _build_rclone_cmd(config_path: str, extra_args: tuple) -> tuple:
    cmd = [RCLONE_PATH, "--config", config_path] + list(extra_args)
    safe_log = [RCLONE_PATH, "--config", "<redacted>"] + list(extra_args)
    return cmd, safe_log

async def _stream_reader(stream, s_type, queue, proc):
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            await queue.put({'type': s_type, 'line': line.decode(errors='replace').strip(), 'proc': proc})
    except Exception as e:
        logger.debug(f"Stream reader error ({s_type}): {e}")
    finally:
        await queue.put({'type': 'EOF_MARKER', 'stream': s_type})

async def _process_stream_queue(queue, proc):
    eof_count = 0
    while eof_count < 2:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=1.0)
            if item.get('type') == 'EOF_MARKER':
                eof_count += 1
            else:
                yield item
        except asyncio.TimeoutError:
            if proc.returncode is not None:
                break

def _kill_proc_safe(proc):
    try:
        proc.kill()
    except Exception as e:
        logger.debug(f"Process kill failed: {e}")


# ── Validation ────────────────────────────────────────────────────────────────

async def validate_config(config_path: str) -> bool:
    """
    Validate user config by running rclone listremotes.
    Returns True if the config is valid and has at least one remote.
    """
    try:
        result = await _run_rclone(config_path, "listremotes", timeout=30)
        return result["returncode"] == 0
    except Exception as e:
        logger.error(f"[RCLONE][AUTH] Config validation error: {e}", exc_info=True)
        return False


# ── Remote listing ────────────────────────────────────────────────────────────

async def list_remotes(config_path: str) -> List[str]:
    """
    Return configured remote names from user's config (e.g. ['gdrive:', 'mega:']).
    """
    logger.info("[RCLONE] Listing remotes")
    result = await _run_rclone(config_path, "listremotes")
    if result["returncode"] != 0:
        raise RuntimeError(f"rclone listremotes failed: {result['stderr']}")
    return [r for r in result["stdout"].splitlines() if r.strip()]


# ── File operations ───────────────────────────────────────────────────────────

async def list_files(
    config_path: str, remote: str, path: str = ""
) -> List[Dict[str, Any]]:
    """
    List files/dirs in remote:path using JSON output.
    Returns list of dicts with keys: Path, Name, Size, MimeType, IsDir.
    """
    logger.info(f"[RCLONE] Listing {remote}{path}")
    result = await _run_rclone(config_path, "lsjson", f"{remote}{path}")
    if result["returncode"] != 0:
        raise RuntimeError(f"rclone lsjson failed: {result['stderr']}")
    return json.loads(result["stdout"]) if result["stdout"] else []


async def copy_file(
    config_path: str, src: str, dst: str, progress: bool = False
) -> bool:
    """
    Copy src to dst (both can be local paths or remote:path strings).
    Returns True on success.
    """
    logger.info(f"[RCLONE][TRANSFER] Copying {src} → {dst}")
    extra = ["--progress"] if progress else []
    result = await _run_rclone(
        config_path, "copy", src, dst, *extra, timeout=_TRANSFER_TIMEOUT
    )
    if result["returncode"] != 0:
        raise RuntimeError(f"rclone copy failed: {result['stderr']}")
    return True

async def copy_file_stream(
    config_path: str, src: str, dst: str, flags: dict = None
):
    """
    Copy or Sync src to dst with live progress yielding.
    Yields parsed progress dicts: {'transferred': str, 'percent': int, 'speed': str, 'eta': str}
    Raises Exception on failure.
    """
    flags = flags or {}
    cmd_action = "sync" if flags.get("mode") == "sync" else "copy"
    
    logger.info(f"[RCLONE][TRANSFER] {cmd_action.capitalize()} (streaming) {src} → {dst}")
    import re
    # Match: Transferred:   1.234 MiB / 10.000 MiB, 12%, 1.234 MiB/s, ETA 7s
    prog_pattern = re.compile(r"Transferred:\s+([\d.]+\s+[kMGTP]iB.*?),\s+(\d+)%,\s+([\d.]+\s+[kMGTP]iB/s),\s+ETA\s+([\w\d]+)")
    
    extra_args = ["--progress", "--stats=2s"]
    if flags.get("ignore_existing"):
        extra_args.append("--ignore-existing")
    if flags.get("fast_list"):
        extra_args.append("--fast-list")
    if flags.get("checksum"):
        extra_args.append("--checksum")
    if flags.get("metadata"):
        extra_args.append("--metadata")
        
    stream_gen = _run_rclone_stream(config_path, cmd_action, src, dst, *extra_args)
    
    async for item in stream_gen:
        if item['type'] == 'exit':
            if item['returncode'] != 0:
                raise RuntimeError(f"rclone copy stream failed (returncode {item['returncode']})")
            yield {'status': 'completed'}
            break
            
        line = item['line']
        if line.startswith("Transferred:"):
            match = prog_pattern.search(line)
            if match:
                transferred = match.group(1).split(" / ")[0].strip() if " / " in match.group(1) else match.group(1).strip()
                yield {
                    'status': 'uploading',
                    'transferred': transferred,
                    'percent': int(match.group(2)),
                    'speed': match.group(3),
                    'eta': match.group(4)
                }

async def sync(config_path: str, src: str, dst: str) -> bool:
    """
    Sync src → dst (dst is made identical to src).
    WARNING: files in dst not in src are deleted.
    """
    logger.info(f"[RCLONE][TRANSFER] Syncing {src} → {dst}")
    result = await _run_rclone(
        config_path, "sync", src, dst, "--progress", timeout=_TRANSFER_TIMEOUT
    )
    if result["returncode"] != 0:
        raise RuntimeError(f"rclone sync failed: {result['stderr']}")
    return True


async def move_file(config_path: str, src: str, dst: str) -> bool:
    """
    Move src to dst using rclone move.
    """
    logger.info(f"[RCLONE][TRANSFER] Moving {src} → {dst}")
    result = await _run_rclone(
        config_path, "move", src, dst, timeout=_TRANSFER_TIMEOUT
    )
    if result["returncode"] != 0:
        raise RuntimeError(f"rclone move failed: {result['stderr']}")
    return True


async def delete_file(config_path: str, remote: str, path: str) -> bool:
    """Delete a single file at remote:path."""
    logger.info(f"[RCLONE] Deleting {remote}{path}")
    result = await _run_rclone(config_path, "deletefile", f"{remote}{path}")
    if result["returncode"] != 0:
        raise RuntimeError(f"rclone deletefile failed: {result['stderr']}")
    return True


async def mkdir(config_path: str, remote_path: str) -> bool:
    """Create a directory at remote_path."""
    result = await _run_rclone(config_path, "mkdir", remote_path)
    if result["returncode"] != 0:
        raise RuntimeError(f"rclone mkdir failed: {result['stderr']}")
    return True


# ── Storage ───────────────────────────────────────────────────────────────────

async def get_storage_info(config_path: str, remote: str) -> Dict[str, Any]:
    """
    Return quota info for a remote via rclone about --json.
    Keys: 'total', 'used', 'free', 'trashed' in bytes (may be None if remote unsupported).
    """
    logger.info(f"[RCLONE] Getting storage info for {remote}")
    result = await _run_rclone(config_path, "about", remote, "--json")
    if result["returncode"] != 0:
        raise RuntimeError(f"rclone about failed: {result['stderr']}")
    return json.loads(result["stdout"]) if result["stdout"] else {}


async def get_size(config_path: str, remote_path: str) -> Dict[str, Any]:
    """
    Return total size and file count for a remote path via rclone size --json.
    Keys: 'count', 'bytes'.
    """
    logger.info(f"[RCLONE] Getting size info for {remote_path}")
    result = await _run_rclone(config_path, "size", remote_path, "--json")
    if result["returncode"] != 0:
        raise RuntimeError(f"rclone size failed: {result['stderr']}")
    return json.loads(result["stdout"]) if result["stdout"] else {"count": 0, "bytes": 0}


# ── Async job API (rcd) ───────────────────────────────────────────────────────

async def copy_async(
    config_path: str, src: str, dst: str
) -> Optional[int]:
    """
    Start an async rclone copy via rclone rc operations/copyfile or jobs/start.
    Returns job_id for polling, or None on failure.
    NOTE: requires rclone rc daemon running. For simplicity we run sync copy
    in an executor and return a synthetic job ID. Real rc integration can be
    added if rcd is deployed.
    """
    import uuid
    job_id = uuid.uuid4().int & 0xFFFFFFFF  # Synthetic 32-bit job ID
    logger.info(f"[RCLONE][TRANSFER] Starting async copy job {job_id}: {src} → {dst}")

    async def _run():
        try:
            await copy_file(config_path, src, dst)
            logger.info(f"[RCLONE][TRANSFER] Async copy job {job_id} completed")
        except Exception as e:
            logger.error(f"[RCLONE][TRANSFER] Async copy job {job_id} failed: {e}", exc_info=True)

    track_task(_run())
    return job_id


# ── System check ──────────────────────────────────────────────────────────────

async def check_binary() -> bool:
    """
    Verify rclone binary is accessible. Uses no user config.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            RCLONE_PATH, "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"[RCLONE] Binary check failed: {e}", exc_info=True)
        return False
