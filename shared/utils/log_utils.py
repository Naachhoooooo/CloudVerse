import re

# Standard Regexes to find secrets
SECRET_REGEXES = {
    'session_token': r'[a-zA-Z0-9\-\_]{100,}',
    'phone_code': r'\b\d{5,6}\b',
    'api_key': r'(?i)(api[_-]?key|secret|token|password)[\s:=]+[\'"]?[a-zA-Z0-9\-\_]{16,}[\'"]?',  # nosec B105 B107
    'email': r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
    'telegram_id': r'\b\d{8,11}\b'
}

def scrub_log_line(line: str) -> str:
    """Scrub a single log line against known secret regexes."""
    scrubbed_line = line
    for key, pattern in SECRET_REGEXES.items():
        if key == 'telegram_id':
             # Skip telegram ID since it's practically public and needed for debugging
             continue
        scrubbed_line = re.sub(pattern, f'<SCRUBBED_{key.upper()}>', scrubbed_line)
    return scrubbed_line
