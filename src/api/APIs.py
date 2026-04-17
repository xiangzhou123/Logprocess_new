"""
Definiciones de API:

http://info.api.elcano.operaciones.adif/elcano-port-royal-manager/swagger-ui/
http://info.api.elcano.operaciones.adif/mse-circulations/swagger-ui/
"""

import json
from datetime import timedelta

import numpy as np
import pandas as pd
import regex
import requests
from typing import List
from src.utils import (
    getEstacionamientos,
    isEmpty,
    isValidCode,
    parallelizeFunction,
    splitDataframe,
    splitList,
    time2localtime,
)
from src.utils.util import loadEstaciones

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


def hacerPeticion(method: str, URL: str, headers: dict = {}, data: dict = None):

    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Prefer": "respond-async",
        **headers,
    }

    response = requests.request(method, URL, headers=headers, data=data)
    if response.status_code == 200:
        return response
    print("")
    if response.status_code >= 400:
        print(f"Error: '{response.status_code}' en la respuesta")
        return
    if response.status_code >= 300:
        print(f"Redirección: código'{response.status_code}'")
        return
    if response.status_code > 200:
        print(f"👍: código'{response.status_code}'")
        return
    if response.status_code < 200:
        print(f"Info: código'{response.status_code}'")
        return
    if not response.encoding == "utf-8":
        response.encoding = "utf-8"
    return response


# IP = "10.251.99.100"
# HOST = "info.api.elcano.operaciones.adif"
# URL = f"http://{IP}"
# "http:info.api.elcano.operaciones.adif"


def getInfoToposMSE(pro=True):
    """
    Carga el json del HMI topo
    """
    HOSTPATH = "http://topo.rail.api.elcano.operaciones.adif/msetopo/findAllToposInfo"
    if not pro:
        HOSTPATH = HOSTPATH.replace(".api.", ".api.pre.")
    response = hacerPeticion("GET", HOSTPATH)
    info_topos = json.loads(response.text)
    conectores= info_topos["configCTCs"]
    cod2ctc = []
    for con in conectores:
        # print(con['configCTCMetadata']['ctcName'])
        if not con["configInterLocks"]:
            continue
        metadata = con["configCTCMetadata"]
        ctc = metadata["ctc"].strip()
        ctcName = regex.sub(r"\bCTC\b", "", metadata["ctcName"]).strip()
        tecnologo = metadata["manufacturer"]
        catalogo = metadata["catalogueId"]
        for interlock in con["configInterLocks"]:
            mnem = interlock["configInterLockMetadata"]["interlock"]
            for dep in interlock["configDependences"]:
                local_op = dep.get("localOperation")
                if isinstance(local_op, bool):
                    local_op = "True" if local_op else "False"
                manual_config =   dep.get("manualConfig")
                if isinstance(manual_config,bool):
                    manual_config = "True" if manual_config else "False"
                breteles =  dep.get("scissorCrossing")
                if isinstance(breteles,bool):
                    breteles = "True" if breteles else "False"
                sectores = dep.get("sectors")
                if isinstance(sectores,bool):
                    sectores = "True" if sectores else "False"
                cod2ctc.append(
                    (
                        dep.get("delegation"),
                        catalogo or dep.get("catalogueId"),
                        ctc,
                        ctcName,
                        tecnologo,
                        dep.get("pointId"),
                        dep.get("pointName"),
                        mnem,
                        dep.get("acronym"),
                        dep.get("red"),
                        local_op,
                        manual_config,
                        breteles,
                        sectores

                    )
                )
                # cod2ctc.append((dep["pointId"], con['configCTCMetadata']['ctcName']))
    cod2ctc = (
        pd.DataFrame(
        cod2ctc,
        columns=[
            "Delegación",
            "Catálogo",
            "CTC",
            "NombreCTC",
            "Tecnólogo",
            "Código",
            "Nombre",
            "Mnemónico",
            "Mnemónico_comercial",
            "Red",
            "MandoLocal",
            "ConfiguraciónManual",
            "Breteles",
            "Sectores",
        ],
    )
    .fillna("")
    .map(lambda x: x.strip())
    )
    # cod2ctc = dict(cod2ctc)
    return cod2ctc

