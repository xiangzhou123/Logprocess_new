import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)

import argparse
import warnings
from datetime import timedelta
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd
import regex
import yaml
from plotly.express import colors

from src.api import cargarHistorico, getHistoricoMOW
from src.processor import XPECProcessor
from src.utils import (
    calcularVelocidades,
    cargarControlPoints,
    isEmpty,
    isValidCode,
    parallelizeFunction,
    parseDate,
)
from src.visualizacion.recorrido import crearTrazas, mostrarMarchas, mostrarVelocidades

color24 = colors.qualitative.Dark24
color12 = colors.qualitative.Set3
DAY = pd.to_datetime(0)

# Tipos de tren que queremos
train_types = {
    "Approach": "APROXIMACIÓN",
    "Arrival": "LLEGADA",
    "Departure": "SALIDA",
    "Elimination": "BAJA",
    "End": "FIN",
    # "Entry": "ENTRY",
    # "Exit": "EXIT",
    # "Maneuver": "MANIOBRA",
    "Platform": "ALTA",
    # "PlatformForecast": "PREVISIÓN",  # "PREDICCIÓN",
    # "Stopped": "STOP",
    # "TrackingLost": "LOST_TRACK",
}


# Orden lógico de movimientos
mov_sorter = {
    v: k
    for k, v in enumerate(
        [
            "PREVISIÓN",
            "APROXIMACIÓN",
            "MANIOBRALLEGADA",
            "EXIT",
            "LLEGADA",
            "FIN",
            "BAJA",
            "ALTA",
            "SALIDA",
            "MANIOBRASALIDA",
            "MANIOBRA",
        ]
    )
}


def loadConfig(config_file: Path = Path("config.yaml")):
    with config_file.open("r", encoding="utf8") as f:
        config = yaml.safe_load(f)
    return config


def loadArgs():
    # python recorrido_linea.py -d output/test/madrid_pamplona -s 2025-04-21 -e 2025-04-21
    parser = argparse.ArgumentParser(
        description="Script para procesar datos de estaciones y trenes."
    )

    parser.add_argument(
        "-d",
        "--dir_save",
        help="Directorio donde se guardarán los resultados.",
    )
    parser.add_argument(
        "-s",
        "--inicio",
        type=parseDate,
        help="Fecha de inicio en uno de los siguientes formatos: Y-m-d H:M:S, Y-m-d, YmdHMS, Ymd.",
    )
    parser.add_argument(
        "-e",
        "--fin",
        type=parseDate,
        help="Fecha de fin en uno de los siguientes formatos: Y-m-d H:M:S, Y-m-d, YmdHMS, Ymd.",
    )
    # parser.add_argument(
    #     "-t",
    #     "--trenes",
    #     nargs="+",
    #     default=[],
    #     help="Lista de trenes.",
    # )
    # parser.add_argument(
    #     "-c",
    #     "--config",
    #     help="Ruta al archivo config.yaml para cargar los parámetros.",
    # )

    args = parser.parse_args()
    return args


def cargarXPEC(
    ntecnicos: list[str],
    start_date: str,
    end_date: str,
    dir_logs: Union[str, Path] = Path(r"C:\Users\xiangzhou.zhang\Documents\Data\xPEC"),
):
    """
    Carga los datos de XPEC en una fecha
    """
    fechas_inicio = pd.date_range(start_date, end_date, freq="D").date
    dir_logs = Path(dir_logs)
    fnames = []
    for fname in dir_logs.rglob("*.*"):
        if not fname.is_file() and not fname.suffix == ".xml":
            continue
        fdate = pd.to_datetime(regex.search(r"(?<=_)\d+(?=\.)", fname.name).group())
        if not (
            (fdate >= pd.to_datetime("2025-02-17"))
            & (fdate <= pd.to_datetime("2025-02-18"))
        ):
            continue
        fnames.append(fname)

    xpec_processor = XPECProcessor()
    service_info = parallelizeFunction(
        xpec_processor.loadLogFile,
        data=list(set(fnames)),
        leave=True,
        desc=f"Cargando XPECs...",
    )

    xpec = xpec_processor.getLogsInfo(service_info, ntecnicos, fechas_inicio)
    xpec["mov_ord"] = xpec["Movimiento"].apply(mov_sorter.get)
    return xpec


