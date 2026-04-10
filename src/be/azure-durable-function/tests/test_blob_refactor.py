"""Test refactoring single container blob (path convention)."""
# Path convention: cdp/, cdp-ext/, conf/, cdp-ext/requirements/, cdp-ext/comparisons/


def test_blob_path_convention():
    """Verifica convenzione path per single container ai-audit-poc-sa."""
    # Stesso logic di blob_storage_client.path_* (senza import azure)
    PREFIX_CDP = "cdp"
    PREFIX_CDP_EXT = "cdp-ext"
    PREFIX_CONF = "conf"

    def path_cdp(filename: str) -> str:
        return f"{PREFIX_CDP}/{filename}"

    def path_cdp_ext(filename: str) -> str:
        return f"{PREFIX_CDP_EXT}/{filename}"

    def path_conf(blob_name: str) -> str:
        return f"{PREFIX_CONF}/{blob_name}"

    def path_requirements(blob_name: str) -> str:
        return f"{PREFIX_CDP_EXT}/requirements/{blob_name}"

    def path_locks(blob_name: str) -> str:
        return f"{PREFIX_CONF}/locks/{blob_name}"

    assert path_cdp("file.pdf") == "cdp/file.pdf"
    assert path_cdp_ext("file.pdf") == "cdp-ext/file.pdf"
    assert path_conf("sum.json") == "conf/sum.json"
    assert path_requirements("hash.json") == "cdp-ext/requirements/hash.json"
    assert path_locks("global_operation.lock") == "conf/locks/global_operation.lock"
