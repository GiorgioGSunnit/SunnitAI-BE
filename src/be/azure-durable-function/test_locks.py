"""
Test script for lock management functions in function_app.py.
Azure Blob Storage has been removed — locks now use local filesystem.
"""
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

logger.info("Importing function_app...")
try:
    import function_app
    logger.info("Successfully imported function_app")
except Exception as import_error:
    logger.error(f"Error importing function_app: {import_error}")
    exit(1)


def test_lock_cleanup():
    logger.info("==================== LOCK CLEANUP TEST ====================")

    try:
        is_locked = function_app.is_any_process_locked()
        logger.info(f"is_any_process_locked() returned: {is_locked}")
    except Exception as e:
        logger.error(f"Error checking locks: {e}")
        return

    try:
        function_app.cleanup_stale_locks()
        logger.info("cleanup_stale_locks() completed successfully")
    except Exception as e:
        logger.error(f"Error in cleanup_stale_locks(): {e}")
        return

    try:
        lock_blob = function_app.acquire_global_lock("test_operation", timeout_minutes=5)
        if lock_blob:
            logger.info(f"Successfully created test lock: {lock_blob}")
        else:
            logger.error("Failed to create test lock")
            return
    except Exception as e:
        logger.error(f"Error creating test lock: {e}")
        return

    try:
        is_locked_after = function_app.is_any_process_locked()
        logger.info(f"is_any_process_locked() after acquire: {is_locked_after}")
        if not is_locked_after:
            logger.error("Lock was created but is_any_process_locked() doesn't detect it")
            return
    except Exception as e:
        logger.error(f"Error verifying lock: {e}")
        return

    try:
        function_app.cleanup_stale_locks()
        logger.info("cleanup_stale_locks() ran successfully")
    except Exception as e:
        logger.error(f"Error cleaning up test lock: {e}")
        return

    try:
        is_locked_final = function_app.is_any_process_locked()
        logger.info(f"is_any_process_locked() after cleanup: {is_locked_final}")
        if is_locked_final:
            logger.error("Lock cleanup failed — lock still exists")
        else:
            logger.info("Lock was successfully cleaned up!")
    except Exception as e:
        logger.error(f"Error in final verification: {e}")
        return

    logger.info("Lock cleanup test completed successfully")


try:
    test_lock_cleanup()
except Exception as test_error:
    logger.error(f"Unexpected error during test: {test_error}")

logger.info("Test script completed")