def getInfoToposMSEWithoutCTC(pro=True):
    """
    Carga el json del HMI topo sin CTC
    """
    HOSTPATH = "http://topo.rail.api.elcano.operaciones.adif/msetopo/findAllToposInfoWithoutCtc"
    if not pro:
        HOSTPATH = HOSTPATH.replace(".api.", ".api.pre.")
    response = hacerPeticion("GET", HOSTPATH)
    info_topos = json.loads(response.text)
    puntosId = info_topos["pointIdsWithoutCTC"]
    df = pd.DataFrame(puntosId)
    rename_columns = {
        "pointId": "Código",
        "pointName":"Nombre", 
        "delegation": "Delegación",
        "trustedArrival":"ConfianzaLlegada",
        "trustedDeparture":"ConfianzaSalida",
        "trustedDepartureOrigin":"ConfianzaSalidaOrigen",
        "trustedTracks":"Confianza de seguimiento",
        "ctc": "CTC",
        "ager":"AGER",
        "red":"Red"}
    df = df.rename(columns=rename_columns)
    return df


def getInfoEstacionesComerciales():
    """
    Carga el json del HMI comercial
    """
    HOSTPATH = "http://info.api.elcano.operaciones.adif/stationsmanager/stations/"
    response = hacerPeticion("GET", HOSTPATH)
    info_topos = json.loads(response.text)["data"]["list"]
    return info_topos


def getInfoFiabilidadEstacion(estacion: str):
    """
    Carga las vías fiables de la estación a partir del HMI comercial
    """
    HOSTPATH = f"http://info.api.elcano.operaciones.adif/stationsmanager/stations/station/{estacion}"
    response = hacerPeticion("GET", HOSTPATH)
    info_topos = json.loads(response.text)
    vias = info_topos["data"]["platforms"]["platforms"]
    fiabilidad = []
    for v in vias:
        fiabilidad.append(
            (
                v["technical"],
                v["commercial"],
                "".join([r["product"] for r in v["reliablePlatforms"]]),
            )
        )
    fiabilidad = pd.DataFrame(
        fiabilidad, columns=["Técnica", "Comercial", "Fiabilidad"]
    )
    return fiabilidad


def getInfoAPIs():
    """
    Información genérica de las APIs
    """
    HOSTPATH = "http://info.api.elcano.operaciones.adif/mse-circulations/doc/openapi/"
    response = hacerPeticion("GET", HOSTPATH)
    info_api_circulaciones = json.loads(response.text)
    return info_api_circulaciones
def parse_launching_date(ld):
    """
    Convierte launchingDate a pd.Timestamp:
    - Lista/tupla [YYYY, M, D] o [YYYY, M, D, hh, mm, ss]
    - String o cualquier otro formato reconocido por pd.to_datetime
    - Devuelve NaT si no se puede convertir
    """
    try:
        # Caso: lista o tupla
        if isinstance(ld, (list, tuple)) and len(ld) >= 3:
            y, m, d = int(ld[0]), int(ld[1]), int(ld[2])
            if len(ld) >= 6:
                hh, mm, ss = int(ld[3]), int(ld[4]), int(ld[5])
                return pd.Timestamp(year=y, month=m, day=d, hour=hh, minute=mm, second=ss)
            return pd.Timestamp(year=y, month=m, day=d)
        # Caso: otros tipos -> pd.to_datetime intenta convertir
        return pd.to_datetime(ld, errors='coerce')
    except Exception:
        return pd.NaT

def getCirculacionesPlanificadas(date):
    """
    Información sobre circulacion de un día concreto
    """
    HOSTPATH = "http://info.api.elcano.operaciones.adif/mse-circulations/msecirculations/planning/day/"
    data = {"day": pd.to_datetime(date).strftime("%Y-%m-%d")}
    data = json.dumps(data)
    response = hacerPeticion(
        "POST",
        HOSTPATH,
        data=data,  
    )
    res_data = regex.sub(r"\n*data:\s*", ",", response.text)[1:]
    res_data = json.loads(f"[{res_data}]")
    rows = []
    for el in res_data:
        cid = el.get("circulationId", {}) or {}
        tecnico = cid.get("number")
        fecha = parse_launching_date(cid.get("launchingDate"))
        day_train = el.get("dayTrain", {}) or {}
        line_dict = day_train.get("line") or {}  # Asegura que sea dict, no None
        line = line_dict.get("name")  # Extrae el nombre de la línea
        company = day_train.get("company")
        operator = day_train.get("operator")
        train_type = day_train.get("trainType")
        steps = day_train.get("journey", {}).get("steps") or []
        for s in steps:
            rows.append({
                "NTécnico": tecnico,
                "FechaOrigen": fecha,
                "Secuencia": s.get("step"),
                "Código": s.get("pointId"),   
                "Vía_Planificada": s.get("parkingTrack"),
                "Línea": line,
                "Compañia": company,
                "Operador": operator,
                "TipoTren": train_type
            })

    planificacion = pd.DataFrame(rows)
    return planificacion
