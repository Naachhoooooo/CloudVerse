from typing import Optional, List
from telegram import InlineKeyboardButton

# Master mapping of string values to hours
TIME_MAP = {
    '1 Hr': 1, '3 Hr': 3, '6 Hr': 6,
    '12 Hr': 12, '1 Day': 24, '2 Day': 48,
    '1 Week': 168, '1 Month': 720,
}

# The layout shape for the keyboard rows
_KEYBOARD_LAYOUT = [
    ['1 Hr', '3 Hr', '6 Hr'],
    ['12 Hr', '1 Day', '2 Day'],
    ['1 Week', '1 Month']
]

def parse_duration_to_hours(duration_str: str) -> Optional[int]:
    """Parse duration strings like '1 Day', '3 Hr', etc. to hours."""
    if duration_str == 'Permanent':
        return None
    return TIME_MAP.get(duration_str)

def format_duration_from_hours(hours: Optional[int]) -> str:
    """Convert hours back to a human-readable duration string."""
    if hours is None:
        return "Permanent"
    if hours < 24:
        return f"{hours} Hr"
    if hours < 168:
        days = hours // 24
        # Fallback to hours if the math isn't perfectly divisible by days
        return f"{days} Day" if hours % 24 == 0 else f"{hours} Hr"
    if hours < 720:
        return f"{hours // 168} Week"
    return f"{hours // 720} Month"

def _build_time_keyboard(prefix: str, include_permanent: bool) -> List[List[InlineKeyboardButton]]:
    """Helper to dynamically generate time-selection keyboards."""
    keyboard = []
    
    # Loop through our predefined layout and construct the buttons
    for row_labels in _KEYBOARD_LAYOUT:
        row = [InlineKeyboardButton(label, callback_data=f"{prefix}_{label}") for label in row_labels]
        keyboard.append(row)
    
    # Inject 'Permanent' into the final time row if requested
    if include_permanent:
        keyboard[-1].append(InlineKeyboardButton("Permanent", callback_data=f"{prefix}_Permanent"))
        
    # Standard back button
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="back_to_access")])
    return keyboard

def get_duration_buttons() -> List[List[InlineKeyboardButton]]:
    """Get duration selection buttons (includes Permanent)."""
    return _build_time_keyboard(prefix="duration", include_permanent=True)

def get_limit_buttons() -> List[List[InlineKeyboardButton]]:
    """Get limit selection buttons (excludes Permanent)."""
    return _build_time_keyboard(prefix="limit", include_permanent=False)
