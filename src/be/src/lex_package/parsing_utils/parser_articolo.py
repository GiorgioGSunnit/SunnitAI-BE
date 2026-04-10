import json
import re


def pulisci_e_converti(s):
    # Elimina tutto ciò che non è cifra (0-9)
    s = str(s)
    numeri = re.findall(r"\d+", s)
    if numeri:
        return int(numeri[0])
    else:
        return None


def nojunkchars(text):
    """Normalizza una stringa eliminando caratteri di disturbo.

    Il valore in ingresso potrebbe non essere sempre una stringa (ad esempio
    interi *hash* o identificativi numerici).  Per evitare l'eccezione
    ``TypeError: expected string or bytes-like object`` sollevata dalle
    funzioni ``re.sub`` quando ricevono un *int*, convertiamo sempre il
    parametro in stringa.
    """

    # Garantiamo che *text* sia una stringa; in caso di ``None`` usiamo stringa vuota.
    if text is None:
        text = ""
    elif not isinstance(text, str):
        text = str(text)

    # Rimuove pattern come "▼ABC123" o "▼x9z"
    """Normalizza una stringa eliminando caratteri di disturbo.

    Il valore in ingresso potrebbe non essere sempre una stringa (ad esempio
    interi *hash* o identificativi numerici).  Per evitare l'eccezione
    ``TypeError: expected string or bytes-like object`` sollevata dalle
    funzioni ``re.sub`` quando ricevono un *int*, convertiamo sempre il
    parametro in stringa.
    """

    # Garantiamo che *text* sia una stringa; in caso di ``None`` usiamo stringa vuota.
    if text is None:
        text = ""
    elif not isinstance(text, str):
        text = str(text)

    # Rimuove pattern come "▼ABC123" o "▼x9z"
    text = re.sub(r'▼\w+\d+', '', text)
    # Rimuove caratteri di formattazione e markup HTML/XML di base
    # Rimuove caratteri di formattazione e markup HTML/XML di base
    text = text.replace('\n', '').replace('\t', '')
    text = re.sub(r'<[^>]*>', '', text)
    # Rimuove punteggiatura, spazi, simboli speciali (escluse lettere e numeri)
    text = re.sub(r'[^A-Za-z0-9]', '', text)
    return text.lower()  # Rendiamo tutto minuscolo per normalizzare


def noforbiddenchars(text):
    illegal_chars = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
    
    if isinstance(text, str):
        return illegal_chars.sub("", text)
    return text

def successivo(IdenficativoPrecedente, identificativo):
    IdenficativoPrecedente = pulisci_e_converti(IdenficativoPrecedente)
    identificativo = pulisci_e_converti(identificativo)
    if IdenficativoPrecedente + 1 == identificativo:
        risultato = True
    else:
        risultato = False
    return risultato


def contiene_parole(stringa, lista_parole_Ammesse, lista_parole_daEvitare):
    # Inizializza i contatori
    conteggio_non_ammesse = 0
    conteggio_ammesse = 0

    # Converti il testo in minuscolo per un confronto case-insensitive
    stringa_lower = stringa.lower()

    # Conta le parole non ammesse
    for parola in lista_parole_daEvitare:
        conteggio_non_ammesse += stringa_lower.count(parola.lower())

    # Conta le parole ammesse
    for parola in lista_parole_Ammesse:
        conteggio_ammesse += stringa_lower.count(parola.lower())

    # Controlla la differenza e se positiva, si ha necessità di un ulteriore controllo, altrimenti "False"
    if (conteggio_non_ammesse - conteggio_ammesse) > 0:
        return True
    else:
        return False


