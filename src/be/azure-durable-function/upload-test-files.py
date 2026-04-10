import os
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Azure Storage connection string
CONNECTION_STRING = os.getenv("CONNECTION_STRING")
CONTAINER_NAME_EXT = os.getenv("CONTAINER_NAME_EXT", "cdp-ext")


def upload_file_to_blob(file_path, blob_name=None):
    """Upload a file to Azure Blob Storage"""
    # Use the file name if no blob name is provided
    if blob_name is None:
        blob_name = os.path.basename(file_path)

    print(f"Uploading {file_path} to {CONTAINER_NAME_EXT}/{blob_name}...")

    try:
        # Create a client
        blob_service_client = BlobServiceClient.from_connection_string(
            CONNECTION_STRING
        )
        container_client = blob_service_client.get_container_client(CONTAINER_NAME_EXT)

        # Ensure container exists
        if not container_exists(container_client):
            print(f"Creating container {CONTAINER_NAME_EXT}...")
            container_client.create_container()

        # Upload the file
        with open(file_path, "rb") as data:
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.upload_blob(data, overwrite=True)

        print(f"Successfully uploaded {blob_name} to {CONTAINER_NAME_EXT}")
        return True
    except Exception as e:
        print(f"Error uploading {file_path}: {str(e)}")
        return False


def container_exists(container_client):
    """Check if a container exists"""
    try:
        container_client.get_container_properties()
        return True
    except Exception:
        return False


def main():
    # Files to upload
    test_files = ["testdata/NEW_1to5.pdf", "testdata/OLD_1to5.pdf"]

    if not CONNECTION_STRING:
        print(
            "ERROR: No Azure Storage connection string found in environment variables."
        )
        return

    print(f"Will upload {len(test_files)} files to {CONTAINER_NAME_EXT} container.")

    # Upload each file
    for file_path in test_files:
        if os.path.exists(file_path):
            upload_file_to_blob(file_path)
        else:
            print(f"File not found: {file_path}")


if __name__ == "__main__":
    main()