def getEstaciones():
    """
    Obtener la información disponible para el usuario de todas las estaciones desde API de circulaciones.
    """
    # HOSTPATH = "/portroyalmanager/stations/allstations/"
    HOSTPATH = (
        "http://info.api.elcano.operaciones.adif/portroyalmanager/stations/filtered/"
    )
    data = {
        "detailedInfo": {
            "extendedStationInfo": True,
            "stationActivities": True,
            "stationBanner": True,
            "stationCommercialServices": True,
            "stationInfo": True,
            "stationServices": True,
            "stationTransportServices": True,
        },
        "filter": "ALL",
        "token": "string",
    }
    data = json.dumps(data)

    response = hacerPeticion(
        "POST",
        HOSTPATH,
        data=data,
    )
    info_estaciones = [
        json.loads(el.strip("data:"))
        for el in (regex.split(r"\n+", response.text.strip()))
    ]
    info_estaciones = pd.json_normalize(
        [el["requestedStationInfo"] for el in info_estaciones]
    )
    rename_cols = {
        "stationCode": "Código",
        "stationInfo.stationType": "Tipo",
        "stationInfo.longName": "Nombre",
        "stationInfo.shortName": "NombreCorto",
        "stationInfo.trafficType": "Tráfico",
        "stationInfo.commuterNetwork": "Cercanías",
        "stationInfo.lines": "Lineas",
        "stationInfo.location.longitude": "Longitud",
        "stationInfo.location.latitude": "Latitud",
    }
    info_estaciones = info_estaciones[list(rename_cols.keys())].rename(
        columns=rename_cols
    )
    info_estaciones = info_estaciones.sort_values(by=["Tipo", "Código"])
    return info_estaciones


