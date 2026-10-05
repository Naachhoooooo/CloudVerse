"""
Logger — provider-aware logging kernel for all CloudVerse bots.

Each bot calls setup_logging(provider=...) once at startup. This creates:
    logs/
    ├── <provider>/
    │   ├── bot.log           — general bot flow, commands, user interactions
    │   ├── actions.log       — user/system triggered actions
    │   ├── auth.log          — auth events (if provider in gdrive, mega, rclone)
    │   ├── transfers.log     — transfer lifecycle events (if provider in gdrive, mega, rclone)
    │   └── errors.log        — failures and exceptions for this provider
    ├── database.log          — shared DB operations across all backends
    ├── system_actions.log    — server-level events across all bots
    └── errors.log            — aggregated critical errors from all backends

All handlers are TimedRotatingFileHandler (midnight rollover, 30-day retention).
All formatters use JsonSecurityAwareFormatter to output structured JSON and redact secrets.
"""

import json
import logging
import logging.handlers
import queue
import os
import re
import sys
import contextvars
from pathlib import Path
from typing import Optional, Dict, Any, Literal

trace_id_var = contextvars.ContextVar('trace_id', default=None)
telegram_id_var = contextvars.ContextVar('telegram_id', default=None)
username_var = contextvars.ContextVar('username', default=None)

# Read LOG_LEVEL directly from env — no bot-specific config import here
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

LOGS_ROOT = Path(__file__).parent.parent.parent / "logs"

PROVIDER_TYPE = Literal["gdrive", "mega", "rclone", "administrator"]

# ── Providers that support transfers.log and auth.log ───────────────────────────
_UPLOAD_CAPABLE_PROVIDERS = {"gdrive", "mega", "rclone"}

# ── Secrets scrubber patterns (per memory requirement) ────────────────────────
_SCRUB_PATTERNS = [
    (re.compile(r'(?i)(token|password|key|secret|credential|auth|api_key|'
                r'access_token|refresh_token|session_id|bot_token|'
                r'encryption_key|salt)\s*[=:]\s*\S+'), r'\1=***REDACTED***'),
    # E.164 phone numbers
    (re.compile(r'\+\d{7,15}'), '***PHONE***'),
]

LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}


# ── Sensitive-data scrubber ───────────────────────────────────────────────────

def _scrub(text: str) -> str:
    """Apply all secret-scrubbing regexes to a log line."""
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_sensitive_data(data: Any) -> Any:
    """Recursively sanitize dicts/lists/strings for logging."""
    if isinstance(data, dict):
        return {k: sanitize_sensitive_data(v) for k, v in data.items()}
    elif isinstance(data, str):
        return _scrub(data)
    elif isinstance(data, (list, tuple)):
        return type(data)(sanitize_sensitive_data(i) for i in data)
    return data


def _get_tag(record: logging.LogRecord) -> str:
    """Determine the prefix tag based on the logger module name."""
    name = record.name.lower()
    if 'upload' in name or 'transfer' in name or 'download' in name:
        return '[UPLOAD]'
    elif 'auth' in name or 'security' in name or 'credential' in name:
        return '[AUTH]'
    elif 'mega' in name:
        return '[MEGA]'
    elif 'rclone' in name:
        return '[RCLONE]'
    elif 'drive' in name or 'gdrive' in name:
        return '[DRIVE]'
    elif 'administrator' in name:
        return '[ADMINISTRATOR]'
    elif 'database' in name or 'repository' in name or 'sql' in name:
        return '[SYSTEM]'
    elif 'admin' in name or 'system' in name:
        return '[SYSTEM]'
    return '[BOT]'


# ── Formatters ────────────────────────────────────────────────────────────────

