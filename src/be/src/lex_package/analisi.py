import logging
from copy import deepcopy
from lex_package.parse import parse
from lex_package.analisi_parallel import analisi_parallel

logger = logging.getLogger("lex_package.analisi")

# Execute the async function with asyncio.run()
async def analisi(pdf_path, pdf_name):
    logger.info(f"Starting analisi for {pdf_name} at {pdf_path}")
    articoli: list[dict] = parse(pdf_path, pdf_name)
    logger.info(f"Parse returned {len(articoli)} articoli")
    output = await analisi_parallel(articoli, pdf_name)
    logger.info(f"[OUTPUT ANALISI]: {output}")
    return output

async def consolida_analisi(testocontenuto):
    res = []
    TitoloProvvisorio = "" 
    IdentificativoArticolo = ""
    titolo_appoggio = ""
    UnaLegge = False
    print("####### consolida_analisi #######")
    for j,y in enumerate(testocontenuto):
        if not(UnaLegge) and ("articolo" in str(y.get("identificativo", "")).lower()):
            # print("####### E' una LEGGE! #######")
            UnaLegge = True

    for i,x in enumerate(testocontenuto):
        if UnaLegge and (str(x.get("identificativo", "")).isdigit()):
            # print("#########  SALTO!  #########")
            continue
        else:
            TitoloProvvisorio = x.get("titolo", "")
            IdentificativoArticolo =  str(x.get("identificativo", "")).lower().replace(" ", "").replace("art.", "Articolo").strip()

            if (TitoloProvvisorio == ""):
                titolo_appoggio = x["contenuto_parsato"][0].get("contenuto", "")
                titolo_appoggio = titolo_appoggio.replace("\n", "").strip()
                if (titolo_appoggio.startswith("(") and titolo_appoggio.endswith(")")):
                    TitoloProvvisorio = titolo_appoggio

            x["titolo"] = TitoloProvvisorio
            x["identificativo"] = IdentificativoArticolo

            res.append(deepcopy(x))

    return res
