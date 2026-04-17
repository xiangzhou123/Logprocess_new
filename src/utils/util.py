import json
from collections import Counter
from pathlib import Path
from typing import Union
from typing import List
import numpy as np
import pandas as pd
import regex
from lxml import etree

from .parallelization import parallelizeFunction


def isEmpty(x) -> bool:
    """
    True si el valor de x es nulo, está vacío o similar
    """
    return pd.isna(x) | (str(x).strip() in ["", "N/A", "<NA>", "-"])


###
# Agregadores/desagregadores
###


def aggregateCounters(counts: List[Counter]):
    final_count = Counter()
    [(final_count.update(el)) for el in counts]
    return final_count


def joinSets(values: List[set]):
    joint_set = set()
    for el in values:
        if isEmpty(el):
            continue
        joint_set = joint_set | el
    return joint_set


def splitDataframe(df: pd.DataFrame, indices_or_sections: Union[int, List[int]] = 1000):
    l = df.shape[0]
    if isinstance(indices_or_sections, int):
        indices_or_sections = np.min((l, indices_or_sections))
    splits = np.array_split(np.arange(l), l / indices_or_sections)
    return [df.iloc[s] for s in splits]


def roundGroup(vals: Union[int, float, np.ndarray], group: int = 10):
    """
    Asigna un grupo de redondeo. Ej:
        vals=[1, 12, 15, 35], group=10 -> res=[0, 10, 10, 30]
    """
    r = np.round(vals)
    rest = r % group
    result = np.where(np.invert(rest == 0), r - rest + group, r).astype(int)
    if isinstance(vals, np.ndarray):
        return result
    return int(result)


def getPercentageTrue(x: dict):
    if len(x) and sum(x.values()):
        return round(100 * x.get(True, 0) / sum(x.values()), 2)
    return 0


def range_normalization(vias_c_sum):
    min_y = -0.2
    max_y = 0.8
    if len(vias_c_sum) > 1:
        return [
            min_y
            + (x - vias_c_sum.min())
            * (max_y - min_y)
            / (vias_c_sum.max() - vias_c_sum.min())
            for x in vias_c_sum
        ]
    else:
        return [(min_y + max_y) / 2]


###
# Validadores
###


def isOdd(n_tec: Union[int, str]):
    int_part = "".join(regex.findall(r"\d", f"{n_tec}"))
    if not int_part:
        return None
    is_odd = int(int_part) % 2
    return bool(is_odd)


def rellenarId(code: str):
    """
    Convierte un código de tren/estación al formato de 5 dígitos
    """
    if isEmpty(code):
        return None
    code = f"{code}".strip().split(".")[0]
    if not regex.search(r"^[ABCD]?\d+$", code):
        return None
    return f"{code:0>5}"


def isValidCode(code: Union[str, int]):
    """
    True si el formato de código de tren/estación son 5 dígitos.\\
    Añade 0s al principio para rellenar si la longitud es menor.
    """
    if code is None:
        return False
    if regex.search(r"[^\d\.]", code):
        return False
    code = rellenarId(code)
    return bool(regex.search(r"\d{5}", code))


def splitLongString(string: str, length: int = 15):
    """
    Separa un string por palabras para que cada substring tenga una longitud menor de `length`
    """
    lines = []
    line = []
    for w in string.split():
        l_aux = line + [w]
        if len(" ".join(l_aux)) <= length:
            line = l_aux
        else:
            lines.append(" ".join(line))
            line = [w]
    lines.append(" ".join(line))
    return lines


def removeDoubleQuotes(data: str) -> List[str]:
    if regex.search(r'("{4}|""[\w\d\.-]+?"")', data):
        data = regex.sub(r'""', '"', data)
    lines = data.split("\n")
    # lines = parallelizeFunction(
    #     lambda x: regex.sub(r'(?<!=)(?<=.+)""|(?<==)""(?=.+(?<!=)"")', '"', x),
    #     lines,
    #     show_progress=False,
    #     desc="Limpiando texto",
    #     leave=False,
    # )
    return lines


def filterJsonStrElement(msg: str, element: str):
    # el = regex.search(rf'(?<="{element}"\s*:\s*)(\{{(?:[^{{}}]|(?R))*\}}|\[.*?\]|".*?"|\d+|true|false|null)', msg)
    el = regex.search(
        rf'(?<="{element}"\s*:\s*)(\{{(?:[^{{}}]|(?1))*\}}|\[.*?\]|".*?"|-?\d+(\.\d+)?([eE][+-]?\d+)?|true|false|null)',
        msg,
    )
    if el:
        return json.loads(el.group())
    return None


def splitList(input_list, n):
    """Split a list into multiple sublists of max n elements."""
    return [input_list[i : i + n] for i in range(0, len(input_list), n)]


###
# Ordenar
###


def sortStrNumbers(str_list: List[str]):
    """
    Ordena una lista de strings que contiene números por número.
    """
    str_list = [f"{el}" for el in str_list if regex.search("\d+", f"{el}")]
    return sorted(str_list, key=lambda x: int(regex.search("\d+", x).group()))


def sortElements(els):
    numeric = sortStrNumbers(els)
    alpha = sorted([el for el in els if not regex.search(r"\d", el)])
    return numeric + alpha


def getNumbers(element: str):
    numbs = regex.search("\d+", element)
    if numbs is None:
        return None
    return int(numbs.group())


def getUniqueName(*products: str):
    products = [p for p in products if not isEmpty(p)]
    if not any(products):
        return "UNK"
    if len(set(products)) == 1:
        return products[0]
    return "UNK"


def slidingWindow(elements: List, w_size: int = 2, step: int = 1):
    n_elements = len(elements)
    if n_elements <= w_size:
        yield tuple(elements)
    for win in range(0, n_elements - w_size + step, step):
        yield tuple(elements[win : win + w_size])
def loadEstacionComercial():
    with open(r"data\estaciones_HMI_comercial.json", "r", encoding="utf8") as f:
    # conectores = pd.json_normalize(json.load(f), max_level=10)
        puntosId = json.load(f)["data"]["list"]
    df = pd.DataFrame(puntosId)
    rename_columns = {
        "code": "Código",
        "name": "Nombre",
        "avmdld": "AVMDLD",
        "merchandise":"Mercancías",
        "suburban": "Cercanías",
        "national": "Nacional",
        "commercialArea":"Área Comercial",
        "commercial":"Comercial",
        "commercialMovements":"Movimientos Comercial",
        "sivType":"SIV"}
    df.rename(columns=rename_columns, inplace=True)
    return df
def loadEstacionSinCTC():
    with open(r"data\estaciones_HMI_topo_sin_ctc.json", "r", encoding="utf8") as f:
    # conectores = pd.json_normalize(json.load(f), max_level=10)
        puntosId = json.load(f)["pointIdsWithoutCTC"]
        df = pd.DataFrame(puntosId)
        rename_columns = {
            "pointId": "Código",
            "pointName":"Nombre", 
            "delegation": "Delegación",
            "trustedArrival":"ConfianzaLlegada",
            "trustedDeparture":"ConfianzaSalida",
            "trustedDepartureOrigin":"ConfianzaSalidaOrigen",
            "trustedTracks":"Confianza de seguimiento"}
        df = df.rename(columns=rename_columns)
        return df


###
# Info general de estaciones
###
def loadEstaciones():
    """
    Carga info de las estaciones según IHM topo
    """
    with open(r"data\estaciones_HMI_topo.json", "r", encoding="utf8") as f:
        # conectores = pd.json_normalize(json.load(f), max_level=10)
        conectores = json.load(f)["configCTCs"]
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
                    )
                )
                # cod2ctc.append((dep["pointId"], con['configCTCMetadata']['ctcName']))
    # cod2ctc = dict(cod2ctc)
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
            ],
        )
        .fillna("")
        .map(lambda x: x.strip())
    )
    return cod2ctc


