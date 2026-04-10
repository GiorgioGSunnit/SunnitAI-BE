import importlib.util
from pathlib import Path


def lex_package_is_installed() -> bool:
    """
    Ritorna True se 'lex_package' proviene da site-packages/dist-packages,
    False se il modulo è quello locale nel tuo progetto.
    """
    spec = importlib.util.find_spec("lex_package")
    if not spec or not spec.origin:
        return False  # modulo non trovato o path anomalo
    origin_path = Path(spec.origin).resolve()
    return any(part in {"site-packages", "dist-packages"} for part in origin_path.parts)