def successivoConTipologia( ProgressivoPrecedente, EstensionePrecedente, identificativo, estensione, Tipologia ):
    estensioneArabo_map = {
        "1": 1,
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
        "6": 6,
        "7": 7,
        "8": 8,
        "9": 9,
        "10": 10,
        "11": 11,
        "12": 12,
        "13": 13,
        "14": 14,
        "15": 15,
        "16": 16,
        "17": 17,
        "18": 18,
        "19": 19,
        "20": 20,
        "21": 21,
        "22": 22,
        "23": 23,
        "24": 24,
        "25": 25,
        "26": 26,
        "27": 27,
        "28": 28,
        "29": 29,
        "30": 30,
        "31": 31,
        "32": 32,
        "33": 33,
        "34": 34,
        "35": 35,
        "36": 36,
        "37": 37,
        "38": 38,
        "39": 39,
        "40": 40,
        "41": 41,
        "42": 42,
        "43": 43,
        "44": 44,
        "45": 45,
        "46": 46,
        "47": 47,
        "48": 48,
        "49": 49,
        "50": 50,
        "51": 51,
        "52": 52,
        "53": 53,
        "54": 54,
        "55": 55,
        "56": 56,
        "57": 57,
        "58": 58,
        "59": 59,
        "60": 60,
        "61": 61,
        "62": 62,
        "63": 63,
        "64": 64,
        "65": 65,
        "66": 66,
        "67": 67,
        "68": 68,
        "69": 69,
        "70": 70,
        "71": 71,
        "72": 72,
        "73": 73,
        "74": 74,
        "75": 75,
        "76": 76,
        "77": 77,
        "78": 78,
        "79": 79,
        "80": 80,
        "81": 81,
        "82": 82,
        "83": 83,
        "84": 84,
        "85": 85,
        "86": 86,
        "87": 87,
        "88": 88,
        "89": 89,
        "90": 90,
        "91": 91,
        "92": 92,
        "93": 93,
        "94": 94,
        "95": 95,
        "96": 96,
        "97": 97,
        "98": 98,
        "99": 99,
        "100": 100,
    }
    estensioneLatino_map = {
        None: 1,
		"bis": 2,
		"ter": 3,
		"quater": 4,
		"quinquies": 5,
		"sexies": 6,
		"septies": 7,
		"octies": 8,
		"novies": 9,
		"decies": 10,
		"undecies": 11,
		"duodecies": 12,
		"terdecies": 13,
		"quattuordecies": 14,
		"quindecies": 15,
		"sexdecies": 16,
		"septendecies": 17,
		"octodecies": 18,
		"novendecies": 19,
		"vicies": 20,
		"unvicies": 21,
		"duovicies": 22,
		"tervicies": 23,
		"quattuorvicies": 24,
		"quinvicies": 25,
		"sexvicies": 26,
		"septenvicies": 27,
		"octovicies": 28,
		"novenvicies": 29,
		"tricies": 30
	}

    estensioneLettere_map = {
        "a": 1,
        "b": 2,
        "c": 3,
        "d": 4,
        "e": 5,
        "f": 6,
        "g": 7,
        "h": 8,
        "i": 9,
        "l": 10,
        "m": 11,
        "n": 12,
        "o": 13,
        "p": 14,
        "q": 15,
        "r": 16,
        "s": 17,
        "t": 18,
        "u": 19,
        "v": 20,
        "z": 21,
        "aa": 22,
        "bb": 23,
        "cc": 24,
        "dd": 25,
        "ee": 26,
        "ff": 27,
        "gg": 28,
        "hh": 29,
        "ii": 30,
        "ll": 31,
        "mm": 32,
        "nn": 33,
        "oo": 34,
        "pp": 35,
        "qq": 36,
        "rr": 37,
        "ss": 38,
        "tt": 39,
        "uu": 40,
        "vv": 41,
        "zz": 42,
        "aaa": 43,
        "bbb": 44,
        "ccc": 45,
        "ddd": 46,
        "eee": 47,
        "fff": 48,
        "ggg": 49,
        "hhh": 50,
        "iii": 51,
        "lll": 52,
        "mmm": 53,
        "nnn": 54,
        "ooo": 55,
        "ppp": 56,
        "qqq": 57,
        "rrr": 58,
        "sss": 59,
        "ttt": 60,
        "uuu": 61,
        "vvv": 62,
        "zzz": 63,
        "aaaa": 64,
        "bbbb": 65,
        "cccc": 66,
        "dddd": 67,
        "eeee": 68,
        "ffff": 69,
        "gggg": 70,
        "hhhh": 71,
        "iiii": 72,
        "llll": 73,
        "mmmm": 74,
        "nnnn": 75,
        "oooo": 76,
        "pppp": 77,
        "qqqq": 78,
        "rrrr": 79,
        "ssss": 80,
        "tttt": 81,
        "uuuu": 82,
        "vvvv": 83,
        "zzzz": 84,
        "aaaaa": 85,
        "bbbbb": 86,
        "ccccc": 87,
        "ddddd": 88,
        "eeeee": 89,
        "fffff": 90,
        "ggggg": 91,
        "hhhhh": 92,
        "iiiii": 93,
        "lllll": 94,
        "mmmmm": 95,
        "nnnnn": 96,
        "ooooo": 97,
        "ppppp": 98,
        "qqqqq": 99,
        "rrrrr": 100,
    }
    estensioneRomanino_map = {
        "i": 1,
        "ii": 2,
        "iii": 3,
        "iv": 4,
        "v": 5,
        "vi": 6,
        "vii": 7,
        "viii": 8,
        "ix": 9,
        "x": 10,
        "xi": 11,
        "xii": 12,
        "xiii": 13,
        "xiv": 14,
        "xv": 15,
        "xvi": 16,
        "xvii": 17,
        "xviii": 18,
        "xix": 19,
        "xx": 20,
        "xxi": 21,
        "xxii": 22,
        "xxiii": 23,
        "xxiv": 24,
        "xxv": 25,
        "xxvi": 26,
        "xxvii": 27,
        "xxviii": 28,
        "xxix": 29,
        "xxx": 30,
        "xxxi": 31,
        "xxxii": 32,
        "xxxiii": 33,
        "xxxiv": 34,
        "xxxv": 35,
        "xxxvi": 36,
        "xxxvii": 37,
        "xxxviii": 38,
        "xxxix": 39,
        "xl": 40,
        "xli": 41,
        "xlii": 42,
        "xliii": 43,
        "xliv": 44,
        "xlv": 45,
        "xlvi": 46,
        "xlvii": 47,
        "xlviii": 48,
        "xlix": 49,
        "l": 50,
        "li": 51,
        "lii": 52,
        "liii": 53,
        "liv": 54,
        "lv": 55,
        "lvi": 56,
        "lvii": 57,
        "lviii": 58,
        "lix": 59,
        "lx": 60,
        "lxi": 61,
        "lxii": 62,
        "lxiii": 63,
        "lxiv": 64,
        "lxv": 65,
        "lxvi": 66,
        "lxvii": 67,
        "lxviii": 68,
        "lxix": 69,
        "lxx": 70,
        "lxxi": 71,
        "lxxii": 72,
        "lxxiii": 73,
        "lxxiv": 74,
        "lxxv": 75,
        "lxxvi": 76,
        "lxxvii": 77,
        "lxxviii": 78,
        "lxxix": 79,
        "lxxx": 80,
        "lxxxi": 81,
        "lxxxii": 82,
        "lxxxiii": 83,
        "lxxxiv": 84,
        "lxxxv": 85,
        "lxxxvi": 86,
        "lxxxvii": 87,
        "lxxxviii": 88,
        "lxxxix": 89,
        "xc": 90,
        "xci": 91,
        "xcii": 92,
        "xciii": 93,
        "xciv": 94,
        "xcv": 95,
        "xcvi": 96,
        "xcvii": 97,
        "xcviii": 98,
        "xcix": 99,
        "c": 100,
    }

    #    print ("#######", ProgressivoPrecedente, EstensionePrecedente, identificativo, estensione, Tipologia)

    if Tipologia == "Numerico":
        # Mappatura basata sulla progressione alfabetica
        # numero = int(identificativo)
        numero = estensioneArabo_map.get(identificativo.lower())
    elif Tipologia == "Romanino":
        # Mappatura basata sulla numerazione romana
        numero = estensioneRomanino_map.get(identificativo.lower())
    elif Tipologia == "Lettere":
        # Mappatura basata sulla progressione alfabetica con due lettere
        numero = estensioneLettere_map.get(identificativo.lower())
    elif Tipologia == "":
        if (len(identificativo) == 1) and (
            estensioneArabo_map.get(identificativo.lower()) == 1
        ):
            Tipologia = "Numerico"
            numero = 1
        elif (len(identificativo) == 1) and (
            estensioneRomanino_map.get(identificativo.lower()) == 1
        ):
            Tipologia = "Romanino"
            numero = 1
        elif (len(identificativo) == 1) and (
            estensioneLettere_map.get(identificativo.lower()) == 1
        ):
            Tipologia = "Lettere"
            numero = 1
        else:
            risultato = False
            numero = 1
    else:
        risultato = False

    # Vocaboli latini per addendum
    if estensione is not None:
        cardinalita = estensioneLatino_map.get(identificativo.lower())
    else:
        cardinalita = 1

    if int(ProgressivoPrecedente) + 1 == numero:
        if cardinalita == 1:
            risultato = True  # Case Articolo 7 bis --> Articolo 8
        else:
            risultato = False  # Case Articolo 7 bis --> Articolo 8 bis
    else:
        if (ProgressivoPrecedente == numero) and (int(EstensionePrecedente) + 1 == cardinalita):
            risultato = True  # Case Articolo 7 bis --> Articolo 7 ter
        elif (ProgressivoPrecedente == numero) and (int(EstensionePrecedente) + 2 == cardinalita):
            risultato = True  # Case Articolo 7 bis --> Articolo 7 quater
        elif int(ProgressivoPrecedente) + 2 == numero:
            risultato = True  # Case Articolo 7 bis --> Articolo 9
        else:
            risultato = False  # Case Articolo 7 bis --> Articolo 7 quater
    #    print( "\n --> ", risultato, " - ", identificativo, " - ", cardinalita, " - ", Tipologia, " <-- " )
    return risultato, numero, cardinalita, Tipologia


