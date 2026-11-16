# Acciones chilenas a monitorear.
# Yahoo Finance identifica las acciones de la Bolsa de Santiago con el sufijo ".SN"
# Si algún ticker no existe o Yahoo le cambió el nombre, el bot lo reporta en los logs
# y simplemente lo salta (no rompe el resto del monitoreo).

TICKERS = [
    "CHILE", "BCI", "BSANTANDER", "COPEC", "CMPC", "ENELAM", "ENELCHILE",
    "COLBUN", "CCU", "CENCOSUD", "FALABELLA", "ITAUCL", "AGUAS-A", "ANDINA-B",
    "ENTEL", "PARAUCO", "MALLPLAZA", "VAPORES", "SQM-B", "CAP", "ILC", "ZOFRI",
    "SMU", "QUINENCO", "CFINRENTAS", "HABITAT", "CFMITNIPSA", "LTM", "IAM",
    "CONCHATORO", "BICE", "CENCOMALLS", "ENELGXCH", "LIPIGAS", "RIPLEY",
    "GASCO", "SALFACORP", "SONDA", "CFIETFCD", "CFIETFIPSA", "ECL", "PAMPA",
    "EMBONOR-B", "ENAEX", "SCHWAGER", "BESALCO",
]

def to_yahoo_symbol(ticker: str) -> str:
    """Convierte el nombre de la acción al formato que usa Yahoo Finance."""
    return f"{ticker}.SN"
