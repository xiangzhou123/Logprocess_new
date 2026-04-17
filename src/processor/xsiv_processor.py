import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import regex

from src.utils import (
    getEstacionamientos,
    isEmpty,
    localizeFecha,
    parallelizeFunction,
    removeDoubleQuotes,
)


class XSIVProcessor:
    def readLogFile(
        self,
        fname: Path,
        train_types: dict[str, str] = None,
        train_operator: str = None,
    ) -> list[str]:
        """
        Lee un fichero log XSIV y devuelve los logs
        """
        logs = []
        # Leemos el fichero y usamos solo los audited
        with fname.open("r", encoding="utf8") as f:
            data = f.read()
            if regex.search('^"timestamp', data):
                data = data.split("\n", 1)[-1]
        lines = removeDoubleQuotes(data)

        for el in lines:
            # try:
            # if "PENDING_TO_CIRCULATE" in el:
            #     continue
            el = el.split('#<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')[
                -1
            ]
            el = el.strip('"\n')
            search = ""
            if isinstance(train_types, dict) and train_types:
                search += "train(" + r"\b|".join(train_types.keys()) + r"\b) "
            # if train_operator:
            #     search += f'.+trainOperatorCode="{train_operator}"'
            # search += '.+registerType="AUDITED"'
            if regex.search(search, el):
                logs.append(el)
        # except:
        #     print(f"Error: {el}")
        return logs

    def processLogInfo(self, log: str):
        """
        Procesa el contenido del log
        """
        info = {}
        mse_xsiv = ET.fromstring(log)
        info.update(dict(mse_xsiv.items()))

        info["movementType"] = mse_xsiv[0].tag
        info.update(dict(mse_xsiv[0].items()))
        for el in mse_xsiv[0]:
            if el.text:
                info[el.tag] = el.text
            info.update({f"{el.tag}_{k}": v for k, v in el.items()})
        return info

    def loadLogFile(
        self,
        fname: Path,
        train_types: dict[str, str] = None,
        train_operator: str = None,
        estaciones: list[str] = [],
    ):
        """
        Devuelve un fichero log XSIV en forma Dataframe
        """
        logs = self.readLogFile(fname, train_types, train_operator)
        data = parallelizeFunction(self.processLogInfo, logs, show_progress=False)
        data_df = pd.DataFrame(data)
        if estaciones and not data_df.empty:
            data_df = data_df[data_df["controlPoint_pointCode"].isin(estaciones)]
        return data_df.drop_duplicates()

    def getLogsInfo(
        self,
        log_list: list[pd.DataFrame],
        train_types: dict[str, str],
        estaciones: list[str] = [],
        format_fechas: bool = True,
    ) -> pd.DataFrame:
        rename_cols = {
            "timestamp": "Fecha",
            "trainProduct": "Producto",
            "trainLaunchingDate": "FechaOrigen",
            "trainCode": "NTécnico",
            "unknownTrain": "Desconocido",
            "controlPoint_departurePlanned": "SalidaPlanificada",
            "controlPoint_arrivalPlanned": "LlegadaPlanificada",
            "controlPoint_sequence": "Secuencia",
            "controlPoint_pointName": "Nombre",
            "controlPoint_pointCode": "Código",
            "platform_platformCode": "Vía",
            "platform_source": "FuenteVía",
            "delay_delayTime": "Retraso",
            "platformPlanned_platformCode": "VíaPlanificada",
            "platformPlanned_plannedPlatform": "VíaPlanificada",
            "circulationState_value": "Estado",
            "movementType": "Movimiento",
            "movementType_type": "MovimientoManiobra",
            "movementSource_source": "FuenteMovimiento",
            "controlPoint_registerType": "Registro",
        }

        df_logs = pd.concat(log_list)
        if df_logs.empty:
            return

        # Añadimos las columnas que faltan y filtramos
        df_logs = df_logs.rename(columns=rename_cols)
        cs = [c for c in rename_cols.values() if c not in df_logs]
        df_logs[cs] = None
        df_logs = df_logs.drop(
            [c for c in df_logs.columns if c not in rename_cols.values()], axis=1
        )

        # Filtramos estaciones
        if estaciones:
            df_logs = df_logs[df_logs["Código"].isin(estaciones)]

        # Filtramos fuentes
        df_logs = (
            df_logs[((df_logs["FuenteVía"] == "CTC") | (df_logs["FuenteVía"].isna()))]
            .drop_duplicates()
            .reset_index(drop=True)
        )

        # Renombramos movimientos
        # df_logs["MovimientoManiobra"] = (
        #     df_logs["MovimientoManiobra"]
        #     .str.capitalize()
        #     .apply(self.getTrainType, train_types=train_types)
        # )
        # df_logs["Movimiento"] = df_logs["Movimiento"].apply(
        #     self.getTrainType, train_types=train_types
        # ) + df_logs["MovimientoManiobra"].apply(lambda x: f"{x}" if pd.notna(x) else "")
        df_logs["Movimiento"] = df_logs["Movimiento"].apply(
            self.getTrainType, train_types=train_types
        )

        # Formateamos fechas
        # df_logs["FechaOrigen"] = df_logs["FechaOrigen"].apply(
        #     lambda x: x.split("+")[0]
        # )
        df_logs["FechaOrigen"] = df_logs["FechaOrigen"].apply(
            lambda x: "".join(regex.findall(r"\d+", x))[:8] if not isEmpty(x) else None
        )
        if format_fechas:
            df_logs[
                ["Fecha", "FechaOrigen", "SalidaPlanificada", "LlegadaPlanificada"]
            ] = localizeFecha(
                df_logs,
                ["Fecha", "FechaOrigen", "SalidaPlanificada", "LlegadaPlanificada"],
                unit="ns",
            )
            df_logs["FechaOrigen"] = df_logs["FechaOrigen"].dt.date

        # Incluimos tipo de vía de estacionamiento
        estacionamientos = getEstacionamientos(estaciones)
        df_logs = pd.merge(
            df_logs,
            estacionamientos[["Código", "TipoVía", "Vía"]],
            how="left",
            on=["Código", "Vía"],
        )
        return df_logs

    def getTrainType(self, ttype: str, train_types: dict[str, str]):
        """
        Devuelve el tipo de tren dada la etiqueta.
        """
        if isEmpty(ttype):
            return
        if not train_types:
            return ttype
        tt = regex.search(r"\b|".join(train_types.keys()) + r"\b", ttype)
        if tt:
            return train_types.get(tt.group())
        return