def getHistoricoMOW(
    estaciones: List[str],
    trenes: List[str],
    inicio: str,
    fin: str,
    xSIV: bool = True,
    xSIVPLUS: bool = False,
    jCTC: bool = False,
    xREG: bool = False,
    pro: bool = True,
    maniobra: bool = False,
):
    """
    Obtiene el histórico de movimientos de las estaciones y/o trenes seleccionados en las fechas correspondientes

    Parámetros
    ----------
    - estaciones: list[str]
        Lista de códigos de estación
    - trenes: list[str]
        Lista de códigos técnicos de tren
    - inicio: str
        Fecha desde la que se quiere los registros en formato YYYY-mm-dd
    - fin: str
        Fecha hasta la que se quiere los registros en formato YYYY-mm-dd

    """
    HOSTPATH = "http://info.api.elcano.operaciones.adif/movementsmanager/history/"
    if not pro:
        HOSTPATH = HOSTPATH.replace(".api.", ".api.pre.")

    def iterateHistoric(trenes, ranges, inicio, fin):
        movimientos = []
        page = 0
        last_timestamp = (
            int(pd.to_datetime(inicio).tz_localize("Europe/Madrid").timestamp()) * 1000
        )
        print(last_timestamp)
        print("incio:",pd.to_datetime(inicio).tz_localize("Europe/Madrid").timestamp())
        print("fin:",pd.to_datetime(fin).tz_localize("Europe/Madrid").timestamp())
        
        while True:
            print(f"\rPágina {page} ({inicio} - {fin})", end="")
            data = {
                "page": page,
                "size": 10000,
                "initDate": int(
                    pd.to_datetime(inicio).tz_localize("Europe/Madrid").timestamp()
                )
                * 1000,
                "endDate": int(
                    pd.to_datetime(fin).tz_localize("Europe/Madrid").timestamp()
                )
                * 1000,
                "lastTimestamp": last_timestamp,
                "technicalNumbers": trenes,
                "technicalNumberRanges": ranges,
                "stationCodes": estaciones,
                "xSIV": xSIV,
                "xSIVPlus": xSIVPLUS,
                "jCTC": jCTC,
                "xREG": xREG,
            }
            data = json.dumps(data)

            response = hacerPeticion(
                "POST",
                HOSTPATH,
                # headers=headers,
                data=data,
            )
            if not response:
                break
            response = response.json()
            movimientos.extend(response["movementList"]["list"])
            page += 1
            if page == response["totalPages"]:
                break
            last_timestamp = movimientos[-1]["datetime"]
        return movimientos

    if len(trenes) >= 1000:
        movimientos = iterateHistoric([], ["00000-99999"], inicio, fin)
    elif len(trenes) > 100:
        sep_trenes = splitList(sorted(trenes), 100)
        movimientos = []
        for t in sep_trenes:
            movimientos.extend(iterateHistoric(t, [], inicio, fin))
    else:
        movimientos = iterateHistoric(trenes, [], inicio, fin)

    df_movimientos = pd.DataFrame(movimientos)
    if df_movimientos.empty:
        return df_movimientos
    if (xSIV == True or jCTC == True or xSIVPLUS == True):
        rename_cols = {
            "datetime": "Fecha",
            "technicalNumber": "NTécnico",
            "stationName": "Nombre",
            "stationCode": "Código",
            "ctc": "CTC",
            "interlock": "Mnemónico",
            "element": "Elemento",
            "direction": "Sentido",
            "sequence": "Secuencia",
            "messageSource": "FuenteMensaje",
            "movementType": "Movimiento",
            "movementSubType": "SubtipoMovimiento",
            "movementSource": "FuenteMovimiento",
            "registerType": "Registro",
            "platform": "Vía",
            # "platformType": "TipoVía",
            "sourcePlatform": "FuenteVía",
            "delayInSeconds": "Retraso (segundos)",
            "operator": "CategoríaCirculación",
            "product": "Producto",
            "trainCompanyCode": "Empresa",
            # "triggerElement": "ElementoDisparo",
            "originDate": "FechaOrigen",
            # "travelTime": "",
            # "parkingTime": "",
            # "platformCommercialCode": "",
            "commercialLineCode": "LíneaComercial",
            "originPlannedPointCode": "CódigoOrigen",
            "originPlannedPointName": "NombreOrigen",
            "destinationPlannedPointCode": "CódigoDestino",
            "destinationPlannedPointName": "NombreDestino",
            "technicalDeparturePlanned": "SalidaPlanificada",
            "descriptionMap": "Descripción",
            "networkName":"Núcleo"
        }
        df_movimientos = df_movimientos[
            [el for el in rename_cols.keys() if el in df_movimientos.columns]
        ].rename(columns=rename_cols)

        if trenes:
            df_movimientos = df_movimientos[df_movimientos["NTécnico"].isin(trenes)]
        if estaciones and xSIV:
            df_movimientos = df_movimientos[df_movimientos["Código"].isin(estaciones)]
        df_movimientos[["Fecha", "FechaOrigen", "SalidaPlanificada"]] = pd.concat(
            parallelizeFunction(
                lambda x: x.map(time2localtime, unit="ms"),
                data=splitDataframe(
                    df_movimientos[["Fecha", "FechaOrigen", "SalidaPlanificada"]], 1000
                ),
                show_progress=True,
                desc="Formateando fechas.",
                output="series",
            )
        )
        # Procesamos xSIV y jCTC por separado y los unimos
        df_movimientos_jctc = df_movimientos[
            df_movimientos["FuenteMensaje"] == "JCTC"
        ].copy()
        df_movimientos_xsiv = df_movimientos[
            df_movimientos["FuenteMensaje"] == "XSIV"
        ].copy()
        if jCTC :
            df_movimientos_jctc = processJCTC(df_movimientos_jctc)
        else:
            df_movimientos_jctc = pd.DataFrame()
        if xSIV or xSIVPLUS:
            df_movimientos_xsiv = processXSIV(df_movimientos_xsiv,maniobra)
        else:
            df_movimientos_xsiv = pd.DataFrame()
        df_movimientos = (
            pd.concat([df_movimientos_xsiv, df_movimientos_jctc])
            .sort_values(by=["NTécnico", "Fecha"])
            .reset_index(drop=True)
        )
    if (xREG == True):
        df_movimientos = processXreg(df_movimientos)
    return df_movimientos