def filtrarConXPEC(
    df: pd.DataFrame,
    map_codigo_estacion: dict[str, str],
    map_codigo_pos: dict,
    map_codigo_pos_inv: dict,
    map_codigo_loc: dict,
    map_codigo_loc_inv: dict,
    start_date: str,
    end_date: str,
):
    """
    Añade las posiciones relativas del recorrido usando las posiciones/distancias del XPEC
    """
    use_cols = [
        "FechaOrigen",
        "NTécnico",
        "Fecha",
        "Código",
        "Secuencia",
        "Movimiento",
        # "FuenteVía",
        # "Vía",
        # "TipoVía",
        # "Estado",
        "mov_ord",
    ]
    fechas_inicio = pd.date_range(start_date, end_date, freq="D").date
    df_filt = (
        df.loc[df["Secuencia"] > 0, use_cols]
        .drop_duplicates(
            subset=["FechaOrigen", "NTécnico", "Código", "Secuencia", "Movimiento"],
            keep="first",
        )
        .sort_values(by=["FechaOrigen", "NTécnico", "Secuencia", "Fecha", "mov_ord"])
        # .drop(["mov_ord"], axis=1)
        .copy()
    )
    df_filt["Nombre"] = df_filt["Código"].apply(map_codigo_estacion.get)

    # Cálculos para trenes individuales
    df_show = []
    for d, nt in df_filt[["FechaOrigen", "NTécnico"]].drop_duplicates().values:
        df_t = (
            df_filt[(df_filt["FechaOrigen"] == d) & (df_filt["NTécnico"] == nt)]
            .dropna()
            .copy()
        )
        if df_t.empty:
            continue
        # Obtenemos la posición para mostrar (y comprobar que los puntos de paso son correctos)
        df_t["_pos"] = df_t["Código"].apply(map_codigo_pos.get)
        if not df_t["_pos"].dropna().empty:
            # Mantener posición relativa en gráfica y calcular distancia real
            # Volteamos distancia recorrida para cuadrar origen
            if df_t["_pos"].iloc[0] > df_t["_pos"].iloc[-1]:
                df_t["DistanciaTotal (km)"] = (
                    df_t["Código"].apply(map_codigo_loc_inv.get).round(2)
                )
                df_t["pos"] = df_t["Código"].apply(map_codigo_pos_inv.get)
                df_t["_invert"] = True
            else:
                df_t["DistanciaTotal (km)"] = (
                    df_t["Código"].apply(map_codigo_loc.get).round(2)
                )
                df_t["pos"] = df_t["Código"].apply(map_codigo_pos.get)
                df_t["_invert"] = False
            df_t = pd.merge(
                df_t,
                calcularVelocidades(
                    df_t[df_t["Movimiento"].isin(["LLEGADA", "SALIDA"])]
                ),
                how="left",
                on=df_t.columns.tolist(),
            )
            df_show.append(df_t)
    df_show = pd.concat(df_show)
    df_show = df_show[df_show["FechaOrigen"].isin(fechas_inicio)]
    return df_show


def compute_std(data):
    # Filter out None values
    filtered_data = [x for x in data if not isEmpty(x)]

    # Check if filtered_data is empty
    if not filtered_data:
        return None

    # Compute standard deviation
    std_dev = np.std(filtered_data)
    return std_dev


