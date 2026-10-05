from datetime import datetime
import html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from shared.core.Logger import get_logger

logger = get_logger(__name__)

__all__ = ["AlertManager", "get_alert_manager"]

class AlertManager:
    def __init__(self):
        self.app = None
        self.bot_name = "CloudVerse Bot"
        self.startup_message_id = None
        
    def set_application(self, app):
        """Set the Telegram application instance"""
        self.app = app
        
    def set_bot_name(self, name: str):
        """Set the display name of the bot sending these alerts (e.g. 'Drive Bot')"""
        self.bot_name = name

    def _get_notification_ids(self):
        """Read group/topic IDs from bot_data config — avoids hardcoded module imports."""
        if not self.app:
            return None, None, None, None
        config = self.app.bot_data.get('config', {})
        group_id = config.get('GROUP_CHAT_ID')
        management_topic_id = config.get('MANAGEMENT_TOPIC_ID')
        alerts_topic_id = config.get('ALERTS_TOPIC_ID')
        bugs_topic_id = config.get('BUGS_TOPIC_ID')
        analytics_topic_id = config.get('ANALYTICS_TOPIC_ID', 2550)
        return group_id, management_topic_id, alerts_topic_id, bugs_topic_id, analytics_topic_id

    async def send_startup_notification(self):
        try:
            ids = self._get_notification_ids()
            group_chat_id, management_topic_id = ids[0], ids[1]
            if not group_chat_id or not management_topic_id:
                logger.warning("Group chat ID or management topic ID not configured for startup notification")
                return False
                
            # Attempt to delete the previous startup message
            try:
                from pathlib import Path
                db_path_str = self.app.bot_data.get('config', {}).get('BOT_DB_PATH')
                if db_path_str:
                    cache_dir = Path(db_path_str).parent.parent / "cache"
                    bot_name_safe = self.bot_name.replace(" ", "_").lower()
                    last_msg_file = cache_dir / f".last_startup_{bot_name_safe}.txt"
                    
                    if last_msg_file.exists():
                        content = last_msg_file.read_text().strip().split('|')
                        old_msg_id = int(content[0])
                        await self.app.bot.delete_message(chat_id=int(group_chat_id), message_id=old_msg_id)
                        logger.info(f"Deleted previous startup notification: {old_msg_id}")
            except Exception as e:
                logger.debug(f"Could not delete previous startup notification: {e}")
                
            startup_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            message_text = (
                f"✅ <b>SERVER ONLINE</b> | {html.escape(self.bot_name)}\n\n"
                f"<b>Time:</b> {startup_time}\n"
                "<b>Status:</b> Fully operational\n"
                "<b>Services:</b> Active\n"
                "<b>Database:</b> Connected\n\n"
                "🟢 <i>All systems operational</i>"
            )
            
            message = await self.app.bot.send_message(
                chat_id=int(group_chat_id),
                text=message_text,
                message_thread_id=int(management_topic_id),
                parse_mode="HTML"
            )
            
            self.startup_message_id = message.message_id
            
            try:
                if 'last_msg_file' in locals():
                    last_msg_file.write_text(f"{message.message_id}|{startup_time}")
            except Exception as e:
                logger.warning(f"Failed to save startup message ID: {e}")
                
            logger.info(f"Startup notification sent to maintenance topic. Message ID: {message.message_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending startup notification: {e}")
            return False


    async def send_shutdown_notification(self, reason: str = "Unknown", initiated_by: str = "System"):
        try:
            # Always send the notification, even if it's an intentional shutdown.
                
            ids = self._get_notification_ids()
            group_chat_id, management_topic_id = ids[0], ids[1]
            if not group_chat_id or not management_topic_id:
                logger.warning("Group chat ID or management topic ID not configured for shutdown notification")
                return False
                
            shutdown_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            is_manual = reason == "Server manually stopped or restarted"
            
            import html
            safe_bot_name = html.escape(self.bot_name)
            safe_reason = html.escape(reason)
            safe_initiated_by = html.escape(initiated_by)
            
            header_status = "STOPPED manually" if is_manual else "STOPPED unexpectedly"
            health_status = "🟡 <i>Expected offline</i>" if is_manual else "🔴 <i>Requires attention</i>"
            
            message_text = (
                f"🔴 <b>SERVER OFFLINE</b> | {safe_bot_name}\n\n"
                f"<b>Reason:</b> {safe_reason}\n"
                f"<b>Initiated by:</b> {safe_initiated_by}\n"
                f"<b>Time:</b> {shutdown_time}\n\n"
                f"{health_status}"
            )
            
            if initiated_by != "System":
                message_text += f"\n\n🛑 <i>Terminated by @{initiated_by}</i>"
            
            msg_edited = False
            try:
                from pathlib import Path
                db_path_str = self.app.bot_data.get('config', {}).get('BOT_DB_PATH')
                if db_path_str:
                    cache_dir = Path(db_path_str).parent.parent / "cache"
                    bot_name_safe = self.bot_name.replace(" ", "_").lower()
                    last_msg_file = cache_dir / f".last_startup_{bot_name_safe}.txt"
                    
                    if last_msg_file.exists():
                        content = last_msg_file.read_text().strip().split('|')
                        old_msg_id = int(content[0])
                        startup_time = content[1] if len(content) > 1 else "Unknown"
                        
                        edited_message_text = (
                            f"🔴 <b>SERVER OFFLINE</b> | {safe_bot_name}\n\n"
                            "<b>Lifecycle:</b>\n"
                            f"• Started: {startup_time}\n"
                            f"• Stopped: {shutdown_time}\n\n"
                            "<b>Shutdown Details:</b>\n"
                            f"• Reason: {safe_reason}\n"
                            f"• Initiated by: {safe_initiated_by}\n\n"
                            f"{health_status}"
                        )
                        
                        await self.app.bot.edit_message_text(
                            chat_id=int(group_chat_id),
                            message_id=old_msg_id,
                            text=edited_message_text,
                            parse_mode="HTML"
                        )
                        logger.info(f"Updated startup notification to shutdown state: {old_msg_id}")
                        msg_edited = True
            except Exception as e:
                logger.debug(f"Could not edit previous startup notification: {e}")
            
            if not msg_edited:
                await self.app.bot.send_message(
                    chat_id=int(group_chat_id),
                    text=message_text,
                    message_thread_id=int(management_topic_id),
                    parse_mode="HTML"
                )
                logger.info("Shutdown notification sent as new message to maintenance topic")
            
            return True
            
        except Exception as e:
            logger.error(f"Error sending shutdown notification: {e}")
            return False
    async def send_warning_notification(self, warning_message: str, warning_type: str = "System Warning", severity: str = "MEDIUM"):
        try:
            ids = self._get_notification_ids()
            group_chat_id, alerts_topic_id = ids[0], ids[2]
            if not group_chat_id or not alerts_topic_id:
                logger.warning("Group chat ID or alerts topic ID not configured for warning notification")
                return False
                
            warning_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            import html
            safe_warning = html.escape(warning_message)
            message_text = (
                f"⚠️ <b>{html.escape(warning_type)}</b> | {html.escape(self.bot_name)}\n\n"
                f"<b>Severity:</b> {html.escape(severity)}\n"
                f"<b>Time:</b> {warning_time}\n\n"
                "<b>Details:</b>\n"
                f"<code>{safe_warning}</code>\n\n"
                "<i>Please monitor the system to ensure stability.</i>"
            )
            
            await self.app.bot.send_message(
                chat_id=int(group_chat_id),
                text=message_text,
                message_thread_id=int(alerts_topic_id),
                parse_mode="HTML"
            )
            
            logger.info("Warning notification sent to alerts topic")
            return True
            
        except Exception as e:
            logger.error(f"Error sending warning notification: {e}")
            return False

    async def send_error_notification(self, error_message: str, error_type: str = "Runtime Error", severity: str = "HIGH"):
        try:
            ids = self._get_notification_ids()
            group_chat_id, bugs_topic_id = ids[0], ids[3]
            if not group_chat_id or not bugs_topic_id:
                logger.warning("Group chat ID or bugs topic ID not configured for error notification")
                return False
                
            error_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Truncate error message if too long
            if len(error_message) > 500:
                error_message = error_message[:500] + "..."
            
            # Determine severity emoji and action required
            severity_info = self._get_severity_info(severity, error_type)
            
            import html
            safe_error_type = html.escape(error_type)
            safe_error_message = html.escape(error_message)
            message_text = (
                f"{severity_info['emoji']} <b>{safe_error_type.upper()}</b> | {html.escape(self.bot_name)}\n\n"
                f"<b>Time:</b> {error_time}\n"
                f"<b>Action Required:</b> {html.escape(severity_info['action'])}\n\n"
                "<b>Error Details:</b>\n"
                f"<code>{safe_error_message}</code>"
            )
            
            await self.app.bot.send_message(
                chat_id=int(group_chat_id),
                text=message_text,
                message_thread_id=int(bugs_topic_id),
                parse_mode="HTML"
            )
            
            logger.info(f"Error notification sent to maintenance topic: {error_type} (Severity: {severity})")
            return True
            
        except Exception as e:
            logger.error(f"Error sending error notification: {e}")
            return False

    def _get_severity_info(self, severity: str, error_type: str):
        severity_map = {
            "CRITICAL": {
                "emoji": "🚨",
                "action": "IMMEDIATE ACTION REQUIRED"
            },
            "HIGH": {
                "emoji": "⚠️",
                "action": "Manual attention needed"
            },
            "MEDIUM": {
                "emoji": "🟡",
                "action": "Review when convenient"
            },
            "LOW": {
                "emoji": "ℹ️",
                "action": "Informational only"
            }
        }
        
        return severity_map.get(severity, severity_map["HIGH"])


    async def send_critical_system_alert(self, component: str, issue: str, details: str = ""):
        try:
            ids = self._get_notification_ids()
            group_chat_id, bugs_topic_id = ids[0], ids[3]
            if not group_chat_id or not bugs_topic_id:
                logger.warning("Group chat ID or bugs topic ID not configured")
                return False
                
            alert_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            import html
            safe_comp = html.escape(component)
            safe_issue = html.escape(issue)
            safe_details = html.escape(details)
            message_text = (
                f"🚨 <b>CRITICAL SYSTEM FAILURE</b> | {html.escape(self.bot_name)}\n\n"
                f"<b>Component:</b> {safe_comp.upper()}\n"
                f"<b>Time:</b> {alert_time}\n\n"
                f"<b>Issue:</b>\n{safe_issue}\n\n"
                "<b>Details:</b>\n"
                f"<code>{safe_details}</code>\n\n"
                "❗️ <i>IMMEDIATE ACTION REQUIRED</i>"
            )
            
            await self.app.bot.send_message(
                chat_id=int(group_chat_id),
                text=message_text,
                message_thread_id=int(bugs_topic_id),
                parse_mode="HTML"
            )
            
            logger.critical(f"Critical system alert sent: {component} - {issue}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending critical system alert: {e}")
            return False

    async def send_daily_analytics_notification(self, top_users: list, advanced_metrics: dict = None):
        try:
            ids = self._get_notification_ids()
            group_chat_id, analytics_topic_id = ids[0], ids[4]
            if not group_chat_id or not analytics_topic_id:
                logger.warning("Group chat ID or analytics topic ID not configured")
                return False

            report_time = datetime.now().strftime('%Y-%m-%d')
            
            message_text = (
                f"📊 <b>DAILY ANALYTICS REPORT</b> | {html.escape(self.bot_name)}\n"
                f"<b>Date:</b> {report_time} (IST)\n\n"
            )

            if advanced_metrics:
                u = advanced_metrics.get('users', {})
                t = advanced_metrics.get('transfers', {})
                f = advanced_metrics.get('failures', {})
                
                size_fmt = lambda b: f"{b/1024**3:.2f} GB" if (b or 0) > 1024**3 else f"{(b or 0)/1024**2:.2f} MB"
                
                message_text += (
                    f"👥 <b>USER GROWTH</b>\n"
                    f"• New Today: +{u.get('daily_new') or 0}\n"
                    f"• This Week: +{u.get('weekly_new') or 0}\n"
                    f"• This Month: +{u.get('monthly_new') or 0}\n"
                    f"• This Year: +{u.get('yearly_new') or 0}\n"
                    f"• Lifetime Users: {u.get('total_users') or 0}\n\n"
                    f"🚀 <b>TRANSFER METRICS</b>\n"
                    f"• Today: {size_fmt(t.get('total_size_today'))} ({t.get('total_count_today') or 0} files)\n"
                    f"• This Week: {size_fmt(t.get('total_size_week'))}\n"
                    f"• This Month: {size_fmt(t.get('total_size_month'))}\n"
                    f"• This Year: {size_fmt(t.get('total_size_year'))}\n"
                    f"• Lifetime: {size_fmt(t.get('total_size_lifetime'))}\n"
                    f"• Failures (24h): {f.get('failure_count') or 0}\n\n"
                )

            message_text += "🏆 <b>DAILY TOP USERS</b>\n"
            if not top_users:
                message_text += "<i>No transfers recorded today.</i>"
            else:
                for i, user in enumerate(top_users, 1):
                    tid = user['telegram_id']
                    uname = f"@{user['username']}" if user.get('username') else f"ID: {tid}"
                    role = user.get('role', 'unknown').capitalize()
                    size_bytes = user.get('transferred_today', 0)
                    
                    if size_bytes > 1024**3:
                        size_str = f"{size_bytes/1024**3:.2f} GB"
                    else:
                        size_str = f"{size_bytes/1024**2:.2f} MB"
                    
                    count = user.get('transfer_count_today', 0)
                    message_text += f"{i}. <b>{html.escape(uname)}</b> <i>({html.escape(role)})</i> — {size_str} ({count} files)\n"

            await self.app.bot.send_message(
                chat_id=int(group_chat_id),
                text=message_text,
                message_thread_id=int(analytics_topic_id),
                parse_mode="HTML"
            )
            
            logger.info("Daily analytics notification sent to analytics topic")
            return True
            
        except Exception as e:
            logger.error(f"Error sending daily analytics notification: {e}")
            return False

    async def send_monthly_analytics_notification(self, monthly_data: dict):
        try:
            ids = self._get_notification_ids()
            group_chat_id, analytics_topic_id = ids[0], ids[4]
            if not group_chat_id or not analytics_topic_id:
                logger.warning("Group chat ID or analytics topic ID not configured")
                return False

            # Since it's run at midnight on the 1st of the new month, 
            # we subtract 1 day to get the correct name of the month that just ended.
            from datetime import timedelta
            report_time = (datetime.now() - timedelta(days=1)).strftime('%B %Y') 
            
            message_text = (
                f"🌟 <b>ENTERPRISE MONTHLY REPORT</b> | {html.escape(self.bot_name)}\n"
                f"<b>Month:</b> {report_time}\n\n"
            )

            metrics = monthly_data.get('metrics', {})
            active = monthly_data.get('active_users', 0)
            failures = monthly_data.get('failures', 0)
            top_users = monthly_data.get('top_users', [])
            
            size_fmt = lambda b: f"{b/1024**3:.2f} GB" if (b or 0) > 1024**3 else f"{(b or 0)/1024**2:.2f} MB"
            
            message_text += (
                f"📈 <b>MONTHLY OVERVIEW</b>\n"
                f"• Active Transfer Users: {active}\n"
                f"• Total Data Transferred: {size_fmt(metrics.get('total_size'))}\n"
                f"• Total Files Processed: {metrics.get('total_count') or 0}\n"
                f"• Average Per User: {size_fmt(metrics.get('avg_size'))}\n"
                f"• Failures: {failures}\n\n"
            )

            message_text += "🏆 <b>TOP USERS OF THE MONTH</b>\n"
            if not top_users:
                message_text += "<i>No transfers recorded this month.</i>"
            else:
                for i, user in enumerate(top_users, 1):
                    tid = user['telegram_id']
                    uname = f"@{user['username']}" if user.get('username') else f"ID: {tid}"
                    role = user.get('role', 'unknown').capitalize()
                    size_bytes = user.get('transferred_this_month', 0)
                    count = user.get('transfer_count_this_month', 0)
                    message_text += f"{i}. <b>{html.escape(uname)}</b> <i>({html.escape(role)})</i> — {size_fmt(size_bytes)} ({count} files)\n"

            await self.app.bot.send_message(
                chat_id=int(group_chat_id),
                text=message_text,
                message_thread_id=int(analytics_topic_id),
                parse_mode="HTML"
            )
            
            logger.info("Monthly enterprise analytics notification sent to analytics topic")
            return True
            
        except Exception as e:
            logger.error(f"Error sending monthly analytics notification: {e}")
            return False

    async def send_yearly_analytics_notification(self, yearly_data: dict):
        try:
            ids = self._get_notification_ids()
            group_chat_id, analytics_topic_id = ids[0], ids[4]
            if not group_chat_id or not analytics_topic_id:
                logger.warning("Group chat ID or analytics topic ID not configured")
                return False

            # Subtract 1 day to get the year that just ended
            from datetime import timedelta
            report_time = (datetime.now() - timedelta(days=1)).strftime('%Y')
            
            message_text = (
                f"🎆 <b>YEARLY ENTERPRISE REPORT</b> | {html.escape(self.bot_name)}\n"
                f"<b>Year:</b> {report_time}\n\n"
            )

            metrics = yearly_data.get('metrics', {})
            active = yearly_data.get('active_users', 0)
            failures = yearly_data.get('failures', 0)
            top_users = yearly_data.get('top_users', [])
            
            size_fmt = lambda b: f"{b/1024**3:.2f} GB" if (b or 0) > 1024**3 else f"{(b or 0)/1024**2:.2f} MB"
            
            message_text += (
                f"📊 <b>YEAR IN REVIEW</b>\n"
                f"• Active Transfer Users: {active}\n"
                f"• Total Data Transferred: {size_fmt(metrics.get('total_size'))}\n"
                f"• Total Files Processed: {metrics.get('total_count') or 0}\n"
                f"• Average Per User: {size_fmt(metrics.get('avg_size'))}\n"
                f"• System Failures: {failures}\n\n"
            )

            message_text += "🏆 <b>TOP USERS OF THE YEAR</b>\n"
            if not top_users:
                message_text += "<i>No transfers recorded this year.</i>"
            else:
                for i, user in enumerate(top_users, 1):
                    tid = user['telegram_id']
                    uname = f"@{user['username']}" if user.get('username') else f"ID: {tid}"
                    role = user.get('role', 'unknown').capitalize()
                    size_bytes = user.get('transferred_this_year', 0)
                    count = user.get('transfer_count_this_year', 0)
                    message_text += f"{i}. <b>{html.escape(uname)}</b> <i>({html.escape(role)})</i> — {size_fmt(size_bytes)} ({count} files)\n"

            await self.app.bot.send_message(
                chat_id=int(group_chat_id),
                text=message_text,
                message_thread_id=int(analytics_topic_id),
                parse_mode="HTML"
            )
            
            logger.info("Yearly enterprise analytics notification sent to analytics topic")
            return True
            
        except Exception as e:
            logger.error(f"Error sending yearly analytics notification: {e}")
            return False


_alert_manager = None

def get_alert_manager():
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager
