from pathlib import Path

import pandas as pd
import regex

map_productos = {
    "CERCANIAS": "CERCANIAS",
    "AVE": "AV",
    "MD": "MD-LD",
    "CERCANIAS RAM": "CERCANIAS RAM",
    "REGIONAL EXPRES": "MD-LD",
    "ALVIA": "MD-LD",
    " ": "otros",
    "IRYO": "AV",
    "Mercancias": "otros (no viajeros)",
    "OUIGO": "AV",
    "AVANT": "AV",
    "T.L.E.": "otros (no viajeros)",
    "INTERURBANO": "MD-LD",
    "Material Vacio": "otros (no viajeros)",
    "INTERCITY": "MD-LD",
    "Servicio Interno": "otros (no viajeros)",
    "EUROMED": "MD-LD",
    "LANZADERA-MIXTA": "MD-LD",
    "REGIONAL RAM": "REGIONAL RAM",
    "Transporte excepcional": "otros (no viajeros)",
    "VIAJEROS": "otros",
    "TALGO": "MD-LD",
    "AVLO": "AV",
    "Maquina Aislada Mercancias": "otros (no viajeros)",
    "Material vacio RAM": "otros (no viajeros)",
    "MATERIAL VACIO": "otros (no viajeros)",
    "ALSA": "MD-LD",
    "Maquina Aislada": "otros (no viajeros)",
    "Viajeros Larga Distancia y AVE": "AV",
}
map_ctc_subdireccion = {
    "AV NAFA/MASE: Madrid-Sevilla": "AV",
    "AV MONMUR: Albacete-Monforte-Murcia": "AV",
    "AV ALBALI: Albacete-Alicante": "AV",
    "AV VALEBU: Valladolid-León-Burgos": "AV",
    "AV OLZA/ORSA: Olmedo-Santiago": "AV",
    "AV MAVA/CHATO: Madrid-Valladolid": "AV",
    "AV ANTGRA/COMA: Antequera": "AV",
    "AV MABARFI: Zaragoza": "AV",
    "Ourense Provisonal": "NOROESTE",
    "Orense": "NOROESTE",
    "El Berrón Multired": "NOROESTE",
    "León": "NOROESTE",
    "Miranda Multired": "NORTE",
    "Santander Multired": "NORTE",
    "Santander": "NORTE",
    "Bilbao-Abando": "NORTE",
    "Miranda": "NORTE",
    "Manzanares": "CENTRO",
    "Chamartín": "CENTRO",
    "Barcelona": "NORESTE",
    "Zaragoza RC": "NORESTE",
    "València-Font de Sant Lluis": "ESTE",
    "Valencia Centro-Norte": "ESTE",
    "Córdoba": "SUR",
    "Sevilla": "SUR",
    "Ronda": "SUR",
    "Málaga": "SUR",
    "Granada": "SUR",
}

# TODO: Mnemónicos


# Códigos
df_name_cod = pd.read_excel("data/nombre-codigo.xlsx")
map_cod2name = dict(df_name_cod[["código", "nombre"]].values)
map_use_name2name = dict(df_name_cod[["use_name", "nombre"]].values)
map_name2use_name = dict(df_name_cod[["nombre", "use_name"]].values)

# Nombres de zonas
# df_mies = pd.read_csv("data/MIEs.csv", sep=";").map(
#     lambda x: x.strip() if pd.notna(x) else x
# )
# df_mies.columns = [c.strip() for c in df_mies.columns]
dir_info_mie = Path("data/MIEs.csv")
with dir_info_mie.open("r", encoding="utf8") as f:
    data = [
        row.split(";")
        for row in regex.sub(r"(?<=;) +", "", f.read()).split("\n")
        if row and not row.startswith("#")
    ]
df_info_mie = pd.DataFrame(data[1:], columns=data[0]).replace([""], [None])
map_cata_mie = dict(df_info_mie[["Catálogo", "MIE"]].values)
map_mie_cata = dict(df_info_mie[["MIE", "Catálogo"]].values)
map_mie_cata["/"] = None
# map_delegacion_socketin = (
#     df_info_mie.loc[:, ["Delegación", "SOCKETIN"]]
#     .groupby("Delegación")
#     .agg(lambda x: list(set(x)))
#     .apply(
#         lambda x: sorted([el for el in x["SOCKETIN"] if pd.notna(el)]),
#         axis=1,
#     )
#     .to_dict()
# )
# map_delegacion_mie = (
#     df_info_mie.loc[:, ["Delegación", "MIE"]]
#     .groupby("Delegación")
#     .agg(lambda x: list(set(x)))
#     .apply(
#         lambda x: sorted([el for el in x["MIE"] if pd.notna(el)]),
#         axis=1,
#     )
#     .to_dict()
# )
# map_delegacion_catalogo = (
#     df_info_mie.loc[
#         :,
#         [
#             "Delegación",
#             "Catálogo",
#             # "CTC",
#         ],
#     ]
#     .groupby("Delegación")
#     .agg(lambda x: list(set(x)))
#     # .apply(
#     #     lambda x: sorted([el for el in x["Catálogo"] + x["CTC"] if pd.notna(el)]),
#     #     axis=1,
#     # )
#     .to_dict()
# )