def parser_articolo(s) -> list[dict]:
    print("\n 4️⃣ Parsing article...", s[:50], " ...")
    # Debugging line to show the start of the string
    # Find all matches of pattern "\nn." where \n is literal and n. is a number followed by dot and space
    # Look for both literal "\n" strings and actual newline characters
    # - literal_pattern = r"\\n\d+\. "

    lista_parole_DaEvitare = [
        "decreto",
        "legge",
        "regolamento",
        "raccomandazione",
        "direttiva (UE)",
    ]
    lista_parole_Ammesse = [
        "presente decreto",
        "presente legge",
        "presente regolamento",
    ]

    pattern = re.compile(
        r"(?:^|\n)\s*"  # inizio riga o newline + eventuali spazi
        r"(?P<identificativo>\d+|[a-z]{1,4})"  # numerico o lettere (1-4 lettere)
        r"(?:\s*(?P<estensione>bis|ter|quater|quinquies|sexies|septies|octies|novies|decies))?"  # estensione latina opzionale
        r"(?P<simbolo>\.|\))\s+",  # punto o parentesi chiusa + spazio
        re.IGNORECASE | re.MULTILINE,
    )

    IdentificativoPrecedente = 0
    ProgressivoPrecedente = 0
    CardinalitaPrecedente = 0
    cardinalita = 0
    estensionePrecedente = 0
    numeroProgressivo = 0
    estensione = 0
    simboloPrecedente = ""
    simbolo = ""
    TipologiaPrecedente = ""
    Tipologia = ""
    Livello = 0
    Esito = ""
    InizioVirgolettato = False
    # - matches_literal = list(re.finditer(literal_pattern, s))
    matches_actual = list(re.finditer(pattern, s))

    # Use whichever pattern found more matches

    matches = matches_actual
    pattern_used = pattern

    # print("  -> Numero Matches: ", len(matches))

    if not matches:
        # If no matches found, return single element with default identifier
        IdentificativoPrecedente = 0
        contenuto = s.strip()
        contenuto_senza_struttura = pattern.sub("", contenuto, count=1)
        risultato = contiene_parole(
            contenuto_senza_struttura, lista_parole_Ammesse, lista_parole_DaEvitare
        )

        # print ("   Articolo: Not Matches: ", IdentificativoPrecedente, " Dimensione:", len(contenuto_senza_struttura), "   -> Match non riconosciuto")

        return [
            {
                "identificativo": IdentificativoPrecedente,
                "contenuto": contenuto_senza_struttura,
                "flag": risultato,
                "hash": hash(nojunkchars(contenuto_senza_struttura))
            }
        ]

    result = []

    start_idx = 0  # matches[0].start()

    # Process each section
    for i in range(len(matches)):
        identificativo = matches[i].group("identificativo")
        estensione = matches[i].group("estensione")
        simbolo = matches[i].group("simbolo")
        if simboloPrecedente == "":
            simboloPrecedente = simbolo
            Livello = 1

        Esito = ""
        end_idx = matches[i].start()
        risultato, numeroProgressivo, cardinalita, Tipologia = successivoConTipologia(
            ProgressivoPrecedente,
            estensionePrecedente,
            identificativo,
            estensione,
            Tipologia,
        )
        # print(
        #     "",
        #     i,
        #     " (",
        #     start_idx,
        #     ".",
        #     end_idx,
        #     ") -> id:",
        #     IdentificativoPrecedente,
        #     "->",
        #     identificativo,
        #     " (",
        #     risultato,
        #     "-",
        #     Tipologia,
        #     ") - Progressivo:",
        #     ProgressivoPrecedente,
        #     "->",
        #     numeroProgressivo,
        #     " - cardinalita:",
        #     cardinalita,
        #     " - simbolo:",
        #     simboloPrecedente,
        #     "->",
        #     simbolo,
        #     "  [",
        #     Esito,
        #     "]",
        # )

        if start_idx == end_idx:
            # print(" NON scrivo e vado avanti")
            IdentificativoPrecedente = identificativo
            ProgressivoPrecedente = numeroProgressivo
        else:
            if risultato:
                if simboloPrecedente == simbolo:
                    contenuto = s[start_idx:end_idx].strip().replace("\n|\\n", " ")

                    # Remove the identifier from the content
                    contenuto_senza_struttura = pattern.sub("", contenuto, count=1)
                    risultato = contiene_parole(
                        contenuto_senza_struttura,
                        lista_parole_Ammesse,
                        lista_parole_DaEvitare,
                    )
                    if ((InizioVirgolettato == False) and ("«" in contenuto_senza_struttura)):
                        # print(" Inizia il Virgolettato in ", int(ProgressivoPrecedente))
                        InizioVirgolettato = True
                    if ((InizioVirgolettato == True) and ("»" in contenuto_senza_struttura)):
                        # print(" Termina il Virgolettato in ", int(ProgressivoPrecedente))
                        InizioVirgolettato = False
                    if (InizioVirgolettato == False):
                        if (int(ProgressivoPrecedente) + 1 != numeroProgressivo):
                            result.append(
                                {
                                    "identificativo": int(ProgressivoPrecedente) + 1,
                                    "contenuto": "",
                                    "flag": "",
                                    "hash": "0"
                                }
                            )

                        # print ("   -----------> Virgolettato - Articolo: ", IdentificativoPrecedente, " Dimensione:", len(contenuto_senza_struttura), "   -> Nel Virgolettato")

                        result.append(
                            {
                                "identificativo": IdentificativoPrecedente,
                                "contenuto": contenuto_senza_struttura,
                                "flag": risultato,
                                "hash": hash(nojunkchars(contenuto_senza_struttura))
                            }
                        )
                        IdentificativoPrecedente = identificativo
                        ProgressivoPrecedente = numeroProgressivo
                        # print(" Estraggo Testo e Scrivo --> ", IdentificativoPrecedente)
                        start_idx = end_idx
                        continue
            #         else:
            #             print(" NON scrivo e Integro (virgolettato)")
            #     else:
            #         print(" NON scrivo e Integro (simbolo)")
            # else:
            #     print(" NON scrivo e Integro (risultato)")
    if matches:
        contenuto_successivo = s[start_idx : len(s)].strip().replace("\n|\\n", " ")
        contenuto_senza_struttura = pattern.sub("", contenuto_successivo, count=1)
        risultato_flag = contiene_parole(
            contenuto_senza_struttura, lista_parole_Ammesse, lista_parole_DaEvitare
        )
        # print ("   -----------> Articolo: ", IdentificativoPrecedente, " Dimensione:", len(contenuto_senza_struttura), "   -> Nel Match")
        result.append(
            {
                "identificativo": IdentificativoPrecedente,
                "contenuto": contenuto_senza_struttura,
                "flag": risultato_flag,
                "hash": hash(nojunkchars(contenuto_senza_struttura))
            }
        )

        # print(
        #     "FINALE (",
        #     start_idx,
        #     ".FINALE) -> id:",
        #     IdentificativoPrecedente,
        #     "->",
        #     identificativo,
        #     " (",
        #     risultato,
        #     "-",
        #     Tipologia,
        #     ") - Progressivo:",
        #     ProgressivoPrecedente,
        #     "->",
        #     numeroProgressivo,
        #     " - cardinalita:",
        #     cardinalita,
        #     " - simbolo:",
        #     simboloPrecedente,
        #     "->",
        #     simbolo,
        #     "  [",
        #     Esito,
        #     "]",
        # )
        # print(" Estraggo Testo e Scrivo --> ", IdentificativoPrecedente)

    return result

if __name__ == "__main__":
    # Test with file containing literal \n
    with open("../test.txt", "r", encoding="utf-8") as f:
        _s = f.read()
        s = parser_articolo(_s)
        result = json.dumps(s, ensure_ascii=False, indent=2)
        print(result[:200])

    # Test with the same content but ensure actual newlines
    print("\nTesting with actual newlines:")
    with open("../test.txt", "r", encoding="utf-8") as f:
        _s = f.read().replace("\\n", "\n")
        print(f"First 50 chars after newline conversion: {repr(_s[:50])}")
        s = parser_articolo(_s)
        result = json.dumps(s, ensure_ascii=False, indent=2)
        print(result[:200])
