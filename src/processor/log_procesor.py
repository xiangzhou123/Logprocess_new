import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)

from datetime import timedelta
from itertools import zip_longest
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd
import regex
from tqdm.auto import tqdm
from src.utils.topos import getEstacionamientos

from src.processor.mie_processor import MIEProcessor
from src.processor.xsiv_processor import XSIVProcessor
from src.utils import (
    formatTimedelta,
    guardarExcel,
    isEmpty,
    map_cod2name,
    map_name2use_name,
    parallelizeFunction,
    setEF,
)
from src.visualizacion.ocupacion import (
    visualizacionOcupacionVia,
    visualizacionSaturacionVia,
    visualizeAnticipacionVia,
)

PRODUCTO_VACIO = [
    "material vacío",
    "material vacio",
    "material vacío ram",
    "material vacio ram",
    "servicio interno",
]


class LogProcessor:
    def filterTrains(
        self,
        df: pd.DataFrame,
        day: Union[str, pd.Timestamp] = None,
        platform: str = None,
        station: str = None,
    ):
        """
        Devuelve un dataframe con los datos de un día en una vía en una estación
        """

        filt_df = df.copy()

        if day is not None:
            day = pd.to_datetime(day)
            filt_df = filt_df[filt_df["Fecha"].dt.date == day.date()]

        if station is not None:
            filt_df = filt_df[filt_df["Código"] == station]

        if platform is not None:
            trenes = filt_df[filt_df["Vía"] == platform]["NTécnico"].unique()
            filt_df = filt_df[
                (filt_df["NTécnico"].isin(trenes))
                & ((filt_df["Vía"] == platform) | (filt_df["Vía"].isna()))
            ]

        return filt_df

    def splitTrainsByDate(
        self,
        df: pd.DataFrame,
        day: Union[str, pd.Timestamp] = None,
        platform: str = None,
        station: str = None,
        hour_diff: int = 1,
        filter_mov: bool = False,
    ):
        """
        Separa los datos de un tren en una vía un día concreto
        hour_diff hace que los trenes del mismo número separados por más de estas horas se cuenten por separado
        """
        filt_df = self.filterTrains(
            df=df,
            day=day,
            platform=platform,
            station=station,
        )
        if filt_df.empty:
            return None, None

        # Agrupar trenes por número técnico y fecha.
        filt_df = filt_df.sort_values(by=["NTécnico", "Fecha", "mov_ord"]).reset_index(
            drop=True
        )
        filt_df["tdiff"] = filt_df["Fecha"].apply(pd.to_datetime, dayfirst=True).diff()
        # No incluimos la columna tdiff
        t_cols = filt_df.columns[:-1]
        df_split = np.split(
            filt_df[t_cols],
            np.where(
                (~(filt_df["tdiff"] < timedelta(hours=hour_diff)))
                | (~filt_df["NTécnico"].eq(filt_df["NTécnico"].shift()))
            )[0][1:],
        )

        # Como las finalizaciones no tienen vía, nos aseguramos de que no se asigna una vía
        # a un fin si no hay llegada.
        col_fecha = np.where(t_cols == "Fecha")[0][0]
        if not filter_mov:
            return sorted(df_split, key=lambda x: x.iloc[-1]["NTécnico"]), t_cols
        filt_split = []
        for sp in df_split:
            aux_df = pd.DataFrame(sp, columns=t_cols)
            movs = "_".join(aux_df["Movimiento"])
            if (("FIN" in movs) or ("BAJA" in movs)) and not ("LLEGADA" in movs):
                # wrong_fin = np.in1d(sp[:, 1], ["FIN", "BAJA"])
                wrong_fin = np.in1d(aux_df["Movimiento"].values, ["FIN", "BAJA"])
                sp = np.delete(sp, wrong_fin, axis=0)
                if not sp.size:
                    continue
            filt_split.append(sp)
        filt_split = sorted(filt_split, key=lambda x: x[-1][col_fecha])

        return filt_split, t_cols

    def getMovementType(self, t1: str, t2: str, mov_ts: str):
        llegada = r"(MANIOBRA→|PREVISIÓN→|APROXIMACIÓN→)*(MANIOBRA→|LLEGADA→)"
        fin = r"(FIN(→)?|ELIMINACIÓN(→)?|SUPRESIÓN(→)?|BAJA(→)?)(MANIOBRA(→)?|CAMBIO_VÍA)*"
        alta = r"(ALTA→)"
        salida = r"(MANIOBRA(→)?|SALIDA(→)?|CAMBIO_VÍA(→)?)"
        # opt_maniobra = r"(MANIOBRA.*?(→)?)"
        if t1 == t2:
            if regex.search(
                rf"^{llegada}+{salida}+$",
                mov_ts,
            ):
                return "PASO"
            elif regex.search(
                rf"^{alta}+{salida}+$",
                mov_ts,
            ):
                return "ORIGEN"
            elif regex.search(
                rf"^{llegada}+{fin}+$",
                mov_ts,
            ):
                return "FIN"
            else:
                return "INCOMPLETO"
        else:
            if regex.search(
                rf"^{llegada}+({fin}+{alta}+)+{salida}+$",
                mov_ts,
            ):
                return "ROTACIÓN"
            elif regex.search(
                rf"^{llegada}+{fin}+{salida}+$",
                mov_ts,
            ):
                return "FIN"
            elif regex.search(
                rf"^({alta}{fin})*{alta}+{salida}+$",
                mov_ts,
            ):
                return "ORIGEN"
            elif regex.search(
                rf"^{llegada}+{alta}+{salida}+$",
                mov_ts,
            ):
                return "RENOMBRADO"
            else:
                return "ROTACIÓN_INCORRECTA"

    def getProducto(self, p1: str, p2: str):
        if p1 == p2:
            return p1
        elif isEmpty(p1) or p1.lower().strip() in PRODUCTO_VACIO:
            return p2
        elif isEmpty(p2) or p2.lower().strip() in PRODUCTO_VACIO:
            return p1
        return "?"

    def loadFilesLogs(
        self,
        fnames,
        source: str,
        train_types: dict[str, str] = None,
        load: list = ["elemento", "tren"],
        mtype: list[str] = [
            "ocupa",
            "libera",
            "alta",
            "baja",
            # "proyectado",
        ],
        days: str = "",
        estaciones: list[str] = [],
        format_fechas: bool = True,
    ):
        if source == "xsiv":
            processor = XSIVProcessor()
            log_list = parallelizeFunction(
                processor.loadLogFile,
                data=list(set(fnames)),
                train_types=train_types,
                train_operator=None,
                estaciones=estaciones,
                leave=True,
                desc=f"Cargando logs {days}",
            )
            df_logs = processor.getLogsInfo(
                log_list,
                train_types=train_types,
                estaciones=estaciones,
                format_fechas=format_fechas,
            )

        elif source == "mie_mse":
            processor = MIEProcessor()
            log_list = parallelizeFunction(
                processor.loadLogFile,
                data=list(set(fnames)),
                load=load,
                mtype=mtype,
                leave=True,
                desc=f"Cargando logs {days}",
            )
            df_logs = processor.getLogsInfo(
                log_list, estaciones=estaciones, format_fechas=format_fechas
            )
        return df_logs

    ####################################################################
    # SECUENCIAS COMPLETAS
    ####################################################################

    def procesarSecuenciasEstacion(
        self,
        map_vias: dict[str, list[pd.DataFrame]],
        mov_sorter: dict[str, int],
    ):
        """
        Procesa las secuencias de movimientos en una estacion para cada vía
        map_vias: Dataframe con las siguientes columnas:
            - Fecha
            - NTécnico
            - Vía
            - TipoVía
            - Movimiento
        """

        datos = []
        cols = (
            # Trenes
            ["T1", "T2", "T_seq", "ProductoT1", "ProductoT2", "EF", "Vía", "TipoVía"]
            # Planificación
            + ["LlegadaPlanificada", "SalidaPlanificada", "OcupaciónPlanificada"]
            # Secuencias de movimientos
            + ["Movimiento", "Mov_seq", "full_seq", "cambio_vía"]
            # Anticipación
            + [
                "Anticipación",
                "AnticipaciónPlataforma",
                "AnticipaciónSalida",
                "AnticipaciónAproximación",
                "AnticipaciónLlegada",
            ]
            # Ocupación
            + ["InicioOcupación", "FinOcupación", "Ocupación"]
            # # Tiempos totales
            # + ["Inicio", "Fin", "TiempoTotal"]
            # Todos los movimientos posibles
            + list(mov_sorter.keys())
        )

        for via, trains in map_vias.items():
            if not trains:
                continue
            # *TODO*: Procesar primero cada tren por separado y luego unirlos
            # De esta manera evitamos que haya, por ejemplo, aproximaciones entre la llegada y salida de uno anterior:
            # [llegada tren 1 -> aproximación tren 2 -> salida tren 1] -> [llegada tren 2 -> salida tren 2]
            # pasaría a ser: [llegada tren 1 -> salida tren 1] -> [aproximación tren 2 -> llegada tren 2 -> salida tren 2]

            # Componemos los trenes que han pasado por la vía
            df_via = pd.concat(trains).sort_values(by=["Fecha", "mov_ord"])
            # df_via = df_via[
            #     np.invert(df_via["Movimiento"].isin(["APROXIMACIÓN", "PREVISIÓN"]))
            # ]

            # df_via["_prods"] = df_via["Producto"].apply(lambda x: [x, "Material Vacio"])
            # Separamos si hay salida o si el siguiente tren es de otro tipo
            via_split = np.split(
                df_via,
                np.where(
                    (df_via["Movimiento"].shift().isin(["SALIDA", "CAMBIO_VÍA"]))
                    | (
                        np.invert(df_via["NTécnico"].shift().eq(df_via["NTécnico"]))
                        & (
                            np.invert(
                                (df_via["Producto"].shift().eq(df_via["Producto"]))
                                | (
                                    df_via["Producto"]
                                    .shift()
                                    .str.lower()
                                    .isin(PRODUCTO_VACIO)
                                )
                                | (df_via["Producto"].str.lower().isin(PRODUCTO_VACIO))
                                | (df_via["Producto"].shift().apply(isEmpty))
                                | (df_via["Producto"].apply(isEmpty))
                            )
                            | (df_via["Movimiento"].shift().isin(["MANIOBRA"]))
                        )
                    )
                )[0],
            )
            for train_day in via_split:
                if train_day.empty:
                    continue
                # Si unicamente es una aproximación lo tratamos de forma diferente
                if (
                    train_day["Movimiento"]
                    .apply(lambda x: x in ["APROXIMACIÓN", "PREVISIÓN", "ALTA"])
                    .all()
                ):
                    continue
                info = {c: pd.NA for c in cols}
                # # Limpiamos los movimientos de sobra
                # train_day = train_day.drop_duplicates(
                #     subset=[
                #         "Movimiento",
                #         "Producto",
                #         "FechaOrigen",
                #         "NTécnico",
                #         "Código",
                #         "Vía",
                #     ]
                # )

                # Números técnicos
                info["T1"], info["ProductoT1"] = train_day.iloc[0][
                    ["NTécnico", "Producto"]
                ].values
                info["T2"], info["ProductoT2"] = train_day.iloc[-1][
                    ["NTécnico", "Producto"]
                ].values
                vals = train_day["NTécnico"].values
                seq = [vals[0]]
                for v in vals[1:]:
                    if not v == seq[-1]:
                        seq.append(v)
                info["T_seq"] = "→".join(seq)
                if "Empresa" not in train_day.columns:
                    info["EF"] = setEF(
                        "".join(
                            [
                                el if el and pd.notna(el) else ""
                                for el in [info["ProductoT1"], info["ProductoT2"]]
                            ]
                        )
                    )
                else:
                    info["EF"] = train_day["Empresa"].dropna().iloc[0]
                info["Vía"] = via
                tvias = train_day["TipoVía"].dropna()
                if not tvias.empty:
                    info["TipoVía"] = tvias.iloc[0]
                else:
                    info["TipoVía"] = None

                # Movimientos registrados
                for mtype, mdate in (
                    train_day[["Movimiento", "Fecha"]]
                    .drop_duplicates(subset=["Movimiento"])
                    .values
                ):
                    info[mtype] = mdate

                # Secuencia de movimientos registrados
                vals = train_day[["NTécnico", "Movimiento"]].values
                full_seq = [vals[0].tolist()]
                mov_seq = [vals[0][1]]
                for n, v in vals[1:]:
                    # Si el movimiento del tren es igual que el anterior, lo ignoro
                    if n == full_seq[-1][0] and v == full_seq[-1][1]:
                        continue
                    full_seq.append([n, v])
                    mov_seq.append(v)
                info["full_seq"] = full_seq
                info["Mov_seq"] = "→".join(mov_seq)

                # Comprobamos los cambios de vía y usamos el último (debería ser único)
                if "cambio_vía" in train_day.columns:
                    cambio = train_day["cambio_vía"].dropna()
                    if not cambio.empty:
                        info["cambio_vía"] = cambio.iloc[-1]

                # Si hay más de un producto que no sea vacío: error de rotación
                if (
                    train_day["Producto"][
                        np.invert(
                            (train_day["Producto"].eq("Material Vacio"))
                            | (train_day["Producto"].apply(isEmpty))
                        )
                    ]
                    .unique()
                    .shape[0]
                    > 1
                ):
                    info["Movimiento"] = "INCORRECTO"
                    info["EF"] = "INCORRECTO"
                else:
                    info["Movimiento"] = self.getMovementType(
                        info["T1"], info["T2"], info["Mov_seq"]
                    )

                # Planificación
                if "VíaPlanificada" in train_day.columns:
                    plan_arr = train_day["LlegadaPlanificada"].dropna()
                    info["LlegadaPlanificada"] = (
                        plan_arr.iloc[0] if not plan_arr.empty else pd.NaT
                    )
                    plan_dep = train_day["SalidaPlanificada"].dropna()
                    info["SalidaPlanificada"] = (
                        plan_dep.iloc[-1] if not plan_dep.empty else pd.NaT
                    )
                    info["OcupaciónPlanificada"] = (
                        info["SalidaPlanificada"] - info["LlegadaPlanificada"]
                    )

                # Ocupación
                ini_occ = train_day.loc[
                    train_day["Movimiento"].isin(["LLEGADA", "MANIOBRA", "ALTA"]),
                    "Fecha",
                ]
                if not ini_occ.empty:
                    info["InicioOcupación"] = ini_occ.iloc[0]
                end_occ = train_day.loc[
                    train_day["Movimiento"].isin(["SALIDA", "CAMBIO_VÍA", "MANIOBRA"]),
                    "Fecha",
                ]
                if not end_occ.empty:
                    info["FinOcupación"] = end_occ.iloc[-1]
                info["Ocupación"] = info["FinOcupación"] - info["InicioOcupación"]

                # Anticipación
                # Si es origen tendrá alta y salida
                m0 = pd.NaT
                m1 = pd.NaT
                # TODO: Cómo se tienen en cuenta las rotaciones?
                if info["Movimiento"] == "ORIGEN":
                    m0 = train_day.loc[train_day["Movimiento"] == "ALTA", "Fecha"].iloc[
                        0
                    ]
                    m1 = train_day.loc[
                        train_day["Movimiento"].isin(
                            ["SALIDA", "CAMBIO_VÍA", "MANIOBRA"]
                        ),
                        "Fecha",
                    ].iloc[0]
                    info["AnticipaciónPlataforma"] = m0
                    info["AnticipaciónSalida"] = m1

                else:
                    # Si no, buscamos aproximación y llegada
                    appr = train_day.loc[
                        train_day["Movimiento"].isin(["APROXIMACIÓN", "PREVISIÓN"]),
                        "Fecha",
                    ]
                    arr = train_day.loc[
                        train_day["Movimiento"].isin(["LLEGADA", "MANIOBRA"]),
                        "Fecha",
                    ]
                    if not appr.empty:
                        m0 = appr.iloc[0]
                    if not arr.empty:
                        m1 = arr.iloc[0]
                    info["AnticipaciónAproximación"] = m0
                    info["AnticipaciónLlegada"] = m1
                info["Anticipación"] = m1 - m0

                # # Tiempos totales
                # info["Inicio"] = train_day["Fecha"].iloc[0]
                # info["Fin"] = train_day["Fecha"].iloc[-1]
                # info["TiempoTotal"] = info["Fin"] - info["Inicio"]

                datos.append(info)
        info_estacion = pd.DataFrame(datos)
        return info_estacion
    def procesarSecuenciasEstacion_new(
        self,
        map_vias: dict[str, list[pd.DataFrame]],
        mov_sorter: dict[str, int],
    ):
        """
        Procesa las secuencias de movimientos en una estacion para cada vía
        map_vias: Dataframe con las siguientes columnas:
            - Fecha
            - NTécnico
            - Vía
            - TipoVía
            - Movimiento
        """

        datos = []
        cols = (
            # Trenes
            ["T1", "T2", "T_seq", "ProductoT1", "ProductoT2", "EF", "Vía", "TipoVía"]
            # Planificación
            + ["LlegadaPlanificada", "SalidaPlanificada", "OcupaciónPlanificada"]
            # Secuencias de movimientos
            + ["Movimiento", "Mov_seq", "full_seq", "cambio_vía"]
            # Anticipación
            + [
                "Anticipación",
                "AnticipaciónPlataforma",
                "AnticipaciónSalida",
                "AnticipaciónAproximación",
                "AnticipaciónLlegada",
            ]
            # Ocupación
            + ["InicioOcupación", "FinOcupación", "Ocupación"]
            # # Tiempos totales
            # + ["Inicio", "Fin", "TiempoTotal"]
            # Todos los movimientos posibles
            + list(mov_sorter.keys())
        )

        for Elemento, trains in map_vias.items():
            if not trains:
                continue
            # *TODO*: Procesar primero cada tren por separado y luego unirlos
            # De esta manera evitamos que haya, por ejemplo, aproximaciones entre la llegada y salida de uno anterior:
            # [llegada tren 1 -> aproximación tren 2 -> salida tren 1] -> [llegada tren 2 -> salida tren 2]
            # pasaría a ser: [llegada tren 1 -> salida tren 1] -> [aproximación tren 2 -> llegada tren 2 -> salida tren 2]

            # Componemos los trenes que han pasado por la vía
            df_via = pd.concat(trains).sort_values(by=["Fecha", "mov_ord"])
            # df_via = df_via[
            #     np.invert(df_via["Movimiento"].isin(["APROXIMACIÓN", "PREVISIÓN"]))
            # ]

            # df_via["_prods"] = df_via["Producto"].apply(lambda x: [x, "Material Vacio"])
            # Separamos si hay salida o si el siguiente tren es de otro tipo
            via_split = np.split(
                df_via,
                np.where(
                    (df_via["Movimiento"].shift().isin(["SALIDA", "CAMBIO_VÍA"]))
                    | (
                        np.invert(df_via["NTécnico"].shift().eq(df_via["NTécnico"]))
                        & (
                            np.invert(
                                (df_via["Producto"].shift().eq(df_via["Producto"]))
                                | (
                                    df_via["Producto"]
                                    .shift()
                                    .str.lower()
                                    .isin(PRODUCTO_VACIO)
                                )
                                | (df_via["Producto"].str.lower().isin(PRODUCTO_VACIO))
                                | (df_via["Producto"].shift().apply(isEmpty))
                                | (df_via["Producto"].apply(isEmpty))
                            )
                            | (df_via["Movimiento"].shift().isin(["MANIOBRA"]))
                        )
                    )
                )[0],
            )
            for train_day in via_split:
                if train_day.empty:
                    continue
                # Si unicamente es una aproximación lo tratamos de forma diferente
                if (
                    train_day["Movimiento"]
                    .apply(lambda x: x in ["APROXIMACIÓN", "PREVISIÓN", "ALTA"])
                    .all()
                ):
                    continue
                info = {c: pd.NA for c in cols}
                # # Limpiamos los movimientos de sobra
                # train_day = train_day.drop_duplicates(
                #     subset=[
                #         "Movimiento",
                #         "Producto",
                #         "FechaOrigen",
                #         "NTécnico",
                #         "Código",
                #         "Vía",
                #     ]
                # )

                # Números técnicos
                info["T1"], info["ProductoT1"] = train_day.iloc[0][
                    ["NTécnico", "Producto"]
                ].values
                info["T2"], info["ProductoT2"] = train_day.iloc[-1][
                    ["NTécnico", "Producto"]
                ].values
                if "Código" in train_day.columns:
                    codigo = train_day["Código"].dropna()
                    info["Código"] = codigo.iloc[0] if not codigo.empty else pd.NA
                vals = train_day["NTécnico"].values
                seq = [vals[0]]
                for v in vals[1:]:
                    if not v == seq[-1]:
                        seq.append(v)
                info["T_seq"] = "→".join(seq)
                if "Empresa" not in train_day.columns:
                    info["EF"] = setEF(
                        "".join(
                            [
                                el if el and pd.notna(el) else ""
                                for el in [info["ProductoT1"], info["ProductoT2"]]
                            ]
                        )
                    )
                else:
                    empresa = train_day["Empresa"].dropna()
                    if not empresa.empty:
                        info["EF"] = empresa.iloc[0]
                    else:
                        info["EF"] = pd.NA
                    # info["EF"] = train_day["Empresa"].dropna().iloc[0]
                    info["Elemento"] = Elemento

                # Movimientos registrados
                for mtype, mdate in (
                    train_day[["Movimiento", "Fecha"]]
                    .drop_duplicates(subset=["Movimiento"])
                    .values
                ):
                    info[mtype] = mdate

                # Secuencia de movimientos registrados
                vals = train_day[["NTécnico", "Movimiento"]].values
                full_seq = [vals[0].tolist()]
                mov_seq = [vals[0][1]]
                for n, v in vals[1:]:
                    # Si el movimiento del tren es igual que el anterior, lo ignoro
                    if n == full_seq[-1][0] and v == full_seq[-1][1]:
                        continue
                    full_seq.append([n, v])
                    mov_seq.append(v)
                info["full_seq"] = full_seq
                info["Mov_seq"] = "→".join(mov_seq)

                # Comprobamos los cambios de vía y usamos el último (debería ser único)
                if "cambio_vía" in train_day.columns:
                    cambio = train_day["cambio_vía"].dropna()
                    if not cambio.empty:
                        info["cambio_vía"] = cambio.iloc[-1]

                # Si hay más de un producto que no sea vacío: error de rotación
                if (
                    train_day["Producto"][
                        np.invert(
                            (train_day["Producto"].eq("Material Vacio"))
                            | (train_day["Producto"].apply(isEmpty))
                        )
                    ]
                    .unique()
                    .shape[0]
                    > 1
                ):
                    info["Movimiento"] = "INCORRECTO"
                    info["EF"] = "INCORRECTO"
                else:
                    info["Movimiento"] = self.getMovementType(
                        info["T1"], info["T2"], info["Mov_seq"]
                    )

                # Planificación
                if "VíaPlanificada" in train_day.columns:
                    plan_arr = train_day["LlegadaPlanificada"].dropna()
                    info["LlegadaPlanificada"] = (
                        plan_arr.iloc[0] if not plan_arr.empty else pd.NaT
                    )
                    plan_dep = train_day["SalidaPlanificada"].dropna()
                    info["SalidaPlanificada"] = (
                        plan_dep.iloc[-1] if not plan_dep.empty else pd.NaT
                    )
                    info["OcupaciónPlanificada"] = (
                        info["SalidaPlanificada"] - info["LlegadaPlanificada"]
                    )

                # Ocupación
                ini_occ = train_day.loc[
                    train_day["Movimiento"].isin(["LLEGADA", "MANIOBRA", "ALTA"]),
                    "Fecha",
                ]
                if not ini_occ.empty:
                    info["InicioOcupación"] = ini_occ.iloc[0]
                end_occ = train_day.loc[
                    train_day["Movimiento"].isin(["SALIDA", "CAMBIO_VÍA", "MANIOBRA"]),
                    "Fecha",
                ]
                if not end_occ.empty:
                    info["FinOcupación"] = end_occ.iloc[-1]
                info["Ocupación"] = info["FinOcupación"] - info["InicioOcupación"]

                # Anticipación
                # Si es origen tendrá alta y salida
                m0 = pd.NaT
                m1 = pd.NaT
                # TODO: Cómo se tienen en cuenta las rotaciones?
                if info["Movimiento"] == "ORIGEN":
                    m0 = train_day.loc[train_day["Movimiento"] == "ALTA", "Fecha"].iloc[
                        0
                    ]
                    m1 = train_day.loc[
                        train_day["Movimiento"].isin(
                            ["SALIDA", "CAMBIO_VÍA", "MANIOBRA"]
                        ),
                        "Fecha",
                    ].iloc[0]
                    info["AnticipaciónPlataforma"] = m0
                    info["AnticipaciónSalida"] = m1

                else:
                    # Si no, buscamos aproximación y llegada
                    appr = train_day.loc[
                        train_day["Movimiento"].isin(["APROXIMACIÓN", "PREVISIÓN"]),
                        "Fecha",
                    ]
                    arr = train_day.loc[
                        train_day["Movimiento"].isin(["LLEGADA", "MANIOBRA"]),
                        "Fecha",
                    ]
                    if not appr.empty:
                        m0 = appr.iloc[0]
                    if not arr.empty:
                        m1 = arr.iloc[0]
                    info["AnticipaciónAproximación"] = m0
                    info["AnticipaciónLlegada"] = m1
                info["Anticipación"] = m1 - m0

                # # Tiempos totales
                # info["Inicio"] = train_day["Fecha"].iloc[0]
                # info["Fin"] = train_day["Fecha"].iloc[-1]
                # info["TiempoTotal"] = info["Fin"] - info["Inicio"]

                datos.append(info)
        info_estacion = pd.DataFrame(datos)
        return info_estacion

    def getOccTime(
        self,
        row: pd.Series,
        min_date: pd.Timestamp,
        max_date: pd.Timestamp,
        hour_period: int = 3,
        margen: int = 20,
    ):
        """
        Obtener tiempo de ocupación de una vía en una estación por tramos horarios (periodos de `hour_period` horas).
        Se establece un `margen` de seguridad mínimo (en minutos) antes y después de la ocupación.
        """
        date_range = pd.date_range(
            min_date, max_date, freq=timedelta(hours=hour_period)
        )
        ini = row["InicioOcupación"] - timedelta(minutes=margen)
        fin = row["FinOcupación"] + timedelta(minutes=margen)
        cod = row["Código"]
        via = row["Vía"]
        tipo_via = row.get("TipoVía", None)
        seq = row.get("T_seq", None)
        occ = []
        for i in range(len(date_range) - 1):
            start = date_range[i]
            end = date_range[i + 1]

            if (ini >= start and ini < end) or (ini < start and fin > start):
                duration = (min(fin, end) - max(ini, start)).total_seconds()

                # 👇 ESTRUCTURA FIJA SIEMPRE
                occ.append(
                    (
                        cod,
                        via,
                        tipo_via,
                        start,
                        duration,
                        seq,
                    )
                )

        return occ


    def getSaturation(
        self,
        df: pd.DataFrame,
        min_date: pd.Timestamp,
        max_date: pd.Timestamp,
        hour_period: int = 3,
        margen: int = 20,
        modo: str = "ocupado",
    ):
        """
        Obtener la saturación de vías de todas las estaciones por tramos horarios (periodos de `seconds_period` segundos).
        modo: {"ocupado", "libre"}
        """
        if df is None or df.empty:
            return None

        df_aux = df.copy()
        # Rellenamos valores vacíos
        df_aux.loc[df_aux["InicioOcupación"].isna(), "InicioOcupación"] = df_aux.loc[
            df_aux["InicioOcupación"].isna(), "FinOcupación"
        ]
        df_aux.loc[df_aux["FinOcupación"].isna(), "FinOcupación"] = df_aux.loc[
            df_aux["FinOcupación"].isna(), "InicioOcupación"
        ]
        df_aux = df_aux.dropna(subset=["InicioOcupación", "FinOcupación"], how="all")

        if modo == "ocupado":
            saturation = pd.DataFrame(
                df_aux.apply(
                    self.getOccTime,
                    min_date=min_date,
                    max_date=max_date,
                    hour_period=hour_period,
                    margen=margen,
                    axis=1,
                )
                .explode()
                .dropna()
                .tolist(),
                columns=["Código", "Vía", "TipoVía", "Fecha", "Ocupación", "T_seq"],
            )
            saturation = (
                saturation.groupby(
                    by=["Código", "Vía", "TipoVía", "Fecha"], dropna=False
                )
                .agg({"Ocupación": "sum", "T_seq": lambda x: "→".join(x)})
                .reset_index()
            )
            saturation["Trenes"] = saturation["T_seq"].apply(
                lambda x: list(set(x.split("→")))
            )
            saturation["FinOcupación"] = saturation["Fecha"] + saturation[
                "Ocupación"
            ].apply(lambda x: timedelta(seconds=x))
            total_secs = hour_period * 3600
            saturation["Occ"] = saturation["Ocupación"] / total_secs
            saturation["Ocupación (%)"] = saturation["Occ"].apply(
                lambda x: f"{x*100:.3f}%"
            )
        elif modo == "libre":
            saturation = pd.DataFrame(
                df_aux.apply(
                    self.getOccTime,
                    min_date=min_date,
                    max_date=max_date,
                    hour_period=hour_period,
                    margen=margen,
                    axis=1,
                )
                .explode()
                .dropna()
                .tolist(),
                columns=["Código", "Vía", "TipoVía", "Fecha", "Libre", "T_seq"],
            )
            saturation = (
                saturation.groupby(
                    by=["Código", "Vía", "Fecha"], dropna=False
                )
                .agg({"Libre": "sum"})
                .reset_index()
            )
            saturation["FinLibre"] = saturation.apply(
                lambda x: x["Fecha"] + timedelta(seconds=x["Libre"]), axis=1
            )
            total_secs = hour_period * 3600
            saturation["Lib"] = saturation["Libre"] / total_secs
            saturation["Libre (%)"] = saturation["Lib"].apply(lambda x: f"{x*100:.3f}%")
        return saturation

    def getTiemposEstaciones(self, df_logs: pd.DataFrame):
        """
        Obtener el dataframe de tiempos de viaje entre la lista de estaciones que hay en `df_logs`.
        """
        t_cols = df_logs.columns
        col_ntech = np.where(t_cols == "NTécnico")[0][0]
        col_mov = np.where(t_cols == "Movimiento")[0][0]
        col_cod = np.where(t_cols == "Código")[0][0]
        col_fecha = np.where(t_cols == "Fecha")[0][0]
        col_diffs = np.where(~t_cols.isin(["NTécnico", "Movimiento", "tdiff"]))[0]

        use_df = df_logs[df_logs["Movimiento"].isin(["LLEGADA", "SALIDA"])].copy()

        df_split = np.split(
            use_df.values,
            np.where(
                (~use_df["NTécnico"].eq(use_df["NTécnico"].shift()))
                | (use_df["tdiff"] > timedelta(hours=10))
            )[0][1:],
        )
        trayectos = []
        total = []
        for t_split in df_split:
            sub_t = pd.DataFrame(t_split, columns=t_cols)
            tray = np.split(
                sub_t.values,
                np.where(
                    (
                        (sub_t["Código"].eq(sub_t["Código"].shift()))
                        | (
                            (sub_t["Movimiento"] == "SALIDA")
                            & (sub_t["Movimiento"].shift() == "LLEGADA")
                        )
                        | (
                            sub_t[["Código", "Movimiento"]]
                            == sub_t[["Código", "Movimiento"]].shift()
                        ).any(axis=1)
                    )
                )[0],
            )
            total.extend(tray)
            trayectos.extend(
                [
                    el
                    for el in tray
                    if len(el) == 2
                    and not el[0, col_cod] == el[1, col_cod]
                    and el[0, col_mov] == "SALIDA"
                    and el[1, col_mov] == "LLEGADA"
                ]
            )
        use_cols = (
            ["NTécnico"]
            + [c + "_orig" for c in t_cols[col_diffs]]
            + [c + "_dest" for c in t_cols[col_diffs]]
            + ["TiempoTrayecto", "TiempoTrayecto (segundos)"]
        )
        t_trayectos = []
        for tray in trayectos:
            if len(tray) == 2:
                if not (
                    (tray[0, col_mov] == "SALIDA") and (tray[1, col_mov] == "LLEGADA")
                ):
                    continue
                t_trayectos.append(
                    (
                        [tray[0, col_ntech]]
                        + tray[0, col_diffs].tolist()
                        + tray[1, col_diffs].tolist()
                        + [tray[1, col_fecha] - tray[0, col_fecha]]
                        + [None]
                    )
                )
            elif len(tray) == 1:
                t_trayectos.append(([tray[0, col_ntech]] + tray[0, col_diffs].tolist()))
            else:
                continue
        t_trayectos = pd.DataFrame(t_trayectos, columns=use_cols)
        if not t_trayectos.empty:
            t_trayectos["TiempoTrayecto (segundos)"] = t_trayectos[
                "TiempoTrayecto"
            ].dt.total_seconds()
            t_trayectos["TiempoTrayecto"] = t_trayectos[
                "TiempoTrayecto (segundos)"
            ].apply(formatTimedelta)
        return t_trayectos

    def processStation(
        self,
        station: str,
        df: pd.DataFrame,
        mov_sorter: dict[str, int],
        save_info: bool,
        save_dir: Path = None,
        days: str = "",
        list_rotaciones: list[tuple[str, str]] = [],
        hour_diff: int = 12,
    ):
        df_logs = df[(df["Código"] == station)].copy()
        df_logs["mov_ord"] = df_logs["Movimiento"].apply(mov_sorter.get)
        df_logs = df_logs.sort_values(by=["NTécnico", "Fecha", "mov_ord"])

        # Sacamos info de los logs para hacer mappings de valores
        map_estacion_via = (
            df_logs[["Código", "Vía"]]
            .dropna()
            .groupby("Código")
            .agg(lambda x: {el: [] for el in set(x)})
            .to_dict()["Vía"]
        )
        if station not in map_cod2name:
            aux = df_logs[["Código", "Nombre"]].dropna().drop_duplicates()
            if aux.empty:
                return
            map_cod2name.update(dict(aux.values))
            name = map_cod2name[station]
            use_name = regex.sub(r"[^\w\s-\(\)]", "", name.lower())
            use_name = regex.sub(r"[\s-]+", "_", use_name)
            map_name2use_name.update({name: use_name})
        # map_codigo_nombre = dict(
        #     df_logs[["Código", "Nombre"]].dropna().drop_duplicates().values
        # )

        # Comprobamos los movimientos de cada tren en la estación al completo
        df_split, t_cols = self.splitTrainsByDate(
            df=df_logs,
            # platform=via,
            station=station,
            hour_diff=hour_diff,
            filter_mov=False,
        )
        if not df_split:
            return None, None
        for df_t in df_split:
            if df_t.empty:
                return None, None
            # Si unicamente es una aproximación lo tratamos de forma diferente
            if (
                df_t["Movimiento"]
                .apply(
                    lambda x: x
                    in [
                        "APROXIMACIÓN",
                        "PREVISIÓN",
                        "ALTA",
                    ]
                )
                .all()
            ):
                continue
            # Componemos los trenes que han pasado por cada vía, excluyendo maniobras vacías
            df_t = df_t[
                np.invert(
                    (df_t["Movimiento"].isin(["MANIOBRA"])) & (df_t["Vía"].isna())
                )
            ].copy()
            df_t["Vía"] = df_t["Vía"].ffill()
            df_t["TipoVía"] = df_t["TipoVía"].ffill()

            # Prueba de concepto para reordenar en caso de que haya un pequeña diferencia de tiempo
            # entre movimientos que deberían estar al revés (ej. salida antes que llegada)
            df_t["tdiff"] = df_t["Fecha"].diff().dt.total_seconds()
            df_t["mdiff"] = df_t["mov_ord"].diff()
            df_t = df_t.reset_index(drop=True).reset_index()
            df_t["prev_index"] = df_t["index"].shift(fill_value=-1)
            for i, row in df_t[1:].iterrows():
                if (row["tdiff"] < 5) & (row["mdiff"] < 0):
                    row = row.copy()
                    aux_row = df_t.loc[row["prev_index"]].copy()
                    mdate = [row["Fecha"], aux_row["Fecha"]]
                    aux_row["Fecha"] = max(mdate)
                    row["Fecha"] = min(mdate)
                    df_t.loc[row["prev_index"]] = row
                    df_t.loc[i] = aux_row
            #############################################################

            # Eliminamos altas repetidas que puedan no ser ocupaciones reales
            drop_idx = df_t.loc[
                df_t["Movimiento"].isin(["ALTA"])
                & df_t.duplicated(
                    subset=["NTécnico", "Código", "Secuencia", "Movimiento", "Vía"],
                    keep="last",
                ),
                "index",
            ]
            df_t = df_t.drop(drop_idx, axis=0)

            # Separamos por vía
            df_t_split = np.split(
                df_t,
                np.where((~df_t["Vía"].eq(df_t["Vía"].shift())))[0][1:],
            )
            apariciones = len(df_t_split)
            for n, train_day in enumerate(df_t_split, 1):
                # Si no hay ninguna vía pasamos
                if (train_day["Vía"].isna().all()) or (
                    train_day["Movimiento"]
                    .apply(
                        lambda x: x
                        in [
                            "APROXIMACIÓN",
                            "PREVISIÓN",
                            "ALTA",
                        ]
                    )
                    .all()
                ):
                    continue
                # Si aparece en otra vía, creamos una "salida" provisional a la hora que aparece en la otra
                if n < apariciones and not train_day.iloc[-1]["Movimiento"] == "SALIDA":
                    # Comprobamos que la siguiente aparición no sea aproximación
                    next_aparicion = df_t_split[n].dropna(subset=["Vía"])
                    next_aparicion = next_aparicion[
                        np.invert(
                            next_aparicion["Movimiento"].apply(
                                lambda x: x
                                in [
                                    "APROXIMACIÓN",
                                    "PREVISIÓN",
                                    # "ALTA",
                                ]
                            )
                        )
                    ]
                    if not next_aparicion.empty:
                        new_row = train_day.iloc[-1:].copy()
                        next_aparicion = next_aparicion.iloc[0]
                        # new_row["Fecha"] = next_aparicion["Fecha"]
                        new_row["Movimiento"] = "CAMBIO_VÍA"
                        new_row["cambio_vía"] = [
                            {
                                "Fecha": next_aparicion["Fecha"],
                                "Vía": next_aparicion["Vía"],
                            }
                        ]
                        # # Asignamos la fecha de la siguiente aparición
                        # new_fecha = next_aparicion.iloc[0]["Fecha"]
                        # new_row["Fecha"] = new_fecha
                        train_day = pd.concat([train_day, new_row])
                via = train_day["Vía"].dropna().iloc[0]
                map_estacion_via[station][via].append(train_day)

        station_historic = self.procesarSecuenciasEstacion(
            map_estacion_via[station], mov_sorter
        )
        if station_historic.empty:
            return None, None

        station_historic = station_historic.reset_index(drop=True)
        station_historic["Código"] = station
        station_historic["Estación"] = map_cod2name[station]
        station_historic["Producto"] = station_historic[
            ["ProductoT1", "ProductoT2"]
        ].apply(lambda x: self.getProducto(x["ProductoT1"], x["ProductoT2"]), axis=1)

        # Formateamos fechas/horarios
        cols_tiempos = ["Anticipación", "OcupaciónPlanificada", "Ocupación"]
        cols_tiempos = [c for c in cols_tiempos if c in station_historic.columns]
        station_historic[[f"{c} (segundos)" for c in cols_tiempos]] = station_historic[
            cols_tiempos
        ].map(lambda x: x.total_seconds() if pd.notna(x) else 0)
        station_historic[cols_tiempos] = station_historic[cols_tiempos].map(
            lambda x: formatTimedelta(x.total_seconds()) if pd.notna(x) else x
        )

        # Buscamos rotaciones que nos han dado
        station_historic["RotaciónValidada"] = None
        if list_rotaciones:
            in_rotaciones = []
            for _, r in station_historic.iterrows():
                if r["Movimiento"] == "ROTACIÓN":
                    in_rotaciones.append(
                        tuple(r[["T1", "T2"]].values) in list_rotaciones
                    )
                else:
                    in_rotaciones.append(None)
            station_historic["RotaciónValidada"] = in_rotaciones

        # Hacemos la representación para cada elemento
        use_df = station_historic.copy()
        # Buscamos fallos para excluirlos y los marcamos en el historico
        use_df["Fallo"] = False
        fallos = []
        for v in use_df["Vía"].unique():
            ex = use_df[use_df["Vía"] == v].sort_values(by="InicioOcupación")
            fail = np.where(
                # fin occ. después que inicio occ. siguiente
                (ex["FinOcupación"] > ex["InicioOcupación"].shift(-1))
                # inicio occ. antes que fin occ. anterior
                | (ex["InicioOcupación"] < ex["FinOcupación"].shift(1))
                # fin occ. antes que inicio occ.
                | (ex["InicioOcupación"] > ex["FinOcupación"])
            )[0]
            fallos.extend(ex.iloc[fail].index.tolist())
        use_df.loc[fallos, "Fallo"] = True

        sname = map_cod2name[station]
        fname = f"{station} {map_name2use_name[sname]}"
        # fname = f'{station} {sname.lower().replace(" ", "_").replace("-", "_")}'
        if save_info:
            if not save_dir:
                print("No se ha seleccionado directorio")
                save_dir = Path("outputs")
                save_dir.mkdir(exist_ok=True)
                print(f"Guardando en {save_dir}")
            self.saveInfo(
                use_df,
                save_dir=save_dir,
                fname=fname,
                nombre_estacion=sname,
                days=days,
            )

        return use_df, sname


    def getTramosLibres(self, df: pd.DataFrame, margen: int = 10, t_min: int = 5):
        """
        Genera un dataframe de tramos libres a partir de un dataframe de ocupación.
        Parámetros:
        -----------
        df: pd.DataFrame
            Tabla de ocupaciones con, al menos:
            - "InicioOcupación"
            - "FinOcupación"
            - "Código"
            - "Vía"
            - "TipoVía"

        margen: int
            Tiempo de seguridad mínimo (en minutos) entre ocupaciones.
        t_min: int
            Duración mínima de ocupación (en minutos).
        """
        df_aux = df.copy()
        # Rellenamos valores vacíos
        df_aux.loc[df_aux["InicioOcupación"].isna(), "InicioOcupación"] = df_aux.loc[
            df_aux["InicioOcupación"].isna(), "FinOcupación"
        ]
        df_aux.loc[df_aux["FinOcupación"].isna(), "FinOcupación"] = df_aux.loc[
            df_aux["FinOcupación"].isna(), "InicioOcupación"
        ]

        # Se incluyen las fechas de las que se dispone en el dataframe
        if df_aux[["InicioOcupación", "FinOcupación"]].dropna().empty:
            return pd.DataFrame(
                columns=[
                    "Código",
                    "Vía",
                    "TipoVía",
                    "InicioLibre",
                    "FinLibre",
                    "Libre (segundos)",
                    "Libre",
                ]
            )
        min_date = df_aux[["InicioOcupación", "FinOcupación"]].dropna().values.min()
        max_date = df_aux[["InicioOcupación", "FinOcupación"]].dropna().values.max()
        data_free_time = []
        for cod in df_aux["Código"].unique():
            for via in df_aux.loc[df_aux["Código"] == cod, "Vía"].unique():
                df_via = df_aux[
                    (df_aux["Código"] == cod) & (df_aux["Vía"] == via)
                ].fillna(pd.NaT)
                ini_libre = pd.concat(
                    (df_via["FinOcupación"], pd.Series([min_date]))
                ).sort_values() + timedelta(minutes=margen)
                fin_libre = pd.concat(
                    (df_via["InicioOcupación"], pd.Series([max_date]))
                ).sort_values() - timedelta(minutes=margen)

                for d_ini, d_end in zip_longest(ini_libre, fin_libre, fillvalue=pd.NaT):
                    data_free_time.append(
                        [cod, via, d_ini, d_end, (d_end - d_ini).total_seconds()]
                    )

        map_tipo_via = dict(
            df_aux.loc[
                ~(df_aux[["Vía", "TipoVía"]] == "").any(axis=1), ["Vía", "TipoVía"]
            ]
            .dropna()
            .drop_duplicates()
            .values
        )
        map_tipo_via_tiempo = {"AV": 45, "RC": 25}
        df_free = pd.DataFrame(
            data_free_time,
            columns=[
                "Código",
                "Vía",
                "InicioLibre",
                "FinLibre",
                "Libre (segundos)",
            ],
        ).dropna()
        df_free["TipoVía"] = df_free["Vía"].apply(map_tipo_via.get)
        df_free["Libre"] = df_free["Libre (segundos)"].apply(formatTimedelta)
        df_free = df_free[
            df_free[["Libre (segundos)", "InicioLibre", "FinLibre", "TipoVía"]].apply(
                lambda x: (
                    x["Libre (segundos)"]
                    >= map_tipo_via_tiempo.get(x["TipoVía"], 0) * 60
                )
                & (x["TipoVía"] in map_tipo_via_tiempo.keys())
                & (x["FinLibre"] > x["InicioLibre"]),
                axis=1,
            )
        ]
        # df_free = df_free[
        #     (df_free["Libre (segundos)"] >= t_min * 60)
        #     & (df_free["FinLibre"] > df_free["InicioLibre"])
        # ]
        df_free[["HoraInicioLibre", "HoraFinLibre"]] = df_free[
            ["InicioLibre", "FinLibre"]
        ].map(lambda x: x.strftime("%H:%M:%S") if x and pd.notna(x) else "")
        return df_free

    def getMargenes(self, df: pd.DataFrame, margen: int):
        """
        Genera un dataframe de márgenes a partir de un dataframe de ocupación.
        Parámetros:
        -----------
        df: pd.DataFrame
            Tabla de ocupaciones con, al menos:
            - "InicioOcupación"
            - "FinOcupación"
            - "Código"
            - "Vía"
            - "TipoVía"
        margen: int
            Tiempo de seguridad mínimo (en minutos) entre ocupaciones.
        """
        df_aux = df.copy()
        # Rellenamos valores vacíos
        df_aux.loc[df_aux["InicioOcupación"].isna(), "InicioOcupación"] = df_aux.loc[
            df_aux["InicioOcupación"].isna(), "FinOcupación"
        ]
        df_aux.loc[df_aux["FinOcupación"].isna(), "FinOcupación"] = df_aux.loc[
            df_aux["FinOcupación"].isna(), "InicioOcupación"
        ]

        margenes = []
        for cod, via, ini, fin in df_aux[
            ["Código", "Vía", "InicioOcupación", "FinOcupación"]
        ].values:
            mg = []
            if not pd.isna(ini):
                bef = ini - timedelta(minutes=margen)
                mg.append([cod, via, bef, ini, (ini - bef).total_seconds()])
            if not pd.isna(fin):
                aft = fin + timedelta(minutes=margen)
                mg.append([cod, via, fin, aft, (aft - fin).total_seconds()])
            margenes.extend(mg)
        return pd.DataFrame(
            margenes,
            columns=["Código", "Vía", "InicioOcupación", "FinOcupación", "Margen"],
        )

    # ####################################################################
    # # APROXIMACIONES
    # ####################################################################
    # def processAproximacionLlegada(
    #     self, split: list, t_cols: list, mov_sorter: dict[str, int]
    # ):
    #     cols = (
    #         # Trenes
    #         ["Tren", "Producto", "Vía", "VíaPlanificada"]
    #         # Secuencias de movimientos
    #         + ["Movimiento", "Mov_seq"]
    #         # Anticipación
    #         + ["Anticipación", "Previsión", "Aproximación", "Llegada"]
    #         # Tiempos todales
    #         + ["Inicio", "Fin", "TiempoTotal"]
    #         # Todos los movimientos posibles
    #         + ["APROXIMACIÓN", "LLEGADA"]
    #     )

    #     info = {c: pd.NA for c in cols}

    #     if not len(split):
    #         return info

    #     def getMovementType(mov_ts: str):
    #         if regex.search(r"\b(APROXIMACIÓN_)+(LLEGADA(_)?)+\b", mov_ts):
    #             return "COMPLETO"
    #         elif regex.search(r"\b(APROXIMACIÓN(_)?)+\b", mov_ts):
    #             return "APROXIMACIÓN"
    #         elif regex.search(r"\b(LLEGADA(_)?)+\b", mov_ts):
    #             return "LLEGADA"

    #     train_day = pd.DataFrame(split, columns=t_cols)
    #     # Fill info
    #     # Números técnicos
    #     info["Tren"] = train_day["NTécnico"].iloc[0]
    #     info["Producto"] = train_day["Producto"].iloc[0]
    #     info["Código"] = train_day["Código"].iloc[0]
    #     # info["Nombre"] = train_day["Nombre"].iloc[0]
    #     info["Nombre"] = map_cod2name[info["Código"]]
    #     info["Vía"] = train_day["Vía"].iloc[0]

    #     # Movimientos registrados
    #     for mtype in ["APROXIMACIÓN", "LLEGADA"]:
    #         aux_mov = train_day.loc[train_day["Movimiento"] == mtype, "Fecha"]
    #         if not aux_mov.empty:
    #             info[mtype] = aux_mov.iloc[0]

    #     # Secuencia de movimientos registrados
    #     # Guardamos la lista de los primeros movimientos de cada tipo en orden
    #     # val_movs = [
    #     #     (k, v) for k, v in info.items() if k in mov_sorter.keys() and pd.notna(v)
    #     # ]
    #     # mov_ts = "_".join([el[0] for el in sorted(val_movs, key=lambda x: x[1])])
    #     mov = train_day["Movimiento"].values
    #     info["Mov_seq"] = "_".join(mov)
    #     info["Movimiento"] = getMovementType(info["Mov_seq"])

    #     # Anticipación
    #     # Si es origen tendrá alta y salida
    #     m0 = pd.NaT
    #     m1 = pd.NaT
    #     # Si no, buscamos aproximación y llegada
    #     # appr = train_day.loc[train_day["Movimiento"] == "APROXIMACIÓN", "Fecha"]
    #     appr = train_day.loc[
    #         train_day["Movimiento"].isin(
    #             [
    #                 "APROXIMACIÓN",
    #                 "PREVISIÓN",
    #             ]
    #         ),
    #         "Fecha",
    #     ]
    #     arr = train_day.loc[
    #         train_day["Movimiento"].isin(["LLEGADA", "MANIOBRALLEGADA"]),
    #         "Fecha",
    #     ]
    #     if not appr.empty:
    #         m0 = appr.iloc[0]
    #         info["VíaPlanificada"] = train_day.loc[
    #             train_day["Movimiento"] == "APROXIMACIÓN", "VíaPlanificada"
    #         ].iloc[0]
    #     if not arr.empty:
    #         m1 = arr.iloc[0]
    #     info["Aproximación"] = m0
    #     info["Llegada"] = m1
    #     info["Anticipación"] = formatTimedelta((m1 - m0).total_seconds())

    #     # Fechas
    #     info["Inicio"] = train_day["Fecha"].iloc[0]
    #     info["Fin"] = train_day["Fecha"].iloc[-1]
    #     info["TiempoTotal"] = formatTimedelta(
    #         (info["Fin"] - info["Inicio"]).total_seconds()
    #     )

    #     return info

    # def getWrongApprox(
    #     self,
    #     station: str,
    #     df_aproximaciones: pd.DataFrame,
    #     mov_sorter: dict[str, int],
    # ):
    #     historicos = []
    #     map_estacion_via = (
    #         df_aproximaciones[["Código", "Vía"]]
    #         .dropna()
    #         .groupby("Código")
    #         .agg(lambda x: sorted(list(set(x))))
    #         .to_dict()["Vía"]
    #     )
    #     for platform in map_estacion_via.get(station, []):
    #         if not platform:
    #             continue
    #         df_split, t_cols = self.splitTrainsByDate(
    #             df=df_aproximaciones,
    #             platform=platform,
    #             station=station,
    #         )

    #         # Nos aseguramos de que está en el orden adecuado
    #         if not df_split:
    #             continue
    #         df_split = sorted(df_split, key=lambda x: x[0][0])
    #         historicos.extend(df_split)

    #     datos = [
    #         self.processAproximacionLlegada(split, t_cols, mov_sorter)
    #         for split in historicos
    #         if len(split)
    #     ]

    #     df = pd.DataFrame(datos)

    #     use_cols = [
    #         "Tren",
    #         "Producto",
    #         "Nombre",
    #         "Código",
    #         "Vía",
    #         "VíaPlanificada",
    #         "Movimiento",
    #         "Mov_seq",
    #         "Aproximación",
    #         "Llegada",
    #         "Anticipación",
    #         "Inicio",
    #         "Fin",
    #         "TiempoTotal",
    #     ]
    #     if df is None or df.empty:
    #         return pd.DataFrame(columns=use_cols)

    #     # Agrupar trenes por número técnico y fecha.
    #     filt_df = df.copy()
    #     filt_df = filt_df.sort_values(by=["Tren", "Inicio"]).reset_index(drop=True)
    #     filt_df["tdiff"] = filt_df["Inicio"].apply(pd.to_datetime, dayfirst=True).diff()
    #     t_cols = filt_df.columns
    #     df_split = np.split(
    #         filt_df.values,
    #         np.where(
    #             (~(filt_df["tdiff"] < timedelta(hours=6)))
    #             | (~filt_df["Tren"].eq(filt_df["Tren"].shift()))
    #         )[0][1:],
    #     )
    #     wrong = []
    #     [wrong.extend(el) for el in df_split if len(el) > 1]
    #     df_wrong = pd.DataFrame(wrong, columns=t_cols)

    #     return df_wrong[use_cols]

    ####################################################################
    # EXTRA
    ####################################################################

    def saveInfo(
        self,
        df: pd.DataFrame,
        save_dir: Path,
        fname: str,
        nombre_estacion: str,
        days: str,
    ):
        save_cols = (
            ["Código", "Estación", "Vía", "TipoVía"]
            + ["T1", "T2", "T_seq"]
            + ["ProductoT1", "ProductoT2", "Producto"]
            + ["Movimiento", "Mov_seq", "full_seq", "cambio_vía"]
            + ["ALTA", "APROXIMACIÓN", "MANIOBRALLEGADA", "LLEGADA", "SALIDA"]
            + ["MANIOBRASALIDA", "FIN", "BAJA", "MANIOBRA", "Anticipación"]
            + ["InicioOcupación", "FinOcupación", "Ocupación (segundos)", "Ocupación"]
            + [
                "VíaPlanificada",
                "LlegadaPlanificada",
                "SalidaPlanificada",
                "OcupaciónPlanificada (segundos)",
                "OcupaciónPlanificada",
            ]
            + ["RotaciónValidada", "Fallo"]
        )
        df["Fallo"] = df["Fallo"].apply(lambda x: True if x and pd.notna(x) else False)

        # Buscamos fechas límites para rangos
        min_date = pd.to_datetime(
            (pd.to_datetime(df["FinOcupación"]).min() - timedelta(hours=0.5)).strftime(
                "%Y-%m-%d %H"
            )
        ) - timedelta(hours=1)
        max_date = pd.to_datetime(
            (pd.to_datetime(df["FinOcupación"]).max() + timedelta(hours=0.5)).strftime(
                "%Y-%m-%d %H"
            )
        ) + timedelta(hours=1)

        # Obtenemos la saturación de las vías
        saturation_1h = self.getSaturation(
            df, min_date, max_date, hour_period=1, margen=0
        )
        saturation_3h = self.getSaturation(
            df, min_date, max_date, hour_period=3, margen=0
        )

        # Generamos visualización
        margen = 10
        t_min = 5
        
        df_free = (
            self.getTramosLibres(df, margen=margen, t_min=t_min)
            .sort_values(by=["InicioLibre"])
            .reset_index(drop=True)
        )
        df_free.rename(columns={"Elemento": "Vía"}, inplace=True)
        if df_free is not None:
            print("df_free no es nulo")
            saturation_libre_1h = self.getSaturation(
                df_free.rename(
                    columns={
                        "InicioLibre": "InicioOcupación",
                        "FinLibre": "FinOcupación",
                    }
                ),
                min_date,
                max_date,
                hour_period=1,
                margen=0,
                modo="libre",
            )
            saturation_libre_3h = self.getSaturation(
                df_free.rename(
                    columns={
                        "InicioLibre": "InicioOcupación",
                        "FinLibre": "FinOcupación",
                    }
                ),
                min_date,
                max_date,
                hour_period=3,
                margen=0,
                modo="libre",
            )
        else:
            print("df_free es nulo")
            saturation_libre_1h = None
            saturation_libre_3h = None
        # print(df.head(5))
        # print(df_free.head(5))

        fig_occ = visualizacionOcupacionVia(
            df,
            title=f"Ocupación de vías en <b>{nombre_estacion}</b>",
            df_free=df_free,
            show_fail=True,
            show_plan=True,
        )
        fig_sat_1h = visualizacionSaturacionVia(
            saturation_1h,
            saturation_libre_1h,
            title=f"Saturación de vías en <b>{nombre_estacion}</b> (periodos 1h)",
        )
        fig_sat_3h = visualizacionSaturacionVia(
            saturation_3h,
            saturation_libre_3h,
            title=f"Saturación de vías en <b>{nombre_estacion}</b> (periodos 3h)",
        )
        fig_ant = visualizeAnticipacionVia(
            df, title=f"Anticipación de vías en <b>{nombre_estacion}</b>"
        )

        # Guardamos los datos
        save_loc = Path(save_dir).joinpath(fname)
        save_loc.mkdir(parents=True, exist_ok=True)

        if not df.empty:
            guardarExcel(
                df[[c for c in save_cols if c in df.columns]],
                save_loc.joinpath(f"historico {days}.xlsx"),
                append_sheet=False,
            )
        if fig_occ:
            fig_occ.write_html(save_loc.joinpath(f"ocupación {days}.html"))
            ## fig_occ.write_image(save_loc.joinpath(f"ocupación {days}.png"))
        # if fig_sat_1h:
        #     fig_sat_1h.write_html(save_loc.joinpath(f"saturación_1h {days}.html"))
        # if fig_sat_3h:
        #     fig_sat_3h.write_html(save_loc.joinpath(f"saturación_3h {days}.html"))
        # if fig_ant:
        #     fig_ant.write_html(save_loc.joinpath(f"anticipación {days}.html"))