def getCirculacionesComerciales(codigo: str) -> pd.DataFrame:
    """
    Carga las siguientes salidas de la estación seleccionada
    """
    HOSTPATH = "http://info.api.elcano.operaciones.adif/portroyalmanager/circulationpaths/departures/traffictype/"

    movimientos = []
    page = 0
    while True:
        print(f"\r{codigo} - Página {page}", end="", flush=True)
        data = {
            "commercialService": "BOTH",
            "commercialStopType": "BOTH",
            "page": {"pageNumber": page},
            "stationCode": codigo,
            "trafficType": "ALL",
        }
        data = json.dumps(data)

        response = hacerPeticion(
            "POST",
            HOSTPATH,
            data=data,
        )
        if not response:
            break
        response = response.json()
        movimientos.extend(response["commercialPaths"])
        page += 1
        # if page == response["totalPages"]:
        #     break

    rename_cols = {
        # "commercialPathInfo.timestamp": "Fecha",
        "commercialPathInfo.commercialPathKey.commercialCirculationKey.commercialNumber": "NComercial",
        "commercialPathInfo.commercialPathKey.commercialCirculationKey.launchingDate": "FechaOrigen",
        "commercialPathInfo.commercialPathKey.originStationCode": "CódigoOrigen",
        "commercialPathInfo.commercialPathKey.destinationStationCode": "CódigoDestino",
        "commercialPathInfo.line": "Línea",
        "commercialPathInfo.observation": "Observaciones",
        "commercialPathInfo.trafficType": "Tráfico",
        "commercialPathInfo.opeProComPro.operator": "Operador",
        "commercialPathInfo.opeProComPro.product": "Producto",
        "commercialPathInfo.opeProComPro.commercialProduct": "ProductoComercial",
        "passthroughStep.stopType": "TipoParada",
        "passthroughStep.stationCode": "Código",
        "passthroughStep.arrivalPassthroughStepSides.plannedTime": "LlegadaPlanificada",
        "passthroughStep.arrivalPassthroughStepSides.forecastedOrAuditedDelay": "RetrasoLlegada",
        "passthroughStep.arrivalPassthroughStepSides.timeType": "TipoLlegada",
        "passthroughStep.arrivalPassthroughStepSides.plannedPlatform": "VíaLlegadaPlanificada",
        "passthroughStep.arrivalPassthroughStepSides.sitraPlatform": "VíaLlegadaSitra",
        "passthroughStep.arrivalPassthroughStepSides.ctcPlatform": "VíaLlegadaCTC",
        # "passthroughStep.arrivalPassthroughStepSides.resultantPlatform":"",
        "passthroughStep.arrivalPassthroughStepSides.circulationState": "EstadoLlegada",
        "passthroughStep.arrivalPassthroughStepSides.technicalCirculationKey.technicalNumber": "NTécnicoLlegada",
        # "passthroughStep.arrivalPassthroughStepSides.technicalCirculationKey.technicalLaunchingDate":"FechaOrigenLlegada",
        "passthroughStep.departurePassthroughStepSides.plannedTime": "SalidaPlanificada",
        "passthroughStep.departurePassthroughStepSides.forecastedOrAuditedDelay": "RetrasoSalida",
        "passthroughStep.departurePassthroughStepSides.timeType": "TipoSalida",
        "passthroughStep.departurePassthroughStepSides.plannedPlatform": "VíaSalidaPlanificada",
        "passthroughStep.departurePassthroughStepSides.sitraPlatform": "VíaSalidaSitra",
        "passthroughStep.departurePassthroughStepSides.ctcPlatform": "VíaSalidaCTC",
        # "passthroughStep.departurePassthroughStepSides.resultantPlatform":"",
        "passthroughStep.departurePassthroughStepSides.circulationState": "EstadoSalida",
        "passthroughStep.departurePassthroughStepSides.technicalCirculationKey.technicalNumber": "NTécnicoSalida",
        # "passthroughStep.departurePassthroughStepSides.technicalCirculationKey.technicalLaunchingDate":"FechaOrigenSalida",
    }
    df_movimientos = pd.json_normalize(movimientos)
    df_movimientos = df_movimientos[
        [el for el in rename_cols.keys() if el in df_movimientos.columns]
    ].rename(columns=rename_cols)

    # Carga de los nombres de estación
    info_estaciones = pd.read_excel("data/info_estaciones.xlsx")[["Código", "Nombre"]]
    info_estaciones["Código"] = info_estaciones["Código"].apply(lambda x: f"{x:0>5}")
    map_codigo_nombre = dict(info_estaciones.values)

    # Formatear info
    date_cols = ["FechaOrigen", "LlegadaPlanificada", "SalidaPlanificada"]
    df_movimientos[[c for c in date_cols if c in df_movimientos.columns]] = pd.concat(
        parallelizeFunction(
            lambda x: x.map(time2localtime, unit="ms"),
            data=splitDataframe(
                df_movimientos[[c for c in date_cols if c in df_movimientos.columns]],
                1000,
            ),
            show_progress=True,
            desc="Formateando fechas.",
            output="series",
        )
    )
    df_movimientos[["NombreOrigen", "NombreDestino", "Nombre"]] = pd.concat(
        parallelizeFunction(
            lambda x: x.map(map_codigo_nombre.get),
            data=splitDataframe(
                df_movimientos[["CódigoOrigen", "CódigoDestino", "Código"]], 1000
            ),
            show_progress=True,
            desc="Formateando fechas.",
            output="series",
        )
    )
    return df_movimientos


