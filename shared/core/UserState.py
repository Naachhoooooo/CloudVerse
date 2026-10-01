from enum import IntEnum, auto

class UserStateEnum(IntEnum):
    MENU = auto()
    ADMIN_CONTROL = auto()
    PERFORMANCE_PANEL = auto()
    MAINTENANCE_MENU = auto()
    EXPECTING_MAINTENANCE_ALERT = auto()
    TERMS = auto()
    FILE_MANAGER = auto()
    EXPECTING_CODE = auto()
    EXPECTING_MEGA_EMAIL = auto()
    EXPECTING_MEGA_PASSWORD = auto()
    EXPECTING_MEGA_2FA = auto()
    EXPECTING_APPROVAL_MESSAGE = auto()
    EXPECTING_ADMIN_USERNAME = auto()
    EXPECTING_WHITELIST_USERNAME = auto()
    EXPECTING_LIMIT_HOURS = auto()
    EXPECTING_PARALLEL_TRANSFERS = auto()
    EXPECTING_RCLONE_CONFIG = auto()

class UserState:
    def __init__(self, ctx):
        self.ctx = ctx
        self.data = {}
        
    def is_state(self, state: UserStateEnum) -> bool:
        if self.ctx.user_data is None:
            return False
        return self.ctx.user_data.get("current_state") == state
        
    def set_state(self, state: UserStateEnum) -> None:
        if self.ctx.user_data is None:
            self.ctx.user_data = {}
        self.ctx.user_data["current_state"] = state
        
    def reset(self) -> None:
        if self.ctx.user_data is not None:
            self.ctx.user_data.pop("current_state", None)
            self.ctx.user_data.pop("next_action", None)
            self.data = {}
