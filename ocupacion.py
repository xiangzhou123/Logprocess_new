# pd.set_option("future.no_silent_downcasting", True)
import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)

import argparse
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import regex
import yaml
from tqdm.auto import tqdm

from src.api import cargarHistorico, getHistoricoMOW
from src.processor import LogProcessor
from src.utils import isValidCode, parallelizeFunction, parseDate, rellenarId

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
            "EXIT",
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
    # python ocupacion.py -p días -d output/test/ -s 2025-04-21 -e 2025-04-21 -i 0 -a 60000
    parser = argparse.ArgumentParser(
        description="Script para procesar datos de estaciones y trenes."
    )

    parser.add_argument(
        "-p",
        "--proceso",
        nargs="+",
        choices=["días", "semanas"],
        default=["días"],
        help="Tipo de proceso: 'días' o 'semanas'.",
    )
    parser.add_argument(
        "-i",
        "--inicio_semana",
        type=int,
        default=3,
        help="Día de inicio de la semana (0=Lunes, 1=Martes, ..., 6=Domingo). Obligatorio si 'semanas'.",
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
    parser.add_argument(
        "-a",
        "--estaciones",
        nargs="+",
        default=[],
        help="Lista de estaciones.",
    )
    parser.add_argument(
        "-t",
        "--trenes",
        nargs="+",
        default=[],
        help="Lista de trenes.",
    )
    parser.add_argument(
        "-c",
        "--config",
        help="Ruta al archivo config.yaml para cargar los parámetros.",
    )

    args = parser.parse_args()
    return args


def processOcupacion(
    df_logs: pd.DataFrame,
    save_info: bool,
    save_dir: Path,
    days: str,
    estaciones=list[str],
    list_rotaciones: list = [],
    desc: str = "Procesando estaciones",
):
    log_processor = LogProcessor()
    used_dfs = parallelizeFunction(
        log_processor.processStation,
        estaciones,
        df=df_logs,
        mov_sorter=mov_sorter,
        save_info=save_info,
        save_dir=save_dir,
        days=days,
        list_rotaciones=list_rotaciones,
        leave=True,
        desc=f"{desc}: {days}",
    )
    # with tqdm(total=len(estaciones), position=0, leave=False) as pbar:
    # for e in estaciones:
    #     # pbar.set_description(f"Procesando '{e}'")
    #     print(f"Procesando '{e}'")
    #     used_dfs = log_processor.processStation(
    #         e,
    #         df=df_logs,
    #         mov_sorter=mov_sorter,
    #         save_info=save_info,
    #         save_dir=save_dir,
    #         days=days,
    #         list_rotaciones=list_rotaciones,
    #     )
    #         # pbar.update()
    return used_dfs


def processDias(
    save_info: bool,
    save_dir: Path,
    start_date: str,
    end_date: str,
    estaciones=list[str],
    trenes=list[str],
    list_rotaciones: list = [],
):
    # Cargamos histórico
    df_logs = cargarHistorico(start_date, end_date, estaciones, trenes)

    # Procesamos y guardamos
    d1 = regex.sub(r"[-:\s]", "", f"{pd.to_datetime(start_date).date()}")
    d2 = regex.sub(r"[-:\s]", "", f"{pd.to_datetime(end_date).date()}")
    used_dfs = processOcupacion(
        df_logs,
        save_info,
        save_dir,
        f"{d1} - {d2}",
        estaciones,
        list_rotaciones,
    )
    return used_dfs


def processSemanas(
    save_info: bool,
    save_dir: Path,
    start_date: str,
    end_date: str,
    start_of_week: int = 3,
    estaciones=list[str],
    trenes=list[str],
    list_rotaciones: list = [],
):
    # Usamos rangos de fechas de semanas completas
    d_range = pd.date_range(start_date, end_date, freq="D", inclusive="both")
    d_days = np.array([d.day_of_week for d in d_range])
    week_limits = np.where(d_days == int(start_of_week))[0]
    used_dfs = []

    # Procesamos
    for i in range(len(week_limits) - 1):
        s_date = d_range[week_limits[i]]
        e_date = d_range[week_limits[i + 1]]
        used_dfs.extend(
            processDias(
                save_info,
                save_dir,
                s_date,
                e_date,
                estaciones,
                trenes,
                list_rotaciones,
            )
        )
    return used_dfs


def main():
    #############################################
    # Cargamos la configuración
    args = loadArgs()
    if args.config:
        config = loadConfig(args.config)
    else:
        config = dict()
    process = config.get("proceso", args.proceso)
    if not process:
        print("No se han seleccionado procesos.")
        exit(0)
    start_of_week = config.get("inicio_semana", args.inicio_semana)
    if "semanas" in process and start_of_week is None:
        print("No se ha seleccionado inicio de semana.")
        exit(0)
    dir_save = config.get("dir_save", args.dir_save)
    if not dir_save:
        print("No se ha seleccionado directorio para guardar.")
        exit(0)
    start_date = config.get("inicio", args.inicio)
    end_date = config.get("fin", args.fin)
    if not start_date or not end_date:
        print("No se han seleccionado fecha de inicio o fin.")
        exit(0)
    start_date = parseDate(start_date)
    end_date = parseDate(end_date)
    estaciones = config.get("estaciones", args.estaciones)
    trenes = config.get("trenes", args.trenes)
    if not estaciones and not trenes:
        print("Debe haber al menos una estación o un tren.")
        exit(0)

    print("\nConfiguración:")
    print("#" * 100)
    print(f"Directorio resultados:  {dir_save}")
    if process:
        print(f"Procesar: [{', '.join(process)}] entre {start_date} y {end_date}")
    if process and "semanas" in process:
        print(f"Inicio de semana:       {start_of_week}")
    if estaciones:
        print(f"En las siguientes estaciones: [{', '.join(estaciones)}]")
    if trenes:
        print(f"Para los siguientes trenes: [{', '.join(trenes)}]")
    print("#" * 100, "\n")

    #############################################

    # Cargamos el fichero de rotaciones
    rot = pd.read_csv("data/20240112_fichero_rotacion.txt", sep="\t")
    rot["Fecha"] = pd.to_datetime(rot["Fecha"], format="%Y%m%d")
    rot[["T.ida", "T.vuelta"]] = rot[["T.ida", "T.vuelta"]].map(rellenarId)
    day = pd.to_datetime("2024-01-15")
    rotaciones = rot[rot["Fecha"] == day].drop_duplicates().dropna()
    list_rotaciones = [tuple(el) for el in rotaciones[["T.ida", "T.vuelta"]].values]

    if "días" in process:
        processDias(
            save_info=True,
            save_dir=dir_save,
            start_date=start_date,
            end_date=end_date,
            estaciones=estaciones,
            trenes=trenes,
            list_rotaciones=list_rotaciones,
        )

    if "semanas" in process:
        processSemanas(
            save_info=True,
            save_dir=dir_save,
            start_date=start_date,
            end_date=end_date,
            start_of_week=start_of_week,
            estaciones=estaciones,
            trenes=trenes,
            list_rotaciones=list_rotaciones,
        )


if __name__ == "__main__":
    main()