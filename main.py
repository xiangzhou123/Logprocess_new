# pd.set_option("future.no_silent_downcasting", True)
import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)

from pathlib import Path

import pandas as pd
import yaml

from src.processor import LogProcessor
from src.utils import (
    getFilesByDate,
    getFilesByWeek,
    guardarExcel,
    isValidCode,
    parallelizeFunction,
)

# Tipos de tren que queremos
train_types = {
    "Approach": "APROXIMACIÓN",
    "Arrival": "LLEGADA",
    "Departure": "SALIDA",
    "Elimination": "BAJA",
    "End": "FIN",
    "Entry": "ENTRY",
    "Exit": "EXIT",
    "Maneuver": "MANIOBRA",
    "Platform": "ALTA",
    "PlatformForecast": "PREVISIÓN",  # "PREDICCIÓN",
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
    #     for e in estaciones:
    #         pbar.set_description(f"Procesando '{e}'")
    #         used_dfs = log_processor.processStation(
    #             e,
    #             df=df_logs,
    #             mov_sorter=mov_sorter,
    #             save_info=save_info,
    #             save_dir=save_dir,
    #             days=days,
    #             list_rotaciones=list_rotaciones,
    #         )
    #         pbar.update()
    return used_dfs


def processDias(
    dir_logs: Path,
    source: str,
    save_info: bool,
    save_dir: Path,
    start_date: str,
    end_date: str,
    estaciones=list[str],
    list_rotaciones: list = [],
):
    log_processor = LogProcessor()

    w_logs = getFilesByDate(dir_logs, start_date, end_date)
    if not w_logs:
        print("No hay logs en los días seleccionados")
        return
    fnames, full_days = list(zip(*w_logs))
    days = f"{full_days[0].strftime('%Y-%m-%d')} - {full_days[-1].strftime('%Y-%m-%d')}"
    # Procesamos y guardamos
    df_logs = log_processor.loadFilesLogs(
        fnames, source, train_types=train_types, days=days, estaciones=estaciones
    )
    df_logs = df_logs[
        (df_logs["Fecha"] >= pd.to_datetime(start_date))
        & (df_logs["Fecha"] <= pd.to_datetime(end_date))
    ]
    used_dfs = processOcupacion(
        df_logs[df_logs["NTécnico"].apply(isValidCode)],
        save_info,
        save_dir,
        days,
        estaciones,
        list_rotaciones,
    )
    return used_dfs


def processSemanas(
    dir_logs: Path,
    source: str,
    save_info: bool,
    save_dir: Path,
    start_date: str,
    end_date: str,
    start_of_week: int = 4,
    estaciones=list[str],
    list_rotaciones: list = [],
):
    log_processor = LogProcessor()

    w_logs = getFilesByWeek(dir_logs, start_date, end_date, start_of_week)
    used_dfs = []
    for w_log in w_logs:
        # Nos guardamos la info de la semana que procesamos
        fnames, full_days = list(zip(*w_log))
        days = f"{full_days[0].strftime('%Y-%m-%d')} - {full_days[-1].strftime('%Y-%m-%d')}"

        df_logs = log_processor.loadFilesLogs(
            fnames, source, train_types=train_types, days=days, estaciones=estaciones
        )
        df_logs = df_logs[
            (df_logs["Fecha"] >= pd.to_datetime(start_date))
            & (df_logs["Fecha"] <= pd.to_datetime(end_date))
        ]
        used_dfs.extend(
            processOcupacion(
                df_logs[df_logs["NTécnico"].apply(isValidCode)],
                save_info,
                save_dir,
                days,
                estaciones,
                list_rotaciones,
            )
        )
    return used_dfs


def entreEstaciones(
    dir_logs: Path,
    source: str,
    start_date: str,
    end_date: str,
    estaciones=list[str],
    list_rotaciones: list = [],
):
    log_processor = LogProcessor()

    w_logs = getFilesByDate(dir_logs, start_date, end_date)
    fnames, full_days = list(zip(*w_logs))
    days = f"{full_days[0].strftime('%Y-%m-%d')} - {full_days[-1].strftime('%Y-%m-%d')}"
    fname = Path(f"Tiempos estaciones {days}.xlsx")
    # Procesamos y guardamos
    df_logs = log_processor.loadFilesLogs(fnames, source, train_types, days, estaciones)
    df_logs = df_logs[
        (df_logs["Fecha"] >= pd.to_datetime(start_date))
        & (df_logs["Fecha"] <= pd.to_datetime(end_date))
    ]
    df_logs = df_logs.sort_values(by=["NTécnico", "Fecha"])
    df_logs["tdiff"] = df_logs["Fecha"].diff()
    guardarExcel(
        df_logs.sort_values(by=["Fecha"]).drop("tdiff", axis=1).reset_index(drop=True),
        fname,
        sheet_name="Movimientos",
    )
    print("- Movimientos")
    t_trayectos = log_processor.getTiemposEstaciones(df_logs)
    guardarExcel(
        t_trayectos[
            [
                "NTécnico",
                "Fecha_orig",
                "Código_orig",
                "Nombre_orig",
                # "Mnemónico_orig",
                "Vía_orig",
                "Fecha_dest",
                "Código_dest",
                "Nombre_dest",
                # "Mnemónico_dest",
                "Vía_dest",
                "TiempoTrayecto",
            ]
        ],
        fname,
        sheet_name="Trayectos",
    )
    print("- Trayectos")

    # Obtenemos las rotaciones
    used_dfs = processOcupacion(df_logs, False, "", days, estaciones, list_rotaciones)
    df = pd.concat([el[0] for el in used_dfs if el])
    df = df[df["Movimiento"] == "ROTACIÓN"]
    df = df[
        [
            "Código",
            "Estación",
            "Vía",
            "T1",
            "T2",
            "T_seq",
            "InicioOcupación",
            "FinOcupación",
            "Ocupación",
            "Movimiento",
        ]
    ]
    guardarExcel(
        df.sort_values(by=["Código", "Vía", "InicioOcupación"]),
        fname,
        sheet_name="Rotaciones",
    )
    print("- Rotaciones")

    return used_dfs


def processAproximacionesIncorrectas(
    dir_logs: Path,
    source: str,
    save_info: bool,
    save_dir: Path,
    start_date: str,
    end_date: str,
    estaciones: list[str] = [],
):
    if not source == "xsiv":
        print("Error: la anticipación solo se puede hacer con mensajería MSE.")
        return
    log_processor = LogProcessor()
    w_logs = getFilesByDate(dir_logs, start_date, end_date)
    fnames, full_days = list(zip(*w_logs))
    days = f"{full_days[0].strftime('%Y-%m-%d')} - {full_days[-1].strftime('%Y-%m-%d')}"

    df_logs = log_processor.loadFilesLogs(fnames, source, train_types, days, estaciones)
    df_logs = df_logs[
        (df_logs["Fecha"] >= pd.to_datetime(start_date))
        & (df_logs["Fecha"] <= pd.to_datetime(end_date))
    ]
    df_logs = df_logs.sort_values(by=["NTécnico", "Fecha"])

    aproximaciones = df_logs[df_logs["Movimiento"].isin(["APROXIMACIÓN", "LLEGADA"])]
    if not estaciones:
        estaciones = aproximaciones["Código"].dropna().unique()
    stations_wrong: list[pd.DataFrame] = parallelizeFunction(
        log_processor.getWrongApprox,
        data=estaciones,
        df_aproximaciones=aproximaciones,
        mov_sorter=mov_sorter,
        leave=True,
        desc=f"Obteniendo aproximaciones por estación",
    )
    ant_wrong = pd.concat([st for st in stations_wrong if not st.empty])
    if save_info:
        ant_wrong.to_excel(
            save_dir.joinpath(f"aproximaciones_incorrectas {days}.xlsx"), index=False
        )
    return ant_wrong


def main():
    #############################################
    # Cargamos la configuración
    config_file = Path("config.yaml")
    config = loadConfig(config_file)

    dir_logs = Path(config["dir_logs"])
    dir_save = Path(config["dir_save"])

    # descargar = config["descargar"]
    # segundos = config["segundos"]
    source = config["source"]
    dir_logs = dir_logs.joinpath(source)
    dir_save = dir_save.joinpath(source)
    pro = config["pro"]
    if pro:
        dir_logs = dir_logs.joinpath("PRO")
        dir_save = dir_save.joinpath("PRO")
    else:
        dir_logs = dir_logs.joinpath("PRE")
        dir_save = dir_save.joinpath("PRE")

    process = config["process"]

    start_date = config["start_date"]
    end_date = config["end_date"]
    start_of_week = int(config["start_of_week"])

    estaciones = sorted(list(set(config["estaciones"])))

    print("\nConfiguración:")
    print("#" * 100)
    print(f"Directorio logs:        {dir_logs}")
    print(f"Directorio resultados:  {dir_save}")
    # if descargar:
    #     print(f"Descargar los últimos   {segundos} segundos")
    print(f"Tipo logs:              {source}")
    if process:
        print(f"Procesar: [{', '.join(process)}] entre {start_date} y {end_date}")
    if process and "Semanas" in process:
        print(f"Inicio de semana:       {start_of_week}")
    if estaciones:
        print(f"En las siguientes estaciones: [{', '.join(estaciones)}]")
    print("#" * 100, "\n")

    #############################################

    # Cargamos el fichero de rotaciones
    rot = pd.read_csv("data/20240112_fichero_rotacion.txt", sep="\t")
    rot["Fecha"] = pd.to_datetime(rot["Fecha"], format="%Y%m%d")
    rot["T.ida"] = rot["T.ida"].apply(
        lambda x: f"{int(x):0>5}" if pd.notna(x) else None
    )
    rot["T.vuelta"] = rot["T.vuelta"].apply(
        lambda x: f"{int(x):0>5}" if pd.notna(x) else None
    )
    day = pd.to_datetime("2024-01-15")
    rotaciones = rot[rot["Fecha"] == day].drop_duplicates().dropna()
    list_rotaciones = [tuple(el) for el in rotaciones[["T.ida", "T.vuelta"]].values]

    #############################################

    # riv = {
    #     "RED DE ALTA VELOCIDAD (RAV)": "60000;04104;03208;03216;37700;08004;A0660;02002;04307;03410;92102;02030;03213;03309;04007;03412;08240;02005;37704;08247;08251;A0180;A0720;30002;04018;03205;04044;03300;03305;04055;03310;04107;08007;04300;03203;37113;04009;08014;08253;03202;37210;08252;03302;08016;08009;37801;04110;37104;03214",
    #     "RC SUR": "51003;54413;50500;54517;51405;54509;54511;54516;51103;51300;51400;51407;51419;51200;51406;50600;02003;51415;50700;50702;43005;51101;50417;43003;43026;43027;54405;05000;51203;54412;54406;54407;54408;54410;50413;50502;50501;50506;51205;50504;37500;37606;50300;54400;03100;05012;50407;37603;51202;50507",
    #     "RC NORTE": "13200;14223;13303;13304;13305;13400;13205;13106;13114;13110;13111;13113;13120;10600;05621;13405;13506;05605;13501;13503;13504;13505;13100;13101;05651;05655;05657;05623;05658;11400;11511;11500;05611;05617;05619;05604;05451;05455;05457;05461;05463;05467;11404;11503;14100;11515;05483;05475;11516;11407",
    #     "RC NOROESTE": "15211;15410;15218;15300;15212;16403;15301;15400;05509;15208;15205;15122;15203;15210;05505;15200;16401;05325;15302;16302;16400;31400;05211;05203;05209;05217;22100;05513;15207;05507;15100;23004;05523;23008;05517;21010;16002;16008;16009;16011;30100;05373;16005;05379;05375;05213;05311;16405;31412;05369",
    #     "RC NORESTE": "71801;72305;78804;78805;71802;78802;78800;78806;79400;78706;71707;71708;79500;71706;71700;71705;79404;79405;79407;79410;71600;04040;79100;72209;72210;78700;79006;78703;71701;72300;79004;72303;72301;79600;79104;79005;79011;79200;72503;79603;71100",
    #     "RC ESTE": "65000;60911;65002;65300;64100;64201;64203;65200;64200;61200;64104;64102;65008;65208;60600;69102;69103;69104;69105;69107;69110;62002;65202;65207;60913;62109;05951;05957;05965;05971;05977;62103;62003;60914;62100;62001;65402;62101;62104;64006;65006;65205;65400;66211;65312;66212;65311;64003;64004;65422",
    #     "RC CENTRO": "17000;18000;18002;37001;60100;18001;18101;35002;10000;35001;35607;37012;17001;35703;17009;35608;35609;35600;37002;35603;35606;35604;70103;70102",
    # }
    # for riv, est in riv.items():
    #     processDias(
    #         dir_logs=dir_logs,
    #         source=source,
    #         save_info=True,
    #         # save_dir=dir_save,
    #         # save_dir=Path(
    #         #     "C:/Users/jose.espinosa/ADIF/Elcano - _Análisis Calidad Datos MSE y MIE/1. anticipación y ocupación"
    #         # ),
    #         save_dir=Path(f"output/RIV/{riv}"),
    #         start_date=start_date,
    #         end_date=end_date,
    #         estaciones=est.split(";"),
    #         list_rotaciones=list_rotaciones,
    #     )
    if not process:
        exit(0)

    if "Días" in process:
        processDias(
            dir_logs=dir_logs,
            source=source,
            save_info=True,
            save_dir=dir_save,
            start_date=start_date,
            end_date=end_date,
            estaciones=estaciones,
            list_rotaciones=list_rotaciones,
        )

    if "Semanas" in process:
        processSemanas(
            dir_logs=dir_logs,
            source=source,
            save_info=True,
            save_dir=dir_save,
            start_date=start_date,
            end_date=end_date,
            start_of_week=start_of_week,
            estaciones=estaciones,
            list_rotaciones=list_rotaciones,
        )

    if "AproximacionesIncorrectas" in process:
        processAproximacionesIncorrectas(
            dir_logs=dir_logs,
            source=source,
            save_info=True,
            save_dir=dir_save,
            start_date=start_date,
            end_date=end_date,
            estaciones=estaciones,
        )

    if "EntreEstaciones" in process:
        entreEstaciones(
            dir_logs=dir_logs,
            source=source,
            start_date=start_date,
            end_date=end_date,
            estaciones=estaciones,
            list_rotaciones=list_rotaciones,
        )


if __name__ == "__main__":
    main()
