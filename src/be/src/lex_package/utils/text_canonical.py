import re
import unicodedata as ud
import hashlib

HARD_HYPHENS = (
    "\u2010",
    "\u2011",
    "\u2012",
    "\u2013",
    "\u2014",
)  # trattini tipografici più comuni


def canonical(text: str) -> str:
    # 0) Normalizza accenti e segni diacritici
    text = ud.normalize("NFC", text)

    # 1) Rimuove eventuali "soft-hyphen" invisibili (U+00AD)
    text = text.replace("\u00ad", "")

    # 2) Ricompone le parole spezzate da un trattino a fine riga
    #    - look-behind/look-ahead assicurano che ci siano lettere attorno
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)

    # 3) Su alcuni PDF il trattino di fine riga non è "-" ma un dash tipografico:
    for dash in HARD_HYPHENS:
        pattern = rf"(?<=\w){re.escape(dash)}\s*\n\s*(?=\w)"
        text = re.sub(pattern, "", text)

    # 4) Collassa qualsiasi sequenza di whitespace in un solo spazio
    text = re.sub(r"\s+", " ", text)

    # 5) Ritaglia spazi iniziali/finali
    return text.strip().lower()  # .lower() opzionale
