def escape_markdown(text: str) -> str:
    if not isinstance(text, str):
        return text
    # Escape Telegram Markdown V2 special chars
    special = r'_[]()~`>#+-=|{}.!'
    result = ''
    for ch in text:
        if ch in special:
            result += '\\\\' + ch
        else:
            result += ch
    return result

def escape_html(text: str) -> str:
    if not isinstance(text, str):
        return text
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;'))
