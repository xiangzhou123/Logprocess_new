from pathlib import Path

import pandas as pd
import regex
from tqdm.auto import tqdm

from src.utils import (
    getEstacionamientos,
    loadEstaciones,
    localizeFecha,
    map_mie_cata,
    rellenarId,
)


class MIEProcessor:
    df_estaciones = loadEstaciones()
    df_catalogs = df_estaciones.loc[
        df_estaciones["Mnemónico"] == df_estaciones["Mnemónico_comercial"],
        ["Catálogo", "Mnemónico", "Código", "Nombre"],
    ]

    def readLogFile(
        self,
        fname: Path,
        load: list = ["elemento", "tren"],
        mtype: list[str] = [
            "ocupa",
            "libera",
            "alta",
            "baja",
            "proyectado",
        ],
    ):
        """
        Lee un fichero log mie y devuelve los logs
        """
        with fname.open("r", encoding="utf8") as f:
            lines = f.readlines()
        lines = [
            el
            for el in lines
            if el
            and ("DMS1" in el)
            and ("element" in load and "element" in el)
            or ("tren" in load and "tren" in el and any(t in el for t in mtype))
        ]
        logs = [
            (
                regex.search(r"/opt.+?\.log", l).group() if r"/opt" in l else None,
                regex.search(rf"(?<=DMS1.+)({'|'.join(load)}).+(?=\"?\n)", l).group(),
            )
            for l in lines
        ]
        return logs

    def getMIEMsgPart(self, prev: str, after: str, msg: str):
        if not msg:
            return None
        if not prev and not after:
            return None
        result = regex.search(
            rf"(?<={regex.escape(prev)}\s+).+?(?={regex.escape(after)}\s+)", msg
        )
        if result:
            return result.group(0).strip()
        return None

    def loadInfoLog(self, log: tuple[str, str]):
        """
        Carga la información de un log en función de si es un elemento o un tren
        """
        mie: str = log[0]
        log: str = log[1]
        info = {"MIE": mie}
        ls = log.split()
        if regex.search(r"^elemento", log):
            _, mnem, name, _, tipo, _, e1, e2, e3, e4, _, _, _, _, _, fecha, _, _ = ls
            info["Tipo"] = tipo
            info["Estado"] = ".".join(e.strip(".") for e in [e1, e2, e3, e4])
        elif regex.search(r"^tren", log):
            _, tren, accion, _, mnem, name, _, sent, _, _, _, _, _, fecha, _, _ = ls
            info["NTécnico"] = rellenarId(tren)
            info["Acción"] = accion
            info["Sentido"] = sent
        info["Mnemónico"] = mnem
        info["Elemento"] = name
        # info["Fecha"] = time2localtime(fecha, unit="ms")
        info["Fecha"] = fecha
        return info

    def loadLogFile(
        self,
        fname: Path,
        load: list = ["elemento", "tren"],
        mtype: list[str] = [
            "ocupa",
            "libera",
            "alta",
            "baja",
            "proyectado",
        ],
    ):
        """
        Devuelve un fichero log XSIV en forma Dataframe
        """
        logs = self.readLogFile(fname, load, mtype)

        # Procesamos cada linea
        log_data = []
        with tqdm(
            total=len(logs),
            position=1,
            leave=False,
            desc="Filtrando información...",
        ) as pbar:
            for i, l in enumerate(logs, 1):
                log_data.append(self.loadInfoLog(l))
                if not i % 100000:
                    pbar.update(100000)
            pbar.update(len(logs) - pbar.n)

        data = []
        if not len(log_data):
            return
        if isinstance(log_data[0], list):
            for l in log_data:
                data.extend(l)
        else:
            data = log_data
        if not data:
            return

        # Limpiamos columnas
        df_logs = pd.DataFrame(data).drop_duplicates().reset_index(drop=True)

        return df_logs.drop_duplicates()

    def getLogsInfo(
        self,
        log_data: list[pd.DataFrame],
        estaciones: list[str] = [],
        format_fechas: bool = True,
    ):
        rename_cols = {
            "MIE": "MIE",
            "NTécnico": "NTécnico",
            "Mnemónico": "Mnemónico",
            "Elemento": "Vía",
            "Sentido": "Sentido",
            "Acción": "Movimiento",
            "Fecha": "Fecha",
            "Tipo": "Tipo",
        }
        map_action = {
            "ocupa": "LLEGADA",
            "libera": "SALIDA",
            "baja": "BAJA",
            "alta": "ALTA",
            "proyectado": "PREVISIÓN",
        }

        df_logs = pd.concat([el for el in log_data if not el.empty])
        if df_logs.empty:
            return

        # Limpiamos columnas
        df_logs = df_logs.rename(columns=rename_cols)
        cols = [
            c
            for c in [
                "Fecha",
                "MIE",
                "Código",
                "Nombre",
                "Mnemónico",
                "Vía",
                "Tipo",
                "NTécnico",
                "Movimiento",
                "Sentido",
            ]
            if c in df_logs.columns
        ]
        df_logs = df_logs[cols]

        # Formateamos fechas
        if format_fechas:
            df_logs[["Fecha"]] = localizeFecha(df_logs, ["Fecha"], unit="ms")

        # Relacionamos MIE, catálogo, mnemónico y estación
        df_logs["Catálogo"] = df_logs["MIE"].str.lower().apply(map_mie_cata.get)
        df_logs = pd.merge(
            df_logs,
            self.df_catalogs,
            on=["Catálogo", "Mnemónico"],
            how="left",
        )

        # Filtramos info adicional
        if "Movimiento" in cols:
            df_logs["Movimiento"] = df_logs["Movimiento"].apply(map_action.get)
        # if "NTécnico" in cols:
        #     df_logs = df_logs.dropna(subset=["NTécnico"])
        if estaciones:
            df_logs = df_logs[df_logs["Código"].isin(estaciones)]
        df_logs = df_logs.drop_duplicates().reset_index(drop=True)

        # Incluimos tipo de vía de estacionamiento
        estacionamientos = getEstacionamientos(estaciones)
        df_logs = (
            pd.merge(
                df_logs,
                estacionamientos[["Código", "TipoVía", "VíaTécnica", "Vía"]],
                how="left",
                left_on=["Código", "Vía"],
                right_on=["Código", "VíaTécnica"],
            )
            .drop(["Vía_y"], axis=1)
            .rename(columns={"Vía_x": "Elemento"})
        )

        return df_logs
