import os
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.utils.pagination import Paginator

from shared.core.Logger import get_logger
logger = get_logger(__name__)

ROOT_DIR = Path(__file__).parent.parent.parent
_ALLOWED_DOMAIN_PATH = str(ROOT_DIR / "data" / "AllowedDomains.md")

def init_domain_manager(path: str):
    # Ignored: Domain management is now global.
    pass

_DOMAIN_FILE_HEADER = "# Allowed Streaming/Media Domains\n\n"


def _read_domains() -> list[str]:
    """Read and return the list of allowed domains from the config file."""
    if not _ALLOWED_DOMAIN_PATH or not os.path.exists(_ALLOWED_DOMAIN_PATH):
        return []
    with open(_ALLOWED_DOMAIN_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _write_domains(domains: list[str]) -> None:
    """Overwrite the allowed domains config file with the given list."""
    if not _ALLOWED_DOMAIN_PATH:
        return
    os.makedirs(os.path.dirname(_ALLOWED_DOMAIN_PATH), exist_ok=True)
    with open(_ALLOWED_DOMAIN_PATH, "w", encoding="utf-8") as f:
        f.write(_DOMAIN_FILE_HEADER)
        for domain in domains:
            f.write(domain + "\n")


def get_allowed_domains() -> list[str]:
    """Public wrapper to get allowed domains."""
    try:
        return _read_domains()
    except Exception as e:
        logger.error(f"Failed to read allowed domains: {e}")
        return ['youtube.com', 'vimeo.com']


from shared.utils.url_utils import is_direct_file_url as _is_direct_file_url, is_streaming_site as _is_streaming_site

def is_direct_file_url(url: str) -> bool:
    return _is_direct_file_url(url)

def is_streaming_site(url: str) -> bool:
    """Check if URL is from an allowed streaming site based on AllowedDomains.md."""
    allowed_domains = get_allowed_domains()
    return _is_streaming_site(url, allowed_domains)

def add_allowed_domain(domain: str) -> bool:
    """Add a domain. Returns True if added, False if it was already there."""
    domains = _read_domains()
    if domain in domains:
        return False
    domains.append(domain)
    _write_domains(domains)
    return True

def remove_allowed_domain(domain: str) -> bool:
    """Remove a domain. Returns True if removed, False if it wasn't there."""
    domains = _read_domains()
    if domain in domains:
        domains.remove(domain)
        _write_domains(domains)
        return True
    return False

