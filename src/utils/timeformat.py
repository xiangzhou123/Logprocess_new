import numpy as np
import pandas as pd
import regex
from typing import List
from .util import isEmpty, parallelizeFunction, splitDataframe


def formatTimedelta(t: int):
    """
    Transforma segundos en un formato legible: H:M:S
    """
    dias, horas, minutos, segundos = [0, 0, 0, 0]
    if pd.isna(t):
        return None
    minutos, segundos = divmod(int(np.round(np.abs(t))), 60)
    horas, minutos = divmod(minutos, 60)
    dias, horas = divmod(horas, 24)
    time_string = f"{horas:0>2}:{minutos:0>2}:{segundos:0>2}"
    if dias:
        time_string = f"{dias} {time_string}"
    if t < 0:
        time_string = f"-{time_string}"
    return time_string


def time2iso(t: str):
    """
    Converts timeformats into iso
    """
    if pd.isna(t):
        return pd.NaT
    # return (
    #     pd.to_datetime(t)
    #     .tz_localize("Europe/Madrid")
    #     .tz_convert("UTC")
    #     .tz_localize(None)
    #     .tz_localize("Europe/Madrid")
    #     .isoformat(timespec="milliseconds")
    # )
    return (
        pd.to_datetime(t)
        .tz_localize("Europe/Madrid")
        .tz_convert("UTC")
        .tz_localize(None)
        .isoformat(timespec="milliseconds")
        + "Z"
    )


def time2localtime(
    t,
    # utc: bool = True,
    format: str = None,
    unit: str = None,
):
    """
    Converts timeformats:
        %Y-%m-%dT%H:%M:%S.%f
        %Y-%m-%dT%H:%M:%S.%fZ
        %Y-%m-%dT%H:%M:%S.%f+01:00
    into localtime:
        %Y-%m-%d %H:%M:%S
    """
    if isEmpty(t):
        return pd.NaT
    # return datetime.strptime(
    #     pd.to_datetime(t, utc=True)
    #     .tz_convert("Europe/Madrid")
    #     .tz_localize(None)
    #     .strftime("%Y-%m-%d %H:%M:%S"),
    #     "%Y-%m-%d %H:%M:%S",
    # )
    return (
        pd.to_datetime(
            t,
            utc=True,
            format=format,
            unit=unit,
        )
        .tz_convert("Europe/Madrid")
        .tz_localize(None)
        .round(freq="s")
    )


def dateFromText(text: str):
    res = regex.search(r"(?<![\p{L}\d]+)[\d]{4}(?:-[\d]+)*(?:[\s-]+[\d]+)*(?!\s)", text)
    if res is not None:
        return res.group()
    return None

    # split_char = regex.search("(?<=\.\d{3})[+-Z]", t)
    # if split_char:
    #     d, tz = regex.split("(?<=\.\d{3})[+-Z]", t)
    #     d = datetime.strptime(d, "%Y-%m-%dT%H:%M:%S.%f")
    #     if split_char.group() == "Z":
    #         d = datetime.strptime(
    #             d.strftime("%Y-%m-%d %H:%M:%S.%f") + "Z", "%Y-%m-%d %H:%M:%S.%f%z"
    #         )
    #     else:
    #         pass
    #         # tz_h, tz_m = tz.split(":")
    #         # tz = timedelta(hours=int(tz_h), minutes=int(tz_m))
    #         # if split_char.group() == "+":
    #         #     d = d + tz
    #         # else:
    #         #     d = d - tz
    #     return datetime.strptime(
    #         d.astimezone().strftime("%Y-%m-%d %H:%M:%S"), "%Y-%m-%d %H:%M:%S"
    #     )
    # else:
    #     d = datetime.strptime(t, "%Y-%m-%dT%H:%M:%S.%f")
    # return datetime.strptime(
    #     d.astimezone().strftime("%Y-%m-%d %H:%M:%S"), "%Y-%m-%d %H:%M:%S"
    # )


def localizeFecha(
    df: pd.DataFrame,
    cols: List[str],
    # utc: str = True,
    format: str = None,
    unit: str = None,
):
    """
    Formatea las columnas de un dataframe a fecha local. de forma paralela.
    Es un proceso pesado :(
    """
    fechas = pd.concat(
        parallelizeFunction(
            lambda x: x.map(
                time2localtime,
                # utc=utc,
                format=format,
                unit=unit,
            ),
            data=splitDataframe(df[cols], 1000),
            show_progress=True,
            desc="Formateando fechas.",
            output="series",
        )
    )
    return fechas


def parseDate(date_str):
    formats = ["%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return pd.to_datetime(date_str, format=fmt)
        except ValueError:
            continue
    raise ValueError(f"Fecha '{date_str}' no coincide con los formatos esperados.")
