def is_direct_file_url(url: str) -> bool:
    file_extensions = [
        '.pdf', '.zip', '.rar', '.tar', '.gz', '.jpg', '.jpeg', '.png', '.gif', '.mp4', '.mp3', '.doc', '.docx',
        '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.json', '.xml', '.7z', '.apk', '.exe', '.msi', '.dmg', '.iso'
    ]
    return any(url.lower().endswith(ext) for ext in file_extensions)

def is_streaming_site(url: str, allowed_domains: list[str]) -> bool:
    """Check if URL is from an allowed streaming site."""
    return any(domain in url.lower() for domain in allowed_domains)
