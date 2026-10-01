def get_role_emoji(role: str) -> str:
    """Returns the universally consistent emoji for a given user role."""
    emojis = {
        'super_admin': '👑',
        'admin': '⭐',
        'whitelisted': '✅',
        'blacklisted': '🚫',
        'pending': '⏳'
    }
    return emojis.get(role, '👤')