def getEstadoCirculacionesTecnicas(dia: str):
    """
    Carga el estado de las circulaciones técnicas de un día del GCT
    """
    HOSTPATH = "http://info.api.elcano.operaciones.adif/mse-circulations/msecirculations/state/day/"
    data = {"day": pd.to_datetime(dia).strftime("%Y-%m-%d")}
    data = json.dumps(data)

    response = hacerPeticion(
        "POST",
        HOSTPATH,
        data=data,
    )
    res_data = regex.sub(r"\n*data:\s*", ",", response.text)[1:]
    res_data = json.loads(f"[{res_data}]")
    gct = pd.DataFrame(
        [
            {
                "NTécnico": el["circulationId"]["number"],
                "Fecha": "-".join(f"{i}" for i in el["circulationId"]["launchingDate"]),
                **r,
            }
            for el in res_data
            for r in el["stateRegisters"]
        ]
    )
    return gct


def getPlanificacionCirculacionesTecnicas(dia: str):
    """
    Carga la planificación de circulaciones técnicas de un día del GCT
    """
    HOSTPATH = "http://info.api.elcano.operaciones.adif/mse-circulations/msecirculations/planning/day/"
    data = {"day": pd.to_datetime(dia).strftime("%Y-%m-%d")}
    data = json.dumps(data)

    response = hacerPeticion(
        "POST",
        HOSTPATH,
        data=data,
    )
    res_data = regex.sub(r"\n*data:\s*", ",", response.text)[1:]
    res_data = json.loads(f"[{res_data}]")
    gct = pd.DataFrame(
        [
            {
                "NTécnico": el["circulationId"]["number"],
                "Fecha": "-".join(f"{i}" for i in el["circulationId"]["launchingDate"]),
                "NComercial": el["dayTrain"]["commercialNumber"],
                "Línea": el["dayTrain"]["line"],
                "Empresa": el["dayTrain"]["company"],
                "Operador": el["dayTrain"]["operator"],
                "Tipo": el["dayTrain"]["trainType"],
                "esComercial": el["dayTrain"]["commercialTrain"],
                "esEspecial": el["dayTrain"]["special"],
                "esVirtual": el["dayTrain"]["virtual"],
                "Recorrido": el["dayTrain"]["journey"],
            }
            for el in res_data
        ]
    )
    gct["Línea"] = gct["Línea"].apply(lambda x: x.get("name") if x else x)
    return gct


def getPlanificacionComercialDia(codigo: str):
    circulaciones = getCirculacionesComerciales(codigo)
    planificaciones = (
        circulaciones.loc[
            (circulaciones["TipoVía"].apply(lambda x: "PLANNED" in x))
            & (circulaciones["Retraso"] == 0)
            & np.invert(
                circulaciones["Producto"].isin(
                    [" ", "Material Vacio", "Servicio Interno"]
                )
            ),
            [
                "Código",
                "Nombre",
                "NTécnico",
                "FechaOrigenTécnico",
                "NComercial",
                "FechaOrigenComercial",
                "CódigoOrigen",
                "NombreOrigen",
                "CódigoDestino",
                "NombreDestino",
                "Línea",
                "Tráfico",
                "Operador",
                "Producto",
                # "TipoParada",
                "TiempoPlanificado",
                # "Retraso",
                "VíaPlanificada",
                "VíaSitra",
                "VíaCTC",
                # "TipoVía",
                # "Estado",
            ],
        ]
        .dropna(subset=["NTécnico"])
        .sort_values(by="TiempoPlanificado")
        .reset_index(drop=True)
    )
    planificaciones: pd.DataFrame = planificaciones[
        (
            planificaciones["FechaOrigenTécnico"]
            == planificaciones["FechaOrigenTécnico"].unique()[-1]
        )
    ]

    planificacion_dia = (
        planificaciones.groupby(
            by=[
                "Código",
                "Nombre",
                "NTécnico",
                "FechaOrigenTécnico",
                "CódigoOrigen",
                "NombreOrigen",
                "CódigoDestino",
                "NombreDestino",
                "Línea",
                "Tráfico",
                "Operador",
                "Producto",
                "TiempoPlanificado",
                "VíaPlanificada",
            ],
            dropna=False,
        )
        .agg(
            {
                "NComercial": lambda x: ",".join([str(el) for el in x]),
                "FechaOrigenComercial": lambda x: ",".join([str(el) for el in x]),
            }
        )
        .reset_index()
        .rename(columns={"Tráfico": "trafficGroup"})
    )

    return planificacion_dia


#################################
#################################
#################################
#################################
#################################
#################################


