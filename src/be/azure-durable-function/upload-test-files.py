import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

LOCAL_STORAGE_ROOT = Path(os.getenv("LOCAL_STORAGE_PATH", "/opt/sunnitai-be/storage"))
CATEGORY = os.getenv("CONTAINER_NAME_EXT", "cdp-ext")


def upload_file(file_path: str, blob_name: str | None = None) -> bool:
    """Copy a file into local storage (replaces Azure Blob upload)."""
    if blob_name is None:
        blob_name = os.path.basename(file_path)

    dest_dir = LOCAL_STORAGE_ROOT / CATEGORY
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / blob_name

    print(f"Copying {file_path} → {dest} ...")
    try:
        shutil.copy2(file_path, dest)
        print(f"Successfully copied {blob_name} to {dest_dir}")
        return True
    except Exception as e:
        print(f"Error copying {file_path}: {e}")
        return False


def main():
    test_files = ["testdata/NEW_1to5.pdf", "testdata/OLD_1to5.pdf"]

    print(f"Will upload {len(test_files)} files to {LOCAL_STORAGE_ROOT / CATEGORY}")

    for file_path in test_files:
        if os.path.exists(file_path):
            upload_file(file_path)
        else:
            print(f"File not found: {file_path}")


if __name__ == "__main__":
    main()
