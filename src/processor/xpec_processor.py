from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from lxml import etree
from tqdm.auto import tqdm

from src.utils import (
    calcularVelocidades,
    isEmpty,
    loadEstaciones,
    loadLocalizaciones,
    localizeFecha,
    parallelizeFunction,
)


def str2timedelta(time_str: str):
    if isEmpty(time_str):
        return None
    # Split the string into days and time parts
    days_part, time_part = time_str.split("d ")
    days = int(days_part.split()[0])

    # Split the time part into hours, minutes, and seconds
    hours, minutes, seconds = map(int, time_part.split(":"))

    # Create a timedelta object
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


class XPECProcessor:
    def __init__(self):
        # Cargar info general de estaciones
        estaciones = loadEstaciones()
        self.map_codigo_ctc = dict(estaciones[["Código", "CTC"]].values)

        # Cargar localizaciones de estaciones
        localizaciones = loadLocalizaciones()
        self.map_codigo_estacion = dict(localizaciones[["Código", "Nombre"]].values)

    def getRegulation(self, service: str):
        service = etree.fromstring(service)
        service_info = []
        ntecnico = service.find("identificator").get("code")
        ncomercial = service.find("identificator").get("comercial_code")
        for reg in service.find("regulations").iterchildren():
            journey = []
            period = reg.find("period")
            p_inicio = period.get("start").split()[0]
            p_fin = period.get("end").split()[0]
            operador = reg.find("operator")
            # return reg
            op_info = {}
            for c in operador.iterchildren():
                if "code" in c.keys():
                    op_info[c.tag] = c.get("code")
                else:
                    op_info[c.tag] = c.get("code_1")
            # return op_info
            reg_days = reg.find("regulation/calendar/regular_days")
            for cp in reg.find("journey").iterchildren():
                control_point = {
                    "NTécnico": ntecnico,
                    "NComercial": ncomercial,
                    "regular_days": f'{reg_days.get("value"):0>7}',
                    "periodo_inicio": p_inicio,
                    "periodo_fin": p_fin,
                    **op_info,
                }
                control_point.update(dict(cp.items()))
                for el in cp.iterchildren():
                    control_point.update({f"{el.tag}_{k}": v for k, v in el.items()})
                journey.append(control_point)
            service_info.extend(journey)
        return service_info

    def readLogFile(self, fname: Path):
        """
        Devuelve todos los "journey" del xpec como DataFrame
        """
        service_info = []
        tree = etree.parse(fname)
        root = tree.getroot()
        servicios = parallelizeFunction(
            self.getRegulation, [etree.tostring(s) for s in root.iterchildren()]
        )
        # for service in root.iterchildren():
        #     ntecnico = service.find("identificator").get("code")
        #     ncomercial = service.find("identificator").get("comercial_code")
        #     for reg in service.find("regulations").iterchildren():
        #         journey = []
        #         period = reg.find("period")
        #         operador = reg.find("operator")
        #         op_comercial = reg.find("operator/comercial_association").get("code_1")
        #         reg_days = reg.find("regulation/calendar/regular_days")
        #         # if reg_days is not None:
        #         #     control_point["regular_days"] = reg_days.get("value")
        #         # else:
        #         #     control_point["regular_days"] = None
        #         for cp in reg.find("journey").iterchildren():
        #             control_point = {
        #                 "NTécnico": ntecnico,
        #                 "NComercial": ncomercial,
        #                 "regular_days": f'{reg_days.get("value"):0>7}',
        #                 "periodo_inicio": period.get("start").split()[0],
        #                 "periodo_fin": period.get("end").split()[0],
        #                 "op_comercial": op_comercial,
        #             }
        #             control_point.update(dict(cp.items()))
        #             for el in cp.iterchildren():
        #                 control_point.update(
        #                     {f"{el.tag}_{k}": v for k, v in el.items()}
        #                 )
        #             # if not control_point.get("type"):
        #             #     continue
        #             journey.append(control_point)
        #         service_info.extend(journey)
        for s in servicios:
            service_info.extend(s)
        return service_info

    def loadLogFile(self, fname: Path):
        """
        Devuelve un fichero XPEC en forma Dataframe
        """
        service_info = self.readLogFile(fname)
        # Limpiamos columnas
        xpec = pd.DataFrame(service_info)
        xpec_rename = {
            "NTécnico": "NTécnico",
            "NComercial": "NComercial",
            "company": "Empresa",
            "product": "Producto",
            "comercial_product": "ProductoComercial",
            "comercial_association": "AsociaciónComercial",
            # "traction_provider":"",
            "distance_to_previous": "DistanciaAnterior",
            "code": "Código",
            "type": "Tipo",
            "lineCode": "CódigoLinea",
            "lineCode_Complement": "ComplementoLinea",
            "order_value": "Secuencia",
            "times_arrival": "Llegada",
            "times_departure": "SalidaComercial",
            "times_technical_departure": "Salida",
            "comercial_value": "TipoComercial",
            "regular_days": "DíasActivos",
            "periodo_inicio": "PeriodoInicio",
            "periodo_fin": "PeriodoFin",
            # "op_comercial": "AsociaciónComercial",
        }
        xpec = xpec[list(xpec_rename.keys())].rename(columns=xpec_rename)
        xpec = (
            xpec.drop_duplicates(
                subset=[
                    "NTécnico",
                    "DistanciaAnterior",
                    "Código",
                    "Tipo",
                    "TipoComercial",
                    "Secuencia",
                    "PeriodoInicio",
                    "PeriodoFin",
                    "AsociaciónComercial",
                ]
            )
            .sort_values(by=["NTécnico", "Secuencia"], key=lambda x: x.astype(int))
            .reset_index(drop=True)
        )

        xpec[["SalidaComercial", "Salida", "Llegada"]] = xpec[
            ["SalidaComercial", "Salida", "Llegada"]
        ].map(str2timedelta)
        # Transformamos info
        xpec["Nombre"] = xpec["Código"].apply(self.map_codigo_estacion.get)
        xpec["Fecha"] = xpec[["Llegada", "Salida"]].apply(list, axis=1)
        xpec["Secuencia"] = xpec["Secuencia"].astype(int)
        # Distancia en hectómetros a kilómetros
        xpec["DistanciaAnterior"] = xpec["DistanciaAnterior"].astype(float) / 10
        return xpec

    def getLogsInfo(
        self,
        service_info: list[pd.DataFrame],
        ntecnicos: list[str] = [],
        fechas_inicio: list[date] = [],
    ) -> pd.DataFrame:
        xpec = pd.concat(service_info)
        if ntecnicos:
            xpec = xpec[xpec["NTécnico"].isin(ntecnicos)]
        xpec = (
            xpec.drop_duplicates(
                subset=[
                    "NTécnico",
                    "DistanciaAnterior",
                    "Código",
                    "Tipo",
                    "TipoComercial",
                    "Secuencia",
                    "PeriodoInicio",
                    "PeriodoFin",
                    "AsociaciónComercial",
                ]
            )
            .sort_values(by=["NTécnico", "Secuencia"], key=lambda x: x.astype(int))
            .reset_index(drop=True)
        )
        xpec[["PeriodoInicio", "PeriodoFin"]] = localizeFecha(
            xpec, ["PeriodoInicio", "PeriodoFin"], format="%Y-%m-%d"
        ).map(lambda x: x.date())

        # Repasamos todos los trenes individualmente
        subset_xpec = []
        for ntecnico in tqdm(xpec["NTécnico"].unique()):
            aux_xpec = xpec[xpec["NTécnico"] == ntecnico].copy()
            aux_xpec["DistanciaTotal (km)"] = (
                aux_xpec["DistanciaAnterior"].cumsum().round(2)
            )

            # Transformar tiempos
            # Borramos primera "llegada" y última "salida"
            aux_xpec = aux_xpec.explode("Fecha")[1:-1].reset_index(drop=True)
            aux_xpec["Movimiento"] = ["SALIDA", "LLEGADA"] * int(aux_xpec.shape[0] / 2)

            # Asignamos fecha real
            horarios = []
            for d in fechas_inicio[:-1]:
                dia = pd.to_datetime(d)
                # Comprobamos que sea día activo
                h = aux_xpec[
                    aux_xpec["DíasActivos"].apply(lambda x: x[d.weekday()] == "1")
                    & (aux_xpec["PeriodoInicio"] <= dia.date())
                    & (aux_xpec["PeriodoFin"] >= dia.date())
                ].copy()
                if h.empty:
                    continue
                h["FechaOrigen"] = dia
                h["Fecha"] = h["FechaOrigen"] + h["Fecha"]

                # # No quitamos las estaciones que no son punto de regulación
                # horario_base = horario_base[horario_base["Código"].isin(estaciones)]

                # Velocidades
                h = calcularVelocidades(h)
                # xpec["_invert"] = False
                h = h.drop(["Llegada", "Salida", "DistanciaAnterior"], axis=1)
                horarios.append(h)
            if len(horarios):
                aux_xpec = pd.concat(horarios)
                subset_xpec.append(aux_xpec)
        xpec = pd.concat(subset_xpec)
        xpec["Hora"] = xpec["Fecha"].dt.time
        xpec["FechaOrigen"] = xpec["FechaOrigen"].dt.date
        return xpec