####################################################################
# Procesar mensajerias solamente de XSIV
###################################################################
def processXSIV(df_movimientos: pd.DataFrame, maniobra: bool):
    # Cargamos las estaciones para asignar CTC
    estaciones = loadEstaciones()
    estaciones = (
        estaciones.loc[
            np.invert(estaciones[["CTC", "Código"]].map(isEmpty).any(axis=1)),
            ["CTC", "Código"],
        ]
        .groupby(["Código"])
        .agg(lambda x: ",".join(set(x)))
        .reset_index()
    )
    # Cargamos los tipos de vía a partir de las topos
    estacionamientos = getEstacionamientos(df_movimientos["Código"].unique().tolist())[
        ["Código", "TipoVía", "Vía"]
    ].drop_duplicates()

    cols_xsiv = [
        "Fecha",
        "NTécnico",
        "CTC",
        "Nombre",
        "Código",
        "Secuencia",
        "Movimiento",
        # "SubtipoMovimiento",
        "Elemento",
        "FuenteMovimiento",
        "Registro",
        "Vía",
        "TipoVía",
        "FuenteVía",
        "Retraso (segundos)",
        "CategoríaCirculación",
        "Producto",
        "Empresa",
        "FechaOrigen",
        "LíneaComercial",
        "CódigoOrigen",
        "NombreOrigen",
        "CódigoDestino",
        "NombreDestino",
        "SalidaPlanificada",
        "FuenteMensaje",
        "Descripción",
        "Núcleo"
    ]

    # Asignamos tipo de vía en función de lo que hay en las topos
    df_movimientos = pd.merge(
        df_movimientos.drop(["CTC"], axis=1),
        estaciones,
        how="left",
        on=["Código"],
    )
    # Asignamos tipo de vía en función de lo que hay en las topos
    df_movimientos = pd.merge(
        df_movimientos,
        estacionamientos,
        how="left",
        on=["Código", "Vía"],
    )
    # df_movimientos = df_movimientos[cols_xsiv]
    if(not maniobra):
        map_movimientos = {
            "ORIGIN": "ORIGEN",
            "MANEUVER_IN": "MANIOBRA",
            "MANEUVER_OUT": "MANIOBRA",
            "IN": "LLEGADA",
            "OUT": "SALIDA",
            "FORECAST": "PREVISIÓN",
            "APROXIMATION": "APROXIMACIÓN",
            "END": "FIN",
            "MANEUVER_APROXIMATION": "MANIOBRA",
            "TRACKING_LOST": "PÉRDIDA_SEGUIMIENTO",
            "ELIMINATION": "ELIMINACIÓN",
            "CIRCULATION_DESTINATION_CHANGED":"CIRCULATION_DESTINATION_CHANGED",
            "CIRCULATION_ORIGIN_CHANGED": "CIRCULATION_ORIGIN_CHANGED"
    }
    elif(maniobra):
         map_movimientos = {
            "ORIGIN": "ORIGEN",
            "MANEUVER_IN": "MANIOBRA_LLEGADA",
            "MANEUVER_OUT": "MANIOBRA_SALIDA",
            "IN": "LLEGADA",
            "OUT": "SALIDA",
            "FORECAST": "PREVISIÓN",
            "APROXIMATION": "APROXIMACIÓN",
            "END": "FIN",
            "MANEUVER_APROXIMATION": "MANIOBRA_APROXIMACION",
            "TRACKING_LOST": "PÉRDIDA_SEGUIMIENTO",
            "ELIMINATION": "ELIMINACIÓN",
            "CIRCULATION_DESTINATION_CHANGED":"CIRCULATION_DESTINATION_CHANGED",
            "CIRCULATION_ORIGIN_CHANGED": "CIRCULATION_ORIGIN_CHANGED"
    }
            
            

    map_ef = {
        "IL": "IRYO",
        "RF": "RENFE",
        "RI": "OUIGO",
        "AD": "ADIF",
    }

    # Formatear info
    df_movimientos["Movimiento"] = df_movimientos["Movimiento"].apply(
        lambda x: map_movimientos.get(x, x)
    )
    df_movimientos["Empresa"] = df_movimientos["Empresa"].apply(
        lambda x: map_ef.get(x) if x in map_ef else x
    )
    return df_movimientos