def procesarTraza(
    prod: str,
    df: pd.DataFrame,
    inv: bool,
    map_codigo_pos: dict,
    map_codigo_pos_inv: dict,
    map_codigo_loc: dict,
    map_codigo_loc_inv: dict,
):
    df_map = dict()
    df_medio = []
    for d, nt in df[["FechaOrigen", "NTécnico"]].drop_duplicates().values:
        use_df = df.loc[(df["FechaOrigen"] == d) & (df["NTécnico"] == nt)]
        if use_df.empty:
            continue
        # # Usamos los trenes con recorrido completo, ignorando los parciales que meten ruido
        # sub_est = (
        #     subset_xpec.loc[subset_xpec["NTécnico"] == nt]
        #     .drop_duplicates(subset=["NTécnico", "Código", "Secuencia"])["Código"]
        #     .tolist()
        # )
        # if sub_est and not all(
        #     [e in use_df["Código"].values for e in [sub_est[0], sub_est[-1]]]
        # ):
        #     continue
        use_df = use_df.copy()
        # Normalizamos la fecha a 0
        use_df["FechaNorm"] = pd.to_datetime(
            DAY
            + (
                use_df["Fecha"]
                - use_df[use_df["Movimiento"].isin(["LLEGADA", "SALIDA"])][
                    "Fecha"
                ].min()
            )
        )
        use_df["timestamp"] = use_df["FechaNorm"].apply(
            lambda x: x.timestamp() if not isEmpty(x) else x
        )
        use_df["FechaOrigen"] = pd.to_datetime(use_df["FechaOrigen"]).dt.date
        use_df["Hora"] = use_df["FechaNorm"].dt.time
        df_map[(inv, prod, d, nt)] = use_df
        df_medio.append(use_df)
    df_medio = pd.concat(df_medio)
    df_medio["prod"] = prod
    # Agrupamos los mismos movimientos para calcular la media
    df_medio = (
        df_medio.groupby(
            by=[
                # "FechaOrigen",
                "_invert",
                "Código",
                "Nombre",
                "Movimiento",
                "mov_ord",
                "_pos",
                "prod",
            ],
            dropna=False,
        )
        .agg(
            {
                "timestamp": list,
                # "VelocidadMedia (km/h)": list,
            }
        )
        .reset_index()
        .sort_values(by=["prod", "_invert", "_pos", "mov_ord"])
    )
    df_medio = df_medio[df_medio["Movimiento"].isin(["LLEGADA", "SALIDA"])]
    # Calculamedia y desviación estandard
    df_medio["mean"] = (
        df_medio["timestamp"]
        .apply(np.mean)
        .apply(lambda x: int(x) if not isEmpty(x) else x)
    )
    df_medio["std"] = (
        df_medio["timestamp"]
        .apply(compute_std)
        .apply(lambda x: int(x) if not isEmpty(x) else x)
    )
    # df_medio["FechaOrigen"] = pd.to_datetime(df_medio["FechaOrigen"]).dt.date
    if inv:
        df_medio["DistanciaTotal (km)"] = (
            df_medio["Código"].apply(map_codigo_loc_inv.get).round(2)
        )
        df_medio["pos"] = df_medio["Código"].apply(map_codigo_pos_inv.get)
    else:
        df_medio["DistanciaTotal (km)"] = (
            df_medio["Código"].apply(map_codigo_loc.get).round(2)
        )
        df_medio["pos"] = df_medio["Código"].apply(map_codigo_pos.get)
    df_medio["FechaNorm"] = pd.to_datetime(df_medio["mean"], unit="s")
    df_medio["Fecha"] = df_medio["FechaNorm"]
    df_medio = df_medio.sort_values(by=["pos", "mov_ord"], ascending=[True, True])
    df_medio = calcularVelocidades(df_medio)
    # df_medio["VelocidadMedia (km/h)"] = (
    #     df_medio["VelocidadMedia (km/h)"]
    #     .apply(np.mean)
    #     .apply(lambda x: np.round(x, 2) if pd.notna(x) else x)
    # )
    df_map[(inv, "Media", None, prod)] = df_medio
    return df_map