def cargarPuntosRegulacion():
    pr = pd.read_csv("data/PuntosRegulación.csv", dtype="str", thousands=".")
    return pr


def cargarControlPoints():
    """
    Carga los ficheros xml controlPointTable
    """
    control_points = []
    for fname in Path(r"data/tablas auxiliares/").glob(r"controlPointTable*.xml"):
        tree = etree.parse(fname)
        root = tree.getroot()
        for cpoint in root.iterchildren():
            # info = dict()
            info = []
            info.extend(cpoint.items())
            for el in cpoint.iterchildren():
                info.append((el.tag.split("}")[-1], el.values()[0]))
            control_points.append(dict(info))
    control_points = (
        pd.DataFrame(control_points)
        .drop_duplicates(subset=["code", "shortDesc", "KMPoint"])
        .reset_index(drop=True)
    )
    return control_points


# Localizaciones
def loadLocalizaciones():
    """
    Carga localizaciones según API de circulaciones
    """
    # with open("data/info_estaciones.json", "r", encoding="utf8") as f:
    #     info_estaciones = json.load(f)
    # localizaciones = [
    #     {
    #         "Código": el["requestedStationInfo"]["stationInfo"]["stationCode"],
    #         "Nombre": el["requestedStationInfo"]["stationInfo"]["longName"],
    #         "Longitud": el["requestedStationInfo"]["stationInfo"]["location"][
    #             "longitude"
    #         ],
    #         "Latitud": el["requestedStationInfo"]["stationInfo"]["location"][
    #             "latitude"
    #         ],
    #     }
    #     for el in info_estaciones
    #     # if not el["requestedStationInfo"]["stationInfo"]["commuterNetwork"]
    #     # == "NO-COMERCIAL"
    # ]
    # localizaciones = pd.DataFrame(localizaciones)
    info_estaciones = pd.read_excel("data/info_estaciones.xlsx")
    info_estaciones["Código"] = info_estaciones["Código"].apply(lambda x: f"{x:0>5}")
    # localizaciones = info_estaciones.loc[
    #     info_estaciones["Tipo"] == "NATIONAL",
    #     ["Código", "Nombre", "Longitud", "Latitud"],
    # ]
    localizaciones = info_estaciones[["Código", "Nombre", "Longitud", "Latitud"]]
    return localizaciones


def setEF(s: str):
    """
    Establece la empresa ferroviaria en función del producto
    """
    if regex.search(
        r"(ALVIA|AVANT|AVE|CERCANIAS|INTERCITY|MD|REGIONAL EXPRES|TALGO|AVLO)", s
    ):
        return "RENFE"
    if regex.search(r"(IRYO)", s):
        return "IRYO"
    elif regex.search(r"(OUIGO)", s):
        return "OUIGO"
    else:
        return "Otro"


###
# Métodos de cálculo
###


def calcularVelocidades(df: pd.DataFrame):
    """
    Devuelve el df con diferencia de distancia y tiempo y la velocidad media por tramo
    """
    df = df.copy()
    df["Hora"] = df["Fecha"].dt.time
    df["dist_diff"] = df["DistanciaTotal (km)"].diff().fillna(0).round(2)
    df["hour_diff"] = df["Fecha"].diff().dt.total_seconds() / 3600
    df.loc[(df["dist_diff"] > 0) & (df["hour_diff"] == 0), "hour_diff"] = pd.NA
    df["hour_diff"] = df["hour_diff"].interpolate()
    df["VelocidadMedia (km/h)"] = (
        (df["dist_diff"] / df["hour_diff"])
        .round(2)
        .shift(-1, fill_value=0)
        .replace(0, pd.NA)
        .ffill()
        .round(2)
    )
    return df