####################################################################################
# Procesar mensajerias solamente JCTC
####################################################################################
def processJCTC(df_movimientos: pd.DataFrame):
    # Cargamos las estaciones para asignar códigos y nombres
    estaciones = loadEstaciones()
    data = (
        estaciones[["CTC", "Mnemónico_comercial", "Nombre", "Código"]]
        .drop_duplicates()
        .rename(columns={"Mnemónico_comercial": "Mnemónico"})
    )

    cols_jctc = [
        "Fecha",
        "NTécnico",
        "CTC",
        "Mnemónico",
        "Nombre",
        "Código",
        "Elemento",
        "Sentido",
        "Movimiento",
        "SubtipoMovimiento",
        "FuenteMovimiento",
        "FuenteMensaje",
        "Descripción",
    ]

    map_movimientos_jctc = {
        "OCCUPATION": "LLEGADA",
        "LIBERATION": "SALIDA",
    }

    df_movimientos = pd.merge(
        df_movimientos.drop(["Nombre", "Código"], axis=1),
        data,
        on=["CTC", "Mnemónico"],
        how="left",
    )
    df_movimientos = df_movimientos[cols_jctc]
    df_movimientos["Movimiento"] = df_movimientos["SubtipoMovimiento"].apply(
        map_movimientos_jctc.get
    )
    df_movimientos = df_movimientos.drop(["SubtipoMovimiento"], axis=1)
    return df_movimientos


def cargarHistorico(
    start_date: str,
    end_date: str,
    estaciones: List[str],
    trenes: List[str],
    xSIV: bool = True,
    jCTC: bool = False,
    pro: bool = True,
):
    # Comprobamos que la fecha de fin sea después de la de inicio
    if end_date <= start_date:
        end_date = (pd.to_datetime(start_date) + timedelta(days=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    historico = getHistoricoMOW(
        estaciones=estaciones,
        trenes=trenes,
        inicio=start_date,
        fin=end_date,
        xSIV=xSIV,
        jCTC=jCTC,
        pro=pro,
    )
    historico = historico[
        (historico["Fecha"] >= pd.to_datetime(start_date))
        & (historico["Fecha"] <= pd.to_datetime(end_date))
    ]
    # Usamos movimientos auditados
    # historico = historico[
    #     np.invert(historico["FuenteVía"].isin(["PLANNED", "SITRA_PROVIDED"]))
    # ]
    # historico = historico[historico["NTécnico"].apply(isValidCode)].dropna(
    #     subset=["Movimiento"]
    # )
    # historico["mov_ord"] = historico["Movimiento"].apply(mov_sorter.get)
    return historico


def processXreg(df:pd.DataFrame):
    rename_cols ={
        "datetime": "FechaHora",
        "technicalNumber":"NTécnico",
        "stationName":"Nombre",
        "stationCode":"Código",
        "startLocationCode":"CódigoIncio",
        "startLocationName":"NombreInicio",
        "endLocationCode":"CódigoFin",
        "endLocationName":"NombreFin",
        "ctc":"CTC",
        "timestampMSG":"TimestampMSG",
        "interlock": "Enclavamiento",
        "direction":"Dirección",
        "sequence":"Secuencia",
        "startLocationSequence":"SecuenciaInicio",
        "endLocationSequence":"SecuenciaFin",
        "messageSource":"FuenteMensaje",
        "movementType":"TipoMovimiento",
        "movementSource":"FuenteMovimiento",
        "registryType":"TipoRegistro",
        "platform":"Vía",
        "platformType":"TipoVía",
        "sourcePlatform":"FuenteVía",
        "delayInSeconds":"Retraso(s)",
        "operator":"Operador",
        "product":"Producto",
        "trainCompanyCode":"CódigoCompañia",
        "triggerElement":"ElementoTrigger",
        "element":"Elemento",
        "originDate":"FechaOrigen",
        "travelTime":"TiempoViaje",
        "parkingTime":"TiempoEstacionamiento",
        "platformCommercialCode":"CodigoVíaComercial",
        "commercialLineCode":"CódigoLineaComercial",
        "networkName":"NombreRed",
        "originPlannedPointCode":"CódigoOrigenPlanificado",
        "originPlannedPointName":"NombreOrigenPlanificado",
        "destinationPlannedPointCode":"CódigoOrigenPlanificado",
        "destinationPlannedPointName":"NombreDestinoPlanificado",
        "technicalDeparturePlanned":"SalidaTécnicaPlanificado",
        "descriptionMap":"Descripción"
        
    }
    df = df[list(rename_cols.keys())].rename(
        columns=rename_cols
    )
    df[["FechaHora"]] = pd.concat(
        parallelizeFunction(
            lambda x: x.map(time2localtime, unit="ms"),
            data=splitDataframe(df[["FechaHora"]], 1000),
            show_progress=True,
            desc="Formateando fechas.",
            output="series",
            )
    )
    return df