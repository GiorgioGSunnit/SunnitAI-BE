import os
import logging
import time

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Set the connection string (use env or local Azurite / dev storage)
os.environ["CONNECTION_STRING"] = os.getenv(
    "AZURE_STORAGE_CONNECTION_STRING",
    "UseDevelopmentStorage=true",
)

# Import function_app after setting the connection string
logger.info("Importing function_app with production CONNECTION_STRING")

try:
    import function_app

    logger.info("Successfully imported function_app")
except Exception as import_error:
    logger.error(f"Error importing function_app: {str(import_error)}")
    exit(1)

# Check basic connectivity to blob storage
try:
    from azure.storage.blob import BlobServiceClient

    logger.info("Testing connection to Azure Blob Storage...")
    blob_service_client = BlobServiceClient.from_connection_string(
        os.environ["CONNECTION_STRING"], connection_timeout=10, max_retries=1
    )

    # List containers to validate connection
    containers = list(blob_service_client.list_containers())
    logger.info(
        f"Successfully connected to Azure Blob Storage. Found {len(containers)} containers."
    )

    for container in containers:
        logger.info(f"  - {container.name}")

except Exception as conn_error:
    logger.error(f"Failed to connect to Azure Blob Storage: {str(conn_error)}")
    logger.info(
        "The Azure Blob Storage connection is failing. Please check your connection string or network connectivity."
    )
    exit(1)


# Main test function
def test_lock_cleanup():
    logger.info("==================== LOCK CLEANUP TEST ====================")

    # Check if any locks exist
    logger.info("Checking if any processes are locked")
    try:
        is_locked = function_app.is_any_process_locked()
        logger.info(f"is_any_process_locked() returned: {is_locked}")
    except Exception as lock_check_error:
        logger.error(f"Error checking if processes are locked: {str(lock_check_error)}")
        return

    # Run the cleanup stale locks function
    logger.info("Running cleanup_stale_locks()")
    try:
        function_app.cleanup_stale_locks()
        logger.info("cleanup_stale_locks() completed successfully")
    except Exception as cleanup_error:
        logger.error(f"Error running cleanup_stale_locks(): {str(cleanup_error)}")
        return

    # Try to create a test lock
    logger.info("Creating a test lock...")
    try:
        lock_blob = function_app.acquire_global_lock(
            "test_operation", timeout_minutes=5
        )
        if lock_blob:
            logger.info(f"Successfully created test lock: {lock_blob}")
        else:
            logger.error("Failed to create test lock")
            return
    except Exception as create_lock_error:
        logger.error(f"Error creating test lock: {str(create_lock_error)}")
        return

    # Verify the lock exists
    logger.info("Verifying the lock exists...")
    try:
        is_locked_after = function_app.is_any_process_locked()
        logger.info(f"is_any_process_locked() returned: {is_locked_after}")

        if not is_locked_after:
            logger.error(
                "Lock was created but is_any_process_locked() doesn't detect it"
            )
            return
    except Exception as verify_error:
        logger.error(f"Error verifying lock exists: {str(verify_error)}")
        return

    # Clean up the test lock
    logger.info("Running cleanup_stale_locks() to remove the test lock")
    try:
        function_app.cleanup_stale_locks()
        logger.info("cleanup_stale_locks() ran successfully")
    except Exception as cleanup_error2:
        logger.error(f"Error cleaning up test lock: {str(cleanup_error2)}")
        return

    # Verify the lock was removed
    logger.info("Verifying the lock was removed...")
    try:
        is_locked_final = function_app.is_any_process_locked()
        logger.info(f"is_any_process_locked() returned: {is_locked_final}")

        if is_locked_final:
            logger.error("Lock cleanup failed - lock still exists")
        else:
            logger.info("Lock was successfully cleaned up!")
    except Exception as final_verify_error:
        logger.error(f"Error verifying lock was removed: {str(final_verify_error)}")
        return

    logger.info("Lock cleanup test completed successfully")


# Run the test
try:
    test_lock_cleanup()
except Exception as test_error:
    logger.error(f"Unexpected error during test: {str(test_error)}")

logger.info("Test script completed")
