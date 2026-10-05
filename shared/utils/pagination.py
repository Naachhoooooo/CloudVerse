from telegram import InlineKeyboardButton

class Paginator:
    """
    Standard centralized pagination utility.
    Encapsulates logic for page clamping, data slicing, and InlineKeyboardButton generation.
    """
    def __init__(self, items: list, page: int = 0, page_size: int = 10, total_items: int = None):
        self._all_items = items if items else []
        self.page_size = max(1, page_size)
        
        self.is_pre_sliced = total_items is not None
        self.total_items = total_items if self.is_pre_sliced else len(self._all_items)
        
        # Calculate total pages (minimum 1, even if empty)
        self.total_pages = max(1, (self.total_items + self.page_size - 1) // self.page_size)
        
        # Clamp page index to [0, total_pages - 1]
        self.current_page = max(0, min(page, self.total_pages - 1))
        
        # Boundary indices
        if not self.is_pre_sliced:
            self.start_idx = self.current_page * self.page_size
            self.end_idx = min(self.start_idx + self.page_size, self.total_items)
        else:
            self.start_idx = 0
            self.end_idx = len(self._all_items)

    def set_items(self, items: list):
        self._all_items = items if items else []

    @property
    def items(self) -> list:
        """Returns the slice of items for the current page."""
        if self.is_pre_sliced:
            return self._all_items
        return self._all_items[self.start_idx:self.end_idx]

    def get_buttons(self, prev_callback: str = None, next_callback: str = None, page_callback: str = "noop") -> list:
        """
        Generates the standard pagination button row.
        Only renders if there is more than 1 page.
        """
        buttons = []
        if self.total_pages <= 1:
            return buttons

        if self.current_page > 0 and prev_callback:
            buttons.append(InlineKeyboardButton("Prev", callback_data=prev_callback))

        # Current page indication
        page_text = f"{self.current_page + 1} / {self.total_pages}"
        buttons.append(InlineKeyboardButton(page_text, callback_data=page_callback))

        if self.current_page < self.total_pages - 1 and next_callback:
            buttons.append(InlineKeyboardButton("Next", callback_data=next_callback))

        return buttons

def format_user_list_label(user_dict: dict, include_emoji: bool = True) -> str:
    """
    Standardizes how user names are displayed in long lists (e.g., Admin Menus).
    Format: [Emoji] Name (@username) - ID
    Or gracefully falls back if username or name is missing.
    """
    username = user_dict.get("username")
    name = user_dict.get("name")
    uid = user_dict.get("telegram_id")
    
    if username:
        display = f"@{username} - {uid}"
    elif name:
        display = f"{name} - {uid}"
    else:
        display = f"User - {uid}"
        
    if include_emoji:
        emoji = user_dict.get("bot_role_emoji", "👤")
        return f"{emoji} {display}"
    return display