def main():
    #############################################
    # Cargamos la configuración
    args = loadArgs()
    dir_save = Path(args.dir_save)
    if not dir_save:
        print("No se ha seleccionado directorio para guardar.")
        exit(0)
    dir_save.mkdir(parents=True, exist_ok=True)
    start_date = args.inicio
    end_date = args.fin
    if not start_date or not end_date:
        print("No se han seleccionado fecha de inicio o fin.")
        exit(0)
    start_date = parseDate(start_date)
    end_date = parseDate(end_date)
    if end_date <= start_date:
        end_date = start_date + timedelta(days=1)

    # TODO: cargar los recorridos a partir de mensaje XSIV/histórico MOW/planif/XPEC
    # Por ahora se selecciona a mano y se usa XPEC
    ntecnicos = ["00701", "00702", "00705", "00706"]  # Madrid - Logroño
    ntecnicos = (
        ["00601", "00605", "00609", "00617", "00801", "00807"]
        + ["00602", "00606", "00610", "00612", "00614"]
        + ["00802", "00808"]
    )  # Madrid - Pamplona
    ntecnicos = (
        ["19502", "19503", "19504", "19505", "19506", "19507", "19508"]
        + ["19509", "19510", "19511", "19513", "19514", "19515", "19517"]
        + ["19520", "19521", "19523", "19524", "19525", "19526", "19527"]
        + ["19528", "19530", "19533", "19534", "19537", "19538", "19540"]
        + ["19541", "19544", "19545", "19546", "19550", "19552", "19553"]
        + ["19554", "19557", "19558", "19559", "19560", "19562", "19563"]
        + ["19564", "19565", "19566", "19567", "19569", "19570", "19573"]
        + ["19574", "19575", "19576", "19577", "19578", "19580", "19582"]
        + ["19583", "19585", "19586", "19587", "19588", "19589", "19590"]
        + ["19592", "19593", "19594", "19595", "19596", "19597", "19598"]
        + ["19599", "19600", "19601", "19602", "19603", "19604", "19605"]
        + ["19606", "19607", "19609", "19610", "19611", "19612", "19613"]
        + ["19614", "19615", "19616", "19617", "19619", "19621", "19622"]
        + ["19626", "19627", "19628", "19629", "19630", "19631", "19633"]
        + ["19634", "19637", "19638", "19639", "19641", "19642", "19643"]
        + ["19644", "19647", "19648", "19651", "19652", "19653", "19655"]
        + ["19656", "19658", "19659", "19661", "19662", "19664", "19665"]
        + ["19667", "19668", "19669", "19670", "19673", "19674", "19675"]
        + ["19677", "19678", "19680", "19681", "19684", "19685", "19686"]
        + ["19687", "19690", "19691", "19692", "19693", "19695", "19696"]
        + ["19697", "19698", "19700", "19701", "19704", "19705", "19706"]
        + ["19710", "19711", "19713", "19714", "19715", "19716", "19718"]
        + ["19719", "19720", "19721", "19722", "19723", "19725", "19726"]
        + ["19727", "19729", "19730", "19732", "19733", "19736", "19737"]
        + ["19739", "19740", "19741", "19743", "19744", "19745", "19747"]
        + ["19748", "19751", "19752", "19753", "19754", "19755", "19756"]
        + ["19757", "19759", "19760", "19762", "19766", "19772", "19774"]
        + ["20700", "20702", "20703", "20706", "20707", "20708", "20709"]
        + ["20710", "20713", "20714", "20715", "20716", "20719", "20720"]
        + ["20722", "20723", "20725", "20726", "20728", "20729", "20731"]
        + ["20732", "20733", "20734", "20735", "20736", "20737", "20741"]
        + ["20742", "20743", "20746", "20747", "20750", "20753", "20754"]
        + ["20757", "20758", "20759", "20764", "20765", "20766", "20768"]
        + ["20769", "20770", "20772", "20773", "20774", "20775", "20776"]
        + ["20777", "20778", "20779", "20780", "20781", "20782", "20783"]
        + ["20784", "20785", "20786", "20787", "20788", "20789", "20792"]
        + ["20793", "20795", "20799", "20800", "20803", "20804", "20805"]
        + ["20807", "20808", "20810", "20815", "20816", "20819", "20820"]
        + ["20821", "20822", "20823", "20826", "20827", "20830", "20831"]
        + ["20834", "20835", "20836", "20837", "20840", "20841", "20844"]
        + ["20845", "20847", "20848", "20849", "20852", "20853", "20854"]
        + ["20856", "20857", "20859", "20860", "20862", "20863", "20866"]
        + ["20869", "20870", "20871", "20873", "20877", "20880", "20883"]
        + ["20884", "20899", "20888", "20893", "20894", "20895", "20897"]
        + ["20898", "35004", "20900", "20901", "20904", "20905", "20908"]
        + ["35003", "35008", "35738"]
    )

    print("\nConfiguración:")
    print("#" * 100)
    print(f"Directorio resultados:  {dir_save}")
    print(f"Para los siguientes trenes: [{', '.join(ntecnicos)}]")
    print(f"Entre las siguientes fechas: {start_date} - {end_date}")
    print("#" * 100, "\n")

    #############################################

    # Cargamos los puntos de control
    control_points = cargarControlPoints()
    control_points = control_points[control_points["code"].apply(isValidCode)]
    map_codigo_estacion = dict(control_points[["code", "shortDesc"]].values)

    # Cargar info del XPEC
    dir_logs = Path(r"C:\Users\jose.espinosa\Documents\Data\xPEC")
    xpec = cargarXPEC(ntecnicos, start_date, end_date, dir_logs)
    # Asignar posiciones
    # Distancia recorrida
    map_codigo_loc = dict(
        xpec[["Código", "DistanciaTotal (km)"]].drop_duplicates(subset="Código").values
    )
    max_distance = max(map_codigo_loc.values())
    map_codigo_loc_inv = {
        k: np.round(max_distance - v, 2) for k, v in map_codigo_loc.items()
    }

    # Orden en las representaciones
    map_codigo_pos = dict(
        xpec["Código"]
        .drop_duplicates()
        .reset_index(drop=True)
        .reset_index()[["Código", "index"]]
        .values
    )
    max_pos = max(map_codigo_pos.values())
    map_codigo_pos_inv = {k: max_pos - v for k, v in map_codigo_pos.items()}

    map_pos_estaciones = dict(
        (
            (
                xpec[["Nombre", "Código"]].apply(
                    lambda x: x["Nombre"] if x["Nombre"] else x["Código"], axis=1
                )
            )
            .drop_duplicates()
            .reset_index(drop=True)
            .reset_index()[["index", 0]]
            # .rename(columns={0: "Nombre"})
            .values
        )
    )
    map_pos_estaciones_inv = {max_pos - k: v for k, v in map_pos_estaciones.items()}
    estaciones = list(map_codigo_pos.keys())

    # Volteamos distancia recorrida para cuadrar origen
    xpec["_pos"] = xpec["Código"].apply(map_codigo_pos.get)
    nt_xpec = []
    for nt in xpec["NTécnico"].unique():
        aux_xpec = xpec[xpec["NTécnico"] == nt].copy()
        if aux_xpec["_pos"].iloc[0] > aux_xpec["_pos"].iloc[-1]:
            aux_xpec["_invert"] = True
        else:
            aux_xpec["_invert"] = False
        nt_xpec.append(aux_xpec)
    xpec = pd.concat(nt_xpec)

    # Cargar info de MOW
    historico = cargarHistorico(start_date, end_date, estaciones, ntecnicos)
    df_show_mow = filtrarConXPEC(
        historico,
        map_codigo_estacion,
        map_codigo_pos,
        map_codigo_pos_inv,
        map_codigo_loc,
        map_codigo_loc_inv,
        start_date,
        end_date,
    )

    # Procesar trazas
    trace_names = {
        "XPEC": xpec,
        # "Planif",
        # "XSIV": df_show_xsiv,
        # "Sitra": df_show_sitra,
        "MOW": df_show_mow,
    }
    df_map = dict()
    for prod, df in trace_names.items():
        for inv in sorted([True, False]):
            df_rep = df[df["_invert"] == inv]
            if df_rep.empty:
                continue
            df_map.update(
                procesarTraza(
                    prod,
                    df_rep,
                    inv,
                    map_codigo_pos,
                    map_codigo_pos_inv,
                    map_codigo_loc,
                    map_codigo_loc_inv,
                )
            )

    # Guardar gráficos
    traces = []
    vis_all = []
    map_vis = dict()
    for invertir in sorted([True, False]):
        use_df_map = {
            (prod, d, nt): use_df
            for (inv, prod, d, nt), use_df in sorted(df_map.items())
            if inv == invertir
        }
        if invertir:
            map_tick_text = map_pos_estaciones_inv
            i = "_invertido"
        else:
            map_tick_text = map_pos_estaciones
            i = ""

        # Recorridos
        # fig = mostrarMarchas(
        #     use_df_map,
        #     map_tick_text=map_pos_estaciones,
        #     # usar_media=True,
        #     usar_media=False,
        #     map_codigo_pos=map_codigo_pos,
        #     title=f"Marcha Media por Trayecto ({start_date} - {end_date})",
        # )
        t, va, mv = crearTrazas(
            use_df_map,
            usar_media=False,
            map_codigo_pos=map_codigo_pos,
        )
        traces.extend(t)
        vis_all.extend(va)
        for k, v in mv.items():
            if k in map_vis:
                map_vis[k].extend(v)
            else:
                map_vis[k] = v
        # map_vis.update(mv)
    fig = mostrarMarchas(
        usar_media=False,
        traces=traces,
        vis_all=vis_all,
        map_vis=map_vis,
        map_tick_text=map_pos_estaciones,
        title=f"Marcha Media por Trayecto ({start_date} - {end_date})",
    )
    fig.write_html(dir_save.joinpath(f"recorrido_medio{i}.html"))

    # # Velocidades
    # fig = mostrarVelocidades(
    #     use_df_map,
    #     map_tick_text,
    #     title=f"Marcha Media por Trayecto ({start_date} - {end_date})",
    # )
    # fig.write_html(dir_save.joinpath(f"velocidad_media{i}.html"))


if __name__ == "__main__":
    main()
