import argparse
import sys
from datetime import timedelta
from pathlib import Path
from time import sleep
from typing import Union

import pandas as pd
import requests
from tqdm.auto import tqdm

from src.api.api import GraylogAPIProcessor

pd.set_option("future.no_silent_downcasting", True)


def descargarLogs(
    dir_logs: Union[str, Path],
    inicio: str,
    fin: str,
    fuente: str,
    pre: bool,
    ambito: str,
):
    date_start = pd.to_datetime(inicio)
    date_end = pd.to_datetime(fin)

    # Creamos el directorio
    dir_logs = dir_logs.joinpath(f"{fuente}")
    if fuente in ["mie_mse", "xsiv"]:
        if pre:
            dir_logs = dir_logs.joinpath("PRE")
        else:
            dir_logs = dir_logs.joinpath("PRO")
    download_dir = dir_logs.joinpath(f"{date_start.strftime('%Y-%m')}")
    download_dir.mkdir(parents=True, exist_ok=True)

    # Definimos nombre de fichero
    if (
        date_start.hour
        or date_start.minute
        or date_start.second
        or date_end.hour
        or date_end.minute
        or date_end.second
    ):
        fname_date = f'{date_start.strftime("%Y-%m-%d-%H%M%S")} - {date_end.strftime("%Y-%m-%d-%H%M%S")}'
    elif (date_end - date_start) == timedelta(days=1):
        fname_date = f'{date_start.strftime("%Y-%m-%d")}'
    else:
        fname_date = (
            f'{date_start.strftime("%Y-%m-%d")} - {date_end.strftime("%Y-%m-%d")}'
        )

    if ambito and fuente in ["mie_mse", "socket_in"]:
        fname = download_dir.joinpath(f"{ambito} {fname_date}.csv")
    else:
        fname = download_dir.joinpath(f"{fname_date}.csv")

    if fname.exists():
        print(f"Ya existe el fichero '{fname}'")
        return

    # Descargar
    api_base_path = "http://grayloglocal.elcano.adif.es:9000/api/"
    request_path = api_base_path + "search/universal/absolute/export"

    # Transformamos la lista de estaciones y generamos la query
    query = []
    fields = "message"
    if fuente == "xsiv":
        if pre:
            query.append("NOT collector_node_id:VILRMSE00?")
        else:
            query.append("collector_node_id:VILRMSE00?")
        query.extend(
            [
                'source:"/opt/appl/logs/mse/xsiv_published.log"',
                "registerType:AUDITED",
            ]
        )
    elif fuente == "mie_mse":
        fields = "source," + fields
        query.append("source:\/opt\/appl\/logs\/mse\/*mie*")
        if ambito == "tren":
            query.append("_exists_:technicalNumber")
        elif ambito == "elemento":
            query.append("NOT _exists_:technicalNumber")
        if pre:
            query.append("NOT collector_node_id:VILRMSE00?")
        else:
            query.append("collector_node_id:VILRMSE00?")
    # elif fuente == "socket_in":
    #     fields = "source," + fields
    #     if pre:
    #         query.append("NOT collector_node_id:VILRELC0*")
    #     else:
    #         query.append("collector_node_id:VILRELC0*")
    #     query.extend([f"({ambitos[ambito]})", "DMS1"])
    elif fuente == "sitra":
        query.append(
            " OR ".join(
                [
                    '(source:"/opt/appl/logs/mse/xMSG_MSEDelegation.log")',
                    '(source:"/opt/appl/logs/mse/xMSG_MSECentral.log" AND NOT "trainNotice" AND NOT "stopLapse")',
                ]
            )
        )

    query = " AND ".join(query)

    print("Requesting info...")
    queryParams = {
        "query": query,
        "rangetype": "absolute",
        "from": inicio.strftime("%Y-%m-%d %H:%M:%S"),
        "to": fin.strftime("%Y-%m-%d %H:%M:%S"),
        "batch_size": 500,
        "fields": fields,
    }
    for k, v in queryParams.items():
        print(f"{k:<20}{v}")

    response = requests.get(
        url=request_path,
        params=queryParams,
        auth=("1kdpmaon8u8hhe6iimivo4469j8kbica778nse6r3pgjatb0l8ru", "token"),
        stream=True,
    )
    if not response.ok:
        raise Exception(f"Error api request code: {response.status_code}")
    if not response.status_code == 200:
        print(f"Error en la respuesta")
        return
    if not response.encoding == "utf-8":
        response.encoding = "utf-8"

    # Sizes in bytes.
    total_size = int(response.headers.get("content-length", 0))
    block_size = 1024
    with fname.open("wb") as f:
        with tqdm(
            total=total_size,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=f"Descargando en '{fname}'",
        ) as progress_bar:
            for data in response.iter_content(block_size):
                f.write(data)
                progress_bar.update(len(data))
                # file.write(data)

    if total_size != 0 and progress_bar.n != total_size:
        raise RuntimeError("No se ha podido descargar")
    if progress_bar.n:
        print("Vaciando buffer", end="\r", flush=True)
        sleep(60)
        print("               ")
    print("\n")


def main():
    #############################################
    # Args
    print(sys.argv)
    parser = argparse.ArgumentParser(description="Descargar logs.")
    parser.add_argument(
        "--dir-logs",
        nargs=1,
        default="C:/Users/jose.espinosa/Documents/Data",
        help="Directorio donde se guardan los logs.",
    )
    parser.add_argument(
        "--pre",
        action="store_true",
        help="Usar entorno PRE en vez de PRO.",
    )
    parser.add_argument(
        "--fuente",
        nargs=None,
        default="xsiv",
        help="Fuente de los logs.",
    )
    parser.add_argument(
        "--ambito",
        nargs=None,
        required="mie_mse" in sys.argv,
        help="Ámbito de los logs (solo 'mie_mse').",
    )

    args = parser.parse_args()
    dir_logs = Path(args.dir_logs)
    fuente = args.fuente
    pre = args.pre
    ambito = args.ambito

    #############################################

    # Fecha desde ayer hasta hoy a las 03:00
    # today = pd.to_datetime(datetime.today().date())
    # date_start = today - timedelta(hours=21)
    # date_end = today + timedelta(hours=3)
    # # date_start = today - timedelta(hours=21)-timedelta(days=1)
    # # date_end = today + timedelta(hours=3)-timedelta(days=1)

    # fuente = "mie_mse"
    ambito = "tren"
    date_range = pd.date_range(
        "2025-03-16 12:00:00",
        "2025-03-18 00:00:00",
        freq="12h",
    )
    for f in [
        "xsiv",
        "sitra",
        "mie_mse",
    ]:
        for i in range(len(date_range) - 1):
            print(date_range[i], date_range[i + 1])
            descargarLogs(dir_logs, date_range[i], date_range[i + 1], f, pre, ambito)


# from src.api.APIs import getInfoAPIs, getEstaciones
# from src.utils import guardarExcel
# import json
# # TODO: Actualizar información de las topos MSE
# info_topos = getInfoToposMSE()
# with Path("data/estaciones_HMI_topo.json").open("w", encoding="utf8") as f:
#     json.dump(info_topos, f, ensure_ascii=False, indent=4)
# # TODO: Actualizar información de las estaciones
# estaciones = getEstaciones()
# guardarExcel(estaciones, "data/info_estaciones.xlsx")
# # TODO: Descargar puntos de regulación de BI

if __name__ == "__main__":
    main()