class JsonSecurityAwareFormatter(logging.Formatter):
    """Formatter that outputs structured JSON, scrubbing sensitive data and adding tags."""

    def format(self, record: logging.LogRecord) -> str:
        # Prevent mutating the original record
        record_copy = logging.makeLogRecord(record.__dict__)
        
        # Add tag
        tag = _get_tag(record_copy)
        original_msg = str(record_copy.msg)
        if not original_msg.startswith('['):
            record_copy.msg = f"{tag} {original_msg}"
            
        msg = record_copy.msg
        if isinstance(msg, str):
            record_copy.msg = _scrub(msg)
            
        if record_copy.args:
            record_copy.args = sanitize_sensitive_data(record_copy.args)
            
        log_entry = {
            "timestamp": self.formatTime(record_copy, self.datefmt),
            "level": record_copy.levelname,
            "module": record_copy.name,
            "function_name": f"{record_copy.funcName}",
            "message": record_copy.getMessage()
        }
        
        trace_id = trace_id_var.get()
        if trace_id:
            log_entry["trace_id"] = trace_id
            
        telegram_id = telegram_id_var.get()
        if telegram_id:
            log_entry["telegram_id"] = telegram_id
            
        username = username_var.get()
        if username:
            log_entry["username"] = username
        
        if record_copy.exc_info:
            log_entry["traceback"] = self.formatException(record_copy.exc_info)
            
        if hasattr(record_copy, "details") and record_copy.details:
            log_entry["details"] = sanitize_sensitive_data(record_copy.details)

        return json.dumps(log_entry, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """SecurityAwareFormatter with ANSI colour codes and tags for console output."""
    _COLORS = {
        'DEBUG':    '\033[36m',
        'INFO':     '\033[32m',
        'WARNING':  '\033[33m',
        'ERROR':    '\033[31m',
        'CRITICAL': '\033[35m',
        'RESET':    '\033[0m',
    }

    def format(self, record: logging.LogRecord) -> str:
        record_copy = logging.makeLogRecord(record.__dict__)
        
        # Add tag
        tag = _get_tag(record_copy)
        original_msg = str(record_copy.msg)
        if not original_msg.startswith('['):
            record_copy.msg = f"{tag} {original_msg}"
            
        msg = record_copy.msg
        if isinstance(msg, str):
            record_copy.msg = _scrub(msg)
            
        if record_copy.args:
            record_copy.args = sanitize_sensitive_data(record_copy.args)
        
        formatted = super().format(record_copy)
        trace_id = trace_id_var.get()
        telegram_id = telegram_id_var.get()
        username = username_var.get()
        
        prefix_parts = []
        if trace_id:
            prefix_parts.append(f"tid={trace_id}")
        if telegram_id:
            user_str = f"u={telegram_id}"
            if username:
                user_str += f"(@{username})"
            prefix_parts.append(user_str)
            
        if prefix_parts:
            formatted = f"[{' | '.join(prefix_parts)}] {formatted}"
            
        color = self._COLORS.get(record_copy.levelname, '')
        reset = self._COLORS['RESET']
        return f"{color}{formatted}{reset}" if color else formatted


# ── Common formatter instances ────────────────────────────────────────────────
_DATE_FMT     = '%Y-%m-%dT%H:%M:%S%z'
_CONSOLE_FMT  = '%(asctime)s | %(levelname)-7s | %(name)s | %(message)s'
_CONSOLE_DATE = '%H:%M:%S'


def cleanup_old_logs(log_dir: Path, days: int = 5) -> None:
    """Manually delete logs older than the specified number of days."""
    import time
    if not log_dir.exists():
        return
    now = time.time()
    cutoff = now - (days * 86400)
    for p in log_dir.glob("*.log"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            try:
                p.unlink()
            except Exception:
                pass

def _make_rotating_handler(path: Path, level: int,
                           formatter: logging.Formatter) -> logging.FileHandler:
    """Create a run-specific file handler and clean up old logs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Clean up logs older than 3 days
    cleanup_old_logs(path.parent, days=3)
    
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    new_name = f"{path.stem}_{timestamp}{path.suffix}"
    run_path = path.parent / new_name
    
    from logging.handlers import TimedRotatingFileHandler
    h = TimedRotatingFileHandler(str(run_path), when="midnight", interval=1, backupCount=3, encoding='utf-8')
    h.setLevel(level)
    h.setFormatter(formatter)
    return h


# ── Log-level filter helpers ──────────────────────────────────────────────────

def _db_filter(record: logging.LogRecord) -> bool:
    name = record.name.lower()
    return 'database' in name or '.db' in name or 'repository' in name or 'sql' in name

def _upload_filter(record: logging.LogRecord) -> bool:
    name = record.name.lower()
    return any(k in name for k in ('upload', 'transfer', 'download', 'drive', 'mega', 'rclone'))

def _system_actions_filter(record: logging.LogRecord) -> bool:
    msg = record.getMessage().lower()
    return any(k in msg for k in (
        'admin', 'whitelist', 'blacklist', 'ban', 'promote', 'demote',
        'approved', 'rejected', 'broadcast', 'system_action'
    ))

def _actions_filter(record: logging.LogRecord) -> bool:
    msg = record.getMessage().lower()
    return any(k in msg for k in ('user action', 'action:'))

def _auth_filter(record: logging.LogRecord) -> bool:
    name = record.name.lower()
    msg = record.getMessage().lower()
    return 'auth' in name or 'token' in name or 'login' in msg or 'credential' in name


# ── Per-provider registry ─────────────────────────────────────────────────────

_configured_providers: set = set()
_log_queue = queue.Queue(-1)
_log_listener = None


def setup_logging(provider: PROVIDER_TYPE = "gdrive") -> None:
    """
    Configure logging for the given provider. Idempotent — calling twice
    with the same provider is a no-op.
    """
    if provider in _configured_providers:
        return
    _configured_providers.add(provider)

    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    log_dir = LOGS_ROOT / provider
    log_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = LOG_LEVELS.get(LOG_LEVEL, logging.INFO)

    json_fmt = JsonSecurityAwareFormatter(datefmt=_DATE_FMT)
    colored_fmt = ColoredFormatter(fmt=_CONSOLE_FMT, datefmt=_CONSOLE_DATE)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers control effective levels

    global _log_listener
    handlers_to_add = []

    # Console — only set up once
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers) and _log_listener is None:
        if sys.platform == 'win32':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
                sys.stderr.reconfigure(encoding='utf-8')
            except Exception:
                pass
        console_h = logging.StreamHandler(sys.stdout)
        console_h.setLevel(numeric_level)
        console_h.setFormatter(colored_fmt)
        handlers_to_add.append(console_h)
        
        # Quiet down extremely noisy third-party loggers globally
        for noisy in ['httpx', 'httpcore', 'apscheduler', 'telegram', 'urllib3']:
            logging.getLogger(noisy).setLevel(logging.WARNING)

    # ── Provider Specific Logs ────────────────────────────────────────────────
    
    # ── bot.log — all INFO+
    handlers_to_add.append(_make_rotating_handler(
        log_dir / 'bot.log', logging.INFO, json_fmt
    ))

    # ── errors.log (Provider only) — ERROR+
    prov_err_h = _make_rotating_handler(log_dir / 'errors.log', logging.ERROR, json_fmt)
    prov_err_h.addFilter(lambda r: provider in r.name.lower() or 'cloudverse.' not in r.name.lower()) # provider specific + unknown
    handlers_to_add.append(prov_err_h)

    # ── events.log — user/system triggered actions
    act_h = _make_rotating_handler(log_dir / 'events.log', logging.INFO, json_fmt)
    act_h.addFilter(_actions_filter)
    handlers_to_add.append(act_h)

    if provider in _UPLOAD_CAPABLE_PROVIDERS:
        # ── auth.log — auth events
        auth_h = _make_rotating_handler(log_dir / 'auth.log', logging.INFO, json_fmt)
        auth_h.addFilter(_auth_filter)
        handlers_to_add.append(auth_h)

        # ── transfers.log — transfer layer
        up_h = _make_rotating_handler(log_dir / 'transfers.log', logging.INFO, json_fmt)
        up_h.addFilter(_upload_filter)
        handlers_to_add.append(up_h)

    # ── Shared Logs (Root) ────────────────────────────────────────────────────
    
    # ── database.log — DB layer only, DEBUG+
    db_h = _make_rotating_handler(LOGS_ROOT / 'database.log', logging.DEBUG, json_fmt)
    db_h.addFilter(_db_filter)
    handlers_to_add.append(db_h)

    # ── system_actions.log — admin/access events
    sys_h = _make_rotating_handler(LOGS_ROOT / 'system_actions.log', logging.INFO, json_fmt)
    sys_h.addFilter(_system_actions_filter)
    handlers_to_add.append(sys_h)

    # ── aggregated errors.log — ERROR+ across ALL backends
    agg_err_h = _make_rotating_handler(LOGS_ROOT / 'errors.log', logging.ERROR, json_fmt)
    handlers_to_add.append(agg_err_h)

    # Apply all handlers via QueueListener
    if _log_listener is None:
        root.addHandler(logging.handlers.QueueHandler(_log_queue))
        _log_listener = logging.handlers.QueueListener(_log_queue, *handlers_to_add, respect_handler_level=True)
        _log_listener.start()
    else:
        # Just to be safe, if we call this multiple times with different providers, we add to listener
        for h in handlers_to_add:
            _log_listener.handlers = tuple(list(_log_listener.handlers) + [h])

    _init_logger = logging.getLogger(f'cloudverse.{provider}.logger')
    _init_logger.info(f"Logging initialized for provider='{provider}' → logs/{provider}/")
    _init_logger.info(f"Log level: {LOG_LEVEL}")


# ── Public API ────────────────────────────────────────────────────────────────

def get_logger(name: str = None) -> logging.Logger:
    """
    Return a named logger. If no name given, uses the caller's __name__.
    """
    if name is None:
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            name = frame.f_back.f_globals.get('__name__', 'cloudverse')
    return logging.getLogger(name or 'cloudverse')


# ── Legacy pre-configured loggers (import-time) removed per architecture rules ──

# ── CloudVerseLogger class shim ───────────────────────────────────────────────
class CloudVerseLogger:
    """Backwards-compatible shim — all callers should prefer get_logger()."""

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        return get_logger(name)

    @classmethod
    def log_exception(cls, logger: logging.Logger, message: str = "An error occurred",
                      exc_info: bool = True) -> None:
        logger.error(message, exc_info=exc_info)

    @classmethod
    def log_user_action(cls, logger: logging.Logger, user_id: str, action: str,
                        details: Optional[Dict[str, Any]] = None) -> None:
        sanitized = sanitize_sensitive_data(details or {})
        logger.info(f"User Action: {action} | User: {user_id}", extra={'details': sanitized})

    @classmethod
    def log_security_event(cls, logger: logging.Logger, event_type: str,
                           user_id: Optional[str] = None,
                           details: Optional[Dict[str, Any]] = None) -> None:
        sanitized = sanitize_sensitive_data(details or {})
        msg = f"Security Event: {event_type}"
        if user_id:
            msg += f" | User: {user_id}"
        logger.warning(msg, extra={'details': sanitized})

    @classmethod
    def log_performance(cls, logger: logging.Logger, operation: str, duration: float,
                        details: Optional[Dict[str, Any]] = None) -> None:
        sanitized = sanitize_sensitive_data(details or {})
        logger.info(f"Performance: {operation} completed in {duration:.2f}s", extra={'details': sanitized})


# ── Convenience functions ─────────────────────────────────────────────────────

def log_exception(message: str = "An error occurred", exc_info: bool = True,
                  logger_name: str = None) -> None:
    get_logger(logger_name).error(message, exc_info=exc_info)


def log_user_action(user_id: str, action: str, details: Optional[Dict[str, Any]] = None,
                    logger_name: str = None) -> None:
    CloudVerseLogger.log_user_action(get_logger(logger_name), user_id, action, details)


def log_security_event(event_type: str, user_id: Optional[str] = None,
                       details: Optional[Dict[str, Any]] = None, logger_name: str = None) -> None:
    CloudVerseLogger.log_security_event(get_logger(logger_name), event_type, user_id, details)


def log_performance(operation: str, duration: float, details: Optional[Dict[str, Any]] = None,
                    logger_name: str = None) -> None:
    CloudVerseLogger.log_performance(get_logger(logger_name), operation, duration, details)


__all__ = [
    'setup_logging',
    'get_logger',
    'CloudVerseLogger',
    'sanitize_sensitive_data',
    'log_exception',
    'log_user_action',
    'log_security_event',
    'log_performance',
]
