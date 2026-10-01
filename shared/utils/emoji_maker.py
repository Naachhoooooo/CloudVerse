import os
from typing import Optional

def emoji_maker(mime_type: str, file_name: Optional[str] = None) -> str:
    """Return a display emoji for the given MIME type or file extension."""
    if not mime_type:
        mime_type = ""

    if mime_type == "application/vnd.google-apps.folder":
        return "\U0001f4c1"  # folder

    if mime_type.startswith("image/"):
        return "\U0001f5bc\ufe0f"  # frame with picture
    if mime_type.startswith("video/"):
        return "\U0001f3ac"  # clapper board
    if mime_type.startswith("audio/"):
        return "\U0001f3b5"  # music note
    if mime_type in ("application/pdf",):
        return "\U0001f4cb"  # clipboard
    if mime_type in ("application/zip", "application/x-zip-compressed",
                     "application/x-rar-compressed", "application/x-7z-compressed",
                     "application/x-tar", "application/gzip"):
        return "\U0001f4e6"  # package
    if mime_type in ("text/plain", "application/json", "application/xml", "text/markdown"):
        return "\U0001f4dd"  # memo

    if file_name:
        _, ext = os.path.splitext(file_name.lower())
        _ext_map = {
            frozenset((".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md")): "\U0001f4dd",
            frozenset((".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff")): "\U0001f5bc\ufe0f",
            frozenset((".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv", ".webm")): "\U0001f3ac",
            frozenset((".mp3", ".wav", ".ogg", ".m4a", ".flac")): "\U0001f3b5",
            frozenset((".zip", ".rar", ".7z", ".tar", ".gz", ".bz2")): "\U0001f4e6",
        }
        for exts, emoji in _ext_map.items():
            if ext in exts:
                return emoji

    return "\U0001f4c4"  # default: page facing up
