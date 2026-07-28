import json
from collections import Counter
from pathlib import Path
from typing import Union
from typing import List


import numpy as np
import pandas as pd
import regex


TOPOS = Path(r"C:\topogen-adif-repo\baseline")




def listTopos(ftype: str = "properties", estaciones: List[str] = []):
    """
    Dataframe con los códigos y ficheros `properties` o `pl`.\\
    `estaciones` es una lista de códigos de estación. Si está vacía, incluye todas.
    """
    list_topos_all = pd.DataFrame(TOPOS.rglob(f"*.{ftype}"), columns=[ftype])
    list_topos_all["Código"] = list_topos_all[ftype].apply(
        lambda x: regex.search(r"\b[\w\d]\d{4}\b", str(x))
    )
    list_topos_all["Código"] = list_topos_all["Código"].apply(
        lambda x: x.group() if x else None
    )
    list_topos_all = list_topos_all.groupby(by=["Código"]).agg(set).reset_index()
    if estaciones:
        list_topos_all = list_topos_all[list_topos_all["Código"].isin(estaciones)]
    return list_topos_all


def loadElementosTopos(estaciones: List[str] = []):
    """
    Carga todos los elementos que se encuentran en las topos MSE.\\
    `estaciones` es una lista de códigos de estación. Si está vacía, incluye todas.
    """
    list_topos_all = listTopos("properties", estaciones)
    elementos_topos = []
    for codigo, topos in list_topos_all.values:
        for topo in topos:
            with topo.open("r", encoding="utf8") as f:
                lines = [l.strip() for l in f.readlines()]
                # Mnemónico comercial de esta topo
                mnem_com = regex.search(r"(?<==).+?(?=,.+\.pl)", lines[-1]).group()
                for line in lines:
                    if ":" not in line:
                        continue
                    # Mnemónico real al que pertenece cada elemento
                    _, mnem, *els = regex.split(r"[=:,]", line)
                    elementos_topos.extend(
                        [[codigo, mnem_com, mnem] + el.split(";") for el in els]
                    )
    elementos_topos = (
        pd.DataFrame(
            elementos_topos,
            columns=["Código", "Mnemónico_comercial", "Mnemónico", "Elemento", "Tipo"],
        )
        .drop_duplicates()
        .sort_values(by=["Código", "Mnemónico", "Elemento", "Tipo"])
        .reset_index(drop=True)
    )
    elementos_topos["Elemento"] = elementos_topos["Elemento"].apply(
        lambda x: regex.sub(r"^\w{2}_", "", x)
    )
    return elementos_topos


# def loadViasFromTopos(estaciones: list[str], red: str = "all"):
#     """
#     red: {"all", "av", "rc"}
#     """
#     list_topos_all = pd.Series(TOPOS.rglob("*.pl"))
#     list_topos_all = list_topos_all[
#         list_topos_all.apply(lambda x: x.parent.name.split("-")[0] in estaciones)
#     ]
#     map_vias = {codigo: {"AV": {}, "RC": {}} for codigo in estaciones}

#     def getVias(codigo, list_topos):
#         map_vias = {}
#         for topo in list_topos[list_topos.apply(lambda x: codigo in str(x))].tolist():
#             if codigo not in str(topo):
#                 continue
#             with topo.open("r", encoding="utf8") as f:
#                 estacionamientos = [
#                     regex.findall(r"(?<=').+?(?=')", l)
#                     for l in f.readlines()
#                     if "cv_estacionamiento" in l
#                 ]
#                 return dict(
#                     map_vias.get(codigo, [])
#                     + [(el[0].replace("_", ""), el[-1]) for el in estacionamientos]
#                 )
#         return dict()

#     red = red.lower()
#     if red in ["all", "av"]:
#         list_topos_av = list_topos_all[
#             list_topos_all.apply(lambda x: bool(regex.search(r"(AV|ALBACETE)", str(x))))
#         ]
#         for codigo in estaciones:
#             map_vias[codigo]["AV"].update(getVias(codigo, list_topos_av))
#     if red in ["all", "rc"]:
#         list_topos_rc = list_topos_all[
#             list_topos_all.apply(
#                 lambda x: not bool(regex.search(r"(AV|ALBACETE)", str(x)))
#             )
#         ]
#         for codigo in estaciones:
#             map_vias[codigo]["RC"].update(getVias(codigo, list_topos_rc))

#     return map_vias


def getEstacionamientos(estaciones: List[str] = []):
    """
    Carga los circuitos de estacionamiento de las topos.\\
    `estaciones` es una lista de códigos de estación. Si está vacía, incluye todas.
    """
    list_topos_all = listTopos("pl", estaciones)
    estacionamientos_ctc = []
    for codigo, topos in list_topos_all.values:
        for topo in topos:
            with topo.open("r", encoding="utf8") as f:
                lines = [l.strip() for l in f.readlines() if "cv_estacionamiento" in l]
            estacionamientos = [
                [
                    el.strip("'")
                    for el in regex.split(
                        r"[,\s]+", regex.search(r"(?<=\().+?(?=\)\.)", l).group()
                    )
                ]
                for l in lines
            ]
            estacionamientos_ctc.extend(
                [
                    (
                        codigo,
                        e[1],
                        "AV" if "AV" in str(topo) else "RC",
                        regex.sub(r"^\w{2}_", "", e[0]),
                        e[-1],
                    )
                    for e in estacionamientos
                ]
            )
    estacionamientos_ctc = (
        pd.DataFrame(
            estacionamientos_ctc,
            columns=["Código", "MnemónicoComercial", "TipoVía", "VíaTécnica", "Vía"],
        )
        .drop_duplicates()
        .sort_values(
            by=["Código", "MnemónicoComercial", "TipoVía", "VíaTécnica", "Vía"]
        )
        .reset_index(drop=True)
    )
    return estacionamientos_ctc
