import sys
import signal
import atexit
from shared.core.Logger import get_logger

logger = get_logger(__name__)


def _can_import(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def check_requirements(required_packages: dict, bot_name: str, optional_checks=None):
    if optional_checks:
        optional_checks()
    missing = [pkg for pkg, mod in required_packages.items() if not _can_import(mod)]
    if missing:
        logger.error(f"Missing required packages for {bot_name}: {', '.join(missing)}")
        sys.exit(1)
    logger.info(f"{bot_name}: required packages installed")


def check_environment(required_vars: dict, bot_name: str):
    missing = [k for k, v in required_vars.items() if not v]
    if missing:
        logger.error(f"Missing env vars for {bot_name}: {', '.join(missing)}")
        sys.exit(1)
    logger.info(f"{bot_name}: environment variables set")


def register_cleanup(db_path_getter, bot_name: str):
    def _cleanup():
        try:
            from shared.managers.HealthManager import stop_health_monitoring
            stop_health_monitoring()
        except Exception:
            pass
        try:
            db_path = db_path_getter()
            if db_path:
                from shared.database.DatabaseConnectionManager import get_db_manager
                mgr = get_db_manager(str(db_path))
                import asyncio
                try:
                    asyncio.run(mgr.close_all_async_connections())
                except Exception:
                    pass
        except Exception:
            pass
        logger.info(f"[SYSTEM] {bot_name} cleanup complete")
    atexit.register(_cleanup)


def run_bot(bot_name: str, main_func, required_packages: dict, required_vars: dict, db_path_getter, optional_checks=None):
    logger.info(f"🚀 Starting CloudVerse {bot_name}...")
    
    try:
        import uvloop
        import asyncio
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logger.info(f"[{bot_name}] uvloop event loop policy installed successfully")
    except ImportError:
        pass
        
    def _sighandler(sig, frame):
        logger.warning(f"{bot_name} received signal {sig} — exiting")
        sys.exit(0)
        
    signal.signal(signal.SIGINT, _sighandler)
    signal.signal(signal.SIGTERM, _sighandler)
    
    register_cleanup(db_path_getter, bot_name)
    
    try:
        logger.info("Running pre-flight checks...")
        check_requirements(required_packages, bot_name, optional_checks)
        check_environment(required_vars, bot_name)
        logger.info(f"✅ {bot_name} pre-flight checks passed")
        
        main_func()
    except KeyboardInterrupt:
        logger.warning(f"{bot_name} stopped by user (Ctrl+C)")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"[SYSTEM] Fatal error in {bot_name}: {e}", exc_info=True)
        sys.exit(1)
