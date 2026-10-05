from typing import Optional, Dict, Any, Callable
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
from telegram import Update
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger

logger = get_logger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels for appropriate handling."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """Error categories for better organization."""
    NETWORK = "network"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RESOURCE = "resource"
    VALIDATION = "validation"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass
class ErrorInfo:
    """Structured error information."""
    error: Exception
    severity: ErrorSeverity
    category: ErrorCategory
    context: Dict[str, Any]
    user_id: Optional[int] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class ErrorHandler:
    """
    Comprehensive error handling system with user-friendly messages
    and automatic recovery strategies.

    Args:
        on_log_to_db: Optional async callback for persisting error events to the
                      bot's database. Signature:
                          async on_log_to_db(telegram_id, action_taken, status,
                                             event_details, notes) -> None
                      If None, error events are only written to the log file.
    """

    def __init__(self, on_log_to_db: Optional[Callable] = None):
        self.error_counts: Dict[str, int] = {}
        self.user_error_history: Dict[int, list] = {}
        self._on_log_to_db: Optional[Callable] = on_log_to_db
    
    def classify_error(self, error: Exception) -> ErrorCategory:
        """Classify error based on type and message."""
        error_str = str(error).lower()
        
        # Network errors
        if any(keyword in error_str for keyword in ['connection', 'timeout', 'network', 'dns', 'socket']):
            return ErrorCategory.NETWORK
        
        # Authentication errors
        if any(keyword in error_str for keyword in ['auth', 'login', 'token', 'credential', 'unauthorized']):
            return ErrorCategory.AUTHENTICATION
        
        # Permission errors
        if any(keyword in error_str for keyword in ['permission', 'access', 'forbidden', 'denied']):
            return ErrorCategory.PERMISSION
        
        # Resource errors
        if any(keyword in error_str for keyword in ['quota', 'limit', 'storage', 'memory', 'disk']):
            return ErrorCategory.RESOURCE
        
        # Validation errors
        if any(keyword in error_str for keyword in ['invalid', 'validation', 'format', 'type']):
            return ErrorCategory.VALIDATION
        
        # System errors
        if any(keyword in error_str for keyword in ['system', 'internal', 'server', 'database']):
            return ErrorCategory.SYSTEM
        
        return ErrorCategory.UNKNOWN
    
    def determine_severity(self, error: Exception, context: Dict[str, Any]) -> ErrorSeverity:
        """Determine error severity based on context and error type."""
        # Critical errors that affect system stability
        if isinstance(error, (MemoryError, SystemError, KeyboardInterrupt)):
            return ErrorSeverity.CRITICAL
        
        # High severity for authentication and permission issues
        if self.classify_error(error) in [ErrorCategory.AUTHENTICATION, ErrorCategory.PERMISSION]:
            return ErrorSeverity.HIGH
        
        # Medium severity for resource and network issues
        if self.classify_error(error) in [ErrorCategory.RESOURCE, ErrorCategory.NETWORK]:
            return ErrorSeverity.MEDIUM
        
        # Low severity for validation and unknown errors
        return ErrorSeverity.LOW
    
    def get_user_friendly_message(self, error_info: ErrorInfo) -> str:
        """Generate user-friendly error messages."""
        category = error_info.category
        severity = error_info.severity
        
        base_messages = {
            ErrorCategory.NETWORK: {
                ErrorSeverity.LOW: "⚠️ Network connection is slow. Please try again.",
                ErrorSeverity.MEDIUM: "🌐 Network issue detected. Please check your connection and try again.",
                ErrorSeverity.HIGH: "🚫 Network error occurred. Please try again in a few minutes.",
                ErrorSeverity.CRITICAL: "💥 Critical network failure. Please contact support."
            },
            ErrorCategory.AUTHENTICATION: {
                ErrorSeverity.LOW: "🔐 Please log in to continue.",
                ErrorSeverity.MEDIUM: "🔑 Authentication required. Please use /login to authenticate.",
                ErrorSeverity.HIGH: "🚫 Access denied. Please check your credentials.",
                ErrorSeverity.CRITICAL: "💥 Authentication system error. Please contact support."
            },
            ErrorCategory.PERMISSION: {
                ErrorSeverity.LOW: "⚠️ You don't have permission for this action.",
                ErrorSeverity.MEDIUM: "🚫 Access denied. Contact an administrator for assistance.",
                ErrorSeverity.HIGH: "🚫 Permission denied. This action requires special privileges.",
                ErrorSeverity.CRITICAL: "💥 Critical permission error. Please contact support."
            },
            ErrorCategory.RESOURCE: {
                ErrorSeverity.LOW: "⚠️ Resource limit reached. Please try again later.",
                ErrorSeverity.MEDIUM: "💾 Storage space is limited. Please free up some space.",
                ErrorSeverity.HIGH: "🚫 Resource exhausted. Please contact support.",
                ErrorSeverity.CRITICAL: "💥 Critical resource failure. System maintenance required."
            },
            ErrorCategory.VALIDATION: {
                ErrorSeverity.LOW: "⚠️ Please check your input and try again.",
                ErrorSeverity.MEDIUM: "📝 Invalid input format. Please follow the instructions.",
                ErrorSeverity.HIGH: "🚫 Input validation failed. Please review your data.",
                ErrorSeverity.CRITICAL: "💥 Critical validation error. Please contact support."
            },
            ErrorCategory.SYSTEM: {
                ErrorSeverity.LOW: "⚠️ System is busy. Please try again.",
                ErrorSeverity.MEDIUM: "🔧 System issue detected. Please try again later.",
                ErrorSeverity.HIGH: "🚫 System error occurred. Please contact support.",
                ErrorSeverity.CRITICAL: "💥 Critical system failure. Emergency maintenance required."
            },
            ErrorCategory.UNKNOWN: {
                ErrorSeverity.LOW: "⚠️ An unexpected error occurred. Please try again.",
                ErrorSeverity.MEDIUM: "❓ Unknown error detected. Please try again later.",
                ErrorSeverity.HIGH: "🚫 Error occurred. Please contact support.",
                ErrorSeverity.CRITICAL: "💥 Critical error. Please contact support immediately."
            }
        }
        
        message = base_messages[category][severity]
        
        # Add helpful suggestions based on category
        suggestions = {
            ErrorCategory.NETWORK: "\n\n💡 <b>Tips:</b>\n• Check your internet connection\n• Try again in a few minutes\n• Use /help for assistance",
            ErrorCategory.AUTHENTICATION: "\n\n💡 <b>Tips:</b>\n• Use /login to authenticate\n• Check your credentials\n• Contact support if issues persist",
            ErrorCategory.PERMISSION: "\n\n💡 <b>Tips:</b>\n• Contact an administrator\n• Check your user role\n• Use /help for available commands",
            ErrorCategory.RESOURCE: "\n\n💡 <b>Tips:</b>\n• Free up storage space\n• Check your quota limits\n• Contact support for quota increase",
            ErrorCategory.VALIDATION: "\n\n💡 <b>Tips:</b>\n• Follow the input format\n• Check file size limits\n• Use /help for guidance",
            ErrorCategory.SYSTEM: "\n\n💡 <b>Tips:</b>\n• Try again in a few minutes\n• Check system status\n• Contact support if persistent",
            ErrorCategory.UNKNOWN: "\n\n💡 <b>Tips:</b>\n• Try again later\n• Use /help for assistance\n• Contact support if needed"
        }
        
        return message + suggestions.get(category, "")
    
    def get_recovery_actions(self, error_info: ErrorInfo) -> list:
        """Get suggested recovery actions for the user."""
        category = error_info.category
        
        actions = {
            ErrorCategory.NETWORK: [
                ("🔄 Retry", "retry_action"),
                ("📡 Check Connection", "check_connection"),
                ("⏰ Try Later", "try_later")
            ],
            ErrorCategory.AUTHENTICATION: [
                ("🔐 Login", "login_action"),
                ("Refresh", "refresh_auth"),
                ("📞 Contact Support", "contact_support")
            ],
            ErrorCategory.PERMISSION: [
                ("👤 Check Role", "check_role"),
                ("📞 Contact Admin", "contact_admin"),
                ("📖 View Permissions", "view_permissions")
            ],
            ErrorCategory.RESOURCE: [
                ("🗑️ Clear Space", "clear_space"),
                ("📊 Check Quota", "check_quota"),
                ("📞 Request Increase", "request_increase")
            ],
            ErrorCategory.VALIDATION: [
                ("📝 Edit Input", "edit_input"),
                ("❓ Show Help", "show_help"),
                ("🔄 Try Again", "retry_action")
            ],
            ErrorCategory.SYSTEM: [
                ("🔄 Retry", "retry_action"),
                ("⏰ Wait", "wait_action"),
                ("📞 Contact Support", "contact_support")
            ],
            ErrorCategory.UNKNOWN: [
                ("🔄 Retry", "retry_action"),
                ("❓ Help", "show_help"),
                ("📞 Contact Support", "contact_support")
            ]
        }
        
        return actions.get(category, [("🔄 Retry", "retry_action")])
    
    async def handle_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                          error: Exception, handler_name: str = "unknown") -> None:
        """Main error handling method."""
        try:
            error_msg = str(error)
            if "Query is too old" in error_msg or "Message is not modified" in error_msg:
                logger.debug(f"[BOT] Ignored transient API error in {handler_name}: {error_msg}")
                return
                
            # Extract user information
            user_id = None
            if update and update.effective_user:
                user_id = update.effective_user.id
            
            # Create error context
            error_context = {
                'handler_name': handler_name,
                'update_type': type(update).__name__,
                'user_id': user_id,
                'timestamp': datetime.now().isoformat()
            }
            
            # Classify and analyze error
            category = self.classify_error(error)
            severity = self.determine_severity(error, error_context)
            
            error_info = ErrorInfo(
                error=error,
                severity=severity,
                category=category,
                context=error_context,
                user_id=user_id
            )
            
            # Log error
            await self._log_error(error_info)
            
            # Track error counts
            error_key = f"{category.value}_{severity.value}"
            self.error_counts[error_key] = self.error_counts.get(error_key, 0) + 1
            
            # Store in user history
            if user_id:
                if user_id not in self.user_error_history:
                    self.user_error_history[user_id] = []
                self.user_error_history[user_id].append(error_info)
                
                # Keep only last 10 errors per user
                if len(self.user_error_history[user_id]) > 10:
                    self.user_error_history[user_id] = self.user_error_history[user_id][-10:]
            
            # Generate user message
            message = self.get_user_friendly_message(error_info)
            
            # Send error message to user (no action buttons — tips are in the message text)
            try:
                if update.message:
                    await update.message.reply_text(message, parse_mode='HTML')
                elif update.callback_query:
                    await update.callback_query.edit_message_text(message, parse_mode='HTML')
                    await update.callback_query.answer("Error occurred")
            except Exception as send_error:
                logger.error(f"Failed to send error message to user {user_id}: {send_error}")
            

        except Exception as handler_error:
            logger.error(f"Error in error handler: {handler_error}")
    
    async def _log_error(self, error_info: ErrorInfo) -> None:
        """Log error with comprehensive details."""
        log_message = (
            f"Error in {error_info.context.get('handler_name', 'unknown')}: "
            f"{type(error_info.error).__name__}: {str(error_info.error)} "
            f"(Category: {error_info.category.value}, Severity: {error_info.severity.value})"
        )

        if error_info.severity == ErrorSeverity.CRITICAL:
            logger.critical(log_message, exc_info=True)
        elif error_info.severity == ErrorSeverity.HIGH:
            logger.error(log_message, exc_info=True)
        elif error_info.severity == ErrorSeverity.MEDIUM:
            logger.warning(log_message)
        else:
            logger.info(log_message)

        # Persist to bot database via injected callback (if provided)
        if self._on_log_to_db and error_info.user_id:
            try:
                await self._on_log_to_db(
                    telegram_id=str(error_info.user_id),
                    action_taken="error_occurred",
                    status="error",
                    event_details=f"{error_info.category.value}: {str(error_info.error)}",
                    notes=f"Severity: {error_info.severity.value}"
                )
            except Exception as db_error:
                logger.error(f"Failed to log error to database via callback: {db_error}")
    
    def get_error_statistics(self) -> Dict[str, Any]:
        """Get error statistics for monitoring."""
        return {
            'error_counts': self.error_counts.copy(),
            'total_errors': sum(self.error_counts.values()),
            'user_error_history': {str(uid): len(errors) for uid, errors in self.user_error_history.items()},
            'most_common_error': max(self.error_counts.items(), key=lambda x: x[1]) if self.error_counts else None
        }

# Global error handler instance
_error_handler: Optional[ErrorHandler] = None


def get_error_handler() -> ErrorHandler:
    """Get the global error handler instance."""
    global _error_handler
    if _error_handler is None:
        _error_handler = ErrorHandler()
    return _error_handler

def enhanced_error_handler(func: Callable) -> Callable:
    """Decorator for enhanced error handling."""
    from functools import wraps
    from telegram.ext import ApplicationHandlerStop
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            return await func(update, context, *args, **kwargs)
        except ApplicationHandlerStop:
            raise
        except Exception as error:
            await get_error_handler().handle_error(update, context, error, func.__name__)
    
    return wrapper

handle_errors = enhanced_error_handler 
