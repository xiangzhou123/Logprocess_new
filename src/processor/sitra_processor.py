from datetime import timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import regex

from src.utils import (
    isEmpty,
    loadEstaciones,
    loadLocalizaciones,
    localizeFecha,
    parallelizeFunction,
    rellenarId,
    removeDoubleQuotes,
    splitDataframe,
)

map_source_movement = {
    "C": "Automático",
    "M": "Manual",
    "S": "Sistemas / Amortizado",
    "A": "Ager",
    "D": "Davinci",
    "K": "STACrail",
    "P": "Planificación",
}
map_source_parking = {
    "C": "CTC",
    "S": "Sitra",
    "A": "Ager",
    "P": "Planif",
}
map_tipo_movimiento = {
    "EE": "LLEGADA",
    "SE": "SALIDA",
    "SG": "SALIDA Guadiana",
    "EG": "LLEGADA Guadiana",
    "SP": "SUPRESIÓN",
    "stopLapse":"stopLapse"
}


class SitraProcessor:
    def __init__(self):
        # Cargar info general de estaciones
        estaciones = loadEstaciones()
        self.map_codigo_ctc = dict(estaciones[["Código", "CTC"]].values)

        # Cargar localizaciones de estaciones
        localizaciones = loadLocalizaciones()
        self.map_codigo_estacion = dict(localizaciones[["Código", "Nombre"]].values)

    def readLogFile(self, fname: Path):
        with fname.open("r") as f:
            data = f.read()
        data = regex.sub(r'^"timestamp","message"\n?', "", data)
        data = regex.sub(r" +", "", data)
        data = removeDoubleQuotes(data)
        logs = [
            regex.split(r"<\?xml.+?\?>", l.strip('"'))[-1]
            # l.split('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')[-1]
            for l in data
        ]
        # logs = [l.strip('"') for l in regex.split(r"\n?.+?<\?xml.+?\?>", data) if l]
        return logs

    def loadRealParking(self, logs: list[str]):
        parking = [el for el in logs if "realParkingTrack" in el]
        xmls = f"<xml>{''.join(parking)}</xml>"
        df_parking = pd.read_xml(StringIO(xmls))

        rename_cols = {
            "runningDate": "FechaOrigen",
            "runningNumber": "NTécnico",
            "controlPoint": "Código",
            "sequence": "Secuencia",
            "bookedTime": "HoraPlanificada",
            "track": "Vía",
            "assignationType": "Asignacion",
            "timestamp": "Fecha",
            "esbtimestamp": "FechaESB",
            "source": "Fuente",
        }

        df_parking = df_parking[list(rename_cols.keys())].rename(columns=rename_cols)
        df_parking["NTécnico"] = df_parking["NTécnico"].apply(rellenarId)
        df_parking["Código"] = df_parking["Código"].apply(rellenarId)
        df_parking[["Fecha", "FechaESB"]] = (
            localizeFecha(df_parking, ["Fecha", "FechaESB"], format="%Y%m%d%H%M%S%f")
        ).transform(
            lambda x: x.dt.tz_localize("Europe/Madrid").dt.tz_convert(None), axis=0
        )
        df_parking["FechaOrigen"] = (
            df_parking["FechaOrigen"]
            .astype(str)
            .apply(
                lambda x: (
                    "".join(regex.findall(r"\d+", x))[:8] if not isEmpty(x) else None
                )
            )
        )
        df_parking[["FechaOrigen"]] = localizeFecha(
            df_parking, ["FechaOrigen"], format="%Y%m%d"
        )
        df_parking["FechaOrigen"] = df_parking["FechaOrigen"].dt.date
        df_parking: pd.DataFrame = df_parking.drop_duplicates().sort_values(
            by=["Fecha"]
        )
        df_parking["Secuencia"] = df_parking["Secuencia"].astype(int)
        df_parking["Fuente"] = df_parking["Fuente"].apply(
            lambda x: map_source_parking.get(x) if x in map_source_parking else x
        )
        df_parking["Nombre"] = df_parking["Código"].apply(self.map_codigo_estacion.get)
        # df_parking["CTC"] = df_parking["Código"].apply(map_codigo_ctc.get)
        df_parking["Secuencia"] = df_parking["Secuencia"].astype(int).astype(str)
        return df_parking

    def loadRealMovement(self, logs: list[str]):
        movement = [el for el in logs if "realMovement" in el]
        xmls = f"<xml>{''.join(movement)}</xml>"
        df_movement = pd.read_xml(StringIO(xmls))

        rename_cols = {
            "runningDate": "FechaOrigen",
            "runningNumber": "NTécnico",
            "controlPoint": "Código",
            "sequence": "Secuencia",
            "arrivalDelayValue": "RetrasoLlegada",
            "departureDelayValue": "RetrasoSalida",
            "bookedTime": "HoraPlanificada",
            "timestamp": "Fecha",
            "esbtimestamp": "FechaESB",
            "movementType": "Movimiento",
            "source": "Fuente",
            "arrivalDelayCode": "CodigoRetrasoLlegada",
            "departureDelayCode": "CodigoRetrasoSalida"
        }

        df_movement = df_movement[list(rename_cols.keys())].rename(columns=rename_cols)
        df_movement["NTécnico"] = df_movement["NTécnico"].apply(rellenarId)
        df_movement["Código"] = df_movement["Código"].apply(rellenarId)
        df_movement[["Fecha", "FechaESB"]] = (
            localizeFecha(df_movement, ["Fecha", "FechaESB"], format="%Y%m%d%H%M%S%f")
            .transform(
            lambda x: x.dt.tz_localize("Europe/Madrid", ambiguous="infer").dt.tz_convert(None),
            axis=0,
            )
        )

        df_movement["FechaOrigen"] = (
            df_movement["FechaOrigen"]
            .astype(str)
            .apply(
                lambda x: (
                    "".join(regex.findall(r"\d+", x))[:8] if not isEmpty(x) else None
                )
            )
        )
        df_movement[["FechaOrigen"]] = localizeFecha(
            df_movement, ["FechaOrigen"], format="%Y%m%d"
        )
        df_movement["FechaOrigen"] = df_movement["FechaOrigen"].dt.date
        df_movement: pd.DataFrame = df_movement.drop_duplicates().sort_values(
            by=["Fecha"]
        )
        df_movement["Secuencia"] = df_movement["Secuencia"].astype(int)
        df_movement["Fuente"] = df_movement["Fuente"].apply(map_source_movement.get)
        df_movement["Movimiento"] = df_movement["Movimiento"].apply(
            map_tipo_movimiento.get
        )
        df_movement["Nombre"] = df_movement["Código"].apply(
            self.map_codigo_estacion.get
        )
        # df_movement["CTC"] = df_movement["Código"].apply(map_codigo_ctc.get)
        df_movement["Secuencia"] = df_movement["Secuencia"].astype(int).astype(str)

        df_movement["Retraso"] = df_movement.apply(
            lambda x: timedelta(
                minutes=(
                    x["RetrasoLlegada"]
                    if x["Movimiento"] == "LLEGADA"
                    else x["RetrasoSalida"]
                )
            ),
            axis=1,
        )
        return df_movement

    def loadRUOperationRequest(self, logs: list[str]):
        cambios = [el for el in logs if "ruOperationRequest" in el]
        xmls = f"<xml>{''.join(cambios)}</xml>"
        df_cambios = pd.read_xml(StringIO(xmls))

        rename_cols = {
            "runningDate": "FechaOrigen",
            "runningNumber": "NTécnico",
            "startLocation": "CódigoInicio",
            "startLocationSequence":"SecuenciaInicio",
            "startBookedTime": "HoraPlanificadaInicio",
            "enabled": "Activo",
            "operation": "Operación",
            "reason": "Razón",
            # "requestedLapse",
            "timestamp": "Fecha",
            "esbtimestamp": "FechaESB",
            "endLocation": "CódigoFin",
             "endLocationSequence":"SecuenciaFin",
            "endBookedTime":"HoraPlanificadaFin",
        }

        df_cambios = df_cambios[list(rename_cols.keys())].rename(columns=rename_cols)
        df_cambios[["NTécnico", "CódigoInicio", "CódigoFin"]] = df_cambios[
            ["NTécnico", "CódigoInicio", "CódigoFin"]
        ].map(rellenarId)
        df_cambios[["Fecha", "FechaESB"]] = localizeFecha(
            df_cambios, ["Fecha", "FechaESB"]
        )
        # .transform(
        #     lambda x: x.dt.tz_localize("Europe/Madrid").dt.tz_convert(None), axis=0
        # )
        df_cambios["FechaOrigen"] = (
            df_cambios["FechaOrigen"]
            .astype(str)
            .apply(
                lambda x: (
                    "".join(regex.findall(r"\d+", x))[:8] if not isEmpty(x) else None
                )
            )
        )
        df_cambios[["FechaOrigen"]] = localizeFecha(
            df_cambios, ["FechaOrigen"], format="%Y%m%d"
        )
        df_cambios["FechaOrigen"] = df_cambios["FechaOrigen"].dt.date
        df_cambios: pd.DataFrame = df_cambios.drop_duplicates().sort_values(
            by=["Fecha"]
        )
        df_cambios[["NombreInicio", "NombreFin"]] = df_cambios[
            ["CódigoInicio", "CódigoFin"]
        ].map(self.map_codigo_estacion.get)
    

        return df_cambios
