from pathlib import Path
from typing import List, Union

import requests
from tqdm.auto import tqdm


class GraylogAPIProcessor:
    def __init__(self):
        self.api_base_path = "http://grayloglocal.elcano.adif.es:9000/api/"

    # def getPlatformAnticipation(self, point_codes: List[str] = []):
    def getApiQuery(
        self,
        date_from: str,
        date_to: str,
        # seconds: int = 86400,
        point_codes: Union[str, List[str]] = [],
        source: str = "xsiv",
        pro: bool = True,
    ):
        """
        out: {"xsiv", "mie_mse"}
        """
        path = self.api_base_path + "search/universal/relative/export"

        # Transformamos la lista de estaciones y generamos la query
        query = []
        if isinstance(point_codes, str):
            point_codes = [point_codes]
        if point_codes:
            pcs = "(" + " OR :".join([f"pointCode:{el}" for el in point_codes]) + ")"
            query.append(pcs)
        if source == "xsiv":
            fields = "message"
            query.extend(
                [
                    'source:"/opt/appl/logs/mse/xsiv_published.log"',
                    "registerType:AUDITED",
                ]
            )
        elif source == "mie_mse":
            fields = "message"
            query.append(
                '(source:"/opt/appl/logs/mse/mie_mse_objectschamartin.log" OR source:"/opt/appl/logs/mse/mie_mse_objectsatocha.log")'
                # '(source:"/opt/appl/logs/mse/mie_mse_objectsvalencia.log" OR source:"/opt/appl/logs/mse/mie_mse_objectsvalenciacentronorte.log")'
                # '(source:"/opt/appl/logs/mse/mie_mse_objectsbilbao.log" OR source:"/opt/appl/logs/mse/mie_mse_objectsnor1.log")
            )
            query.append('"tren"')
        elif source == "sitra":
            fields = "message"
            query.append(
                '(source:"/opt/appl/logs/mse/xMSG_MSEDelegation.log")'
            )
        elif source =="ruOperation":
            fields = "message"
            query.append(
                '(source:"/opt/appl/logs/mse/xMSG_MSECentral.log")'
            )
        else:
            print(
                f"ERROR: source '{source}' incorrecto. Tiene que ser: {'xsiv', 'mie_mse'}"
            )
            # fields = "pointCode, pointName, technicalNumber, trainAction, platformCode, timestamp"

        if pro:
            query.append("collector_node_id:VILRMSE00?")
        else:
            query.append("NOT collector_node_id:VILRMSE00?")
            # "(trainAction:Approach OR trainAction:Arrival OR trainAction:Platform OR trainAction:Departure)",
            # "_exists_:trainProduct",
            # "platformSource:CTC",

        query = " AND ".join(query)
        print(query)

        queryParams = {
            "query": query,
            # "range": 432000,
            "timerange-absolute-from": date_from,
            "timerange-absolute-to": date_to,
            # "range": seconds,
            "batch_size": 500,
            "fields": fields,
        }
        return {
            "url": path,
            "params": queryParams,
            "auth": ("1kdpmaon8u8hhe6iimivo4469j8kbica778nse6r3pgjatb0l8ru", "token"),
        }

        # if not response.ok:
        #     raise Exception(f"Error api request code: {response.status_code}")

        # return response

    # def get_column_correct_order(self, wrong_cols, correct_cols):
    #     return [i for m in correct_cols for i, b in enumerate(wrong_cols) if m == b]

    def saveResponse(
        self,
        fname: Path,
        date_from: str,
        date_to: str,
        # seconds: int = 86400,
        point_codes: Union[str, List[str]] = [],
        source: str = "xsiv",
        pro: bool = True,
    ):
        """
        Guarda la respuesta en el formato deseado
        out: {"xsiv", "mie_mse"}
        """
        print("Requesting info...")
        response = requests.get(
            **self.getApiQuery(
                point_codes=point_codes,
                date_from=date_from,
                date_to=date_to,
                # seconds=seconds,
                source=source,
                pro=pro,
            ),
            stream=True,
        )
        if not response.ok:
            raise Exception(f"Error api request code: {response.status_code}")
        # if (not response.text) or (not response.status_code == 200):
        #     print(f"Error en la respuesta")
        #     return
        if not response.status_code == 200:
            print(f"Error en la respuesta")
            return
        if not response.encoding == "utf-8":
            response.encoding = "utf-8"

        ftype = fname.suffix
        if ftype == ".txt":
            # r = [el for el in response.text.split("\n") if el]
            # cols = r[0]
            # data = r[1:]

            # Save file
            # if not fname.exists():
            fname.parent.mkdir(parents=True, exist_ok=True)
            # with fname.open("w", encoding="utf8") as f:
            #     f.write("\n".join(el for el in data))

            # Sizes in bytes.
            total_size = int(response.headers.get("content-length", 0))
            block_size = 1024

            with fname.open("wb") as f:
                with tqdm(
                    total=total_size,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=f"Downloading in '{fname}'",
                ) as progress_bar:
                    for data in response.iter_content(block_size):
                        f.write(data)
                        progress_bar.update(len(data))
                        # file.write(data)

            if total_size != 0 and progress_bar.n != total_size:
                raise RuntimeError("No se ha podido descargar")

        # elif ftype == ".csv":
        #     cols_bien = [
        #         "timestamp",
        #         "pointCode",
        #         "pointName",
        #         "technicalNumber",
        #         "trainAction",
        #         "platformCode",
        #     ]
        #     # Separar y reordenar valores
        #     r = np.array(
        #         [GraylogApi.splitAndStrip(el) for el in response.text.split("\n") if el]
        #     )
        #     cols = r[0]
        #     data = r[1:]
        #     correct_order = self.get_column_correct_order(cols, cols_bien)

        #     # Remove duplicates
        #     split_rows = []
        #     for row in data[:, correct_order]:
        #         split_rows.append(tuple(row))
        #     split_rows = sorted(list(set(split_rows)), key=lambda x: x[0])

        #     # Save file
        #     if not fname.exists():
        #         fname.parent.mkdir(parents=True, exist_ok=True)
        #         with fname.open("w", encoding="utf8") as f:
        #             f.write(",".join(f'"{el}"' for el in cols_bien))
        #             for row in split_rows:
        #                 f.write("\n" + ",".join(f'"{el}"' for el in row))
