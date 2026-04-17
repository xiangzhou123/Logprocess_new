import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)
from datetime import timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import regex

from src.processor import LogProcessor
from src.utils import (
    formatTimedelta,
    # loadViasFromTopos,
    map_productos,
    parallelizeFunction,
)
from src.visualizacion.color_maps import map_product_color
from src.visualizacion.visualizaciones import setHoverInfo, setLayout


####
# ESTACIONES
####
def getMovimientosEstaciones(
    df_logs: pd.DataFrame,
    map_estacion_orden: dict[str, int],
    mov_sorter: dict[str, int],
):
    # Procesamos los movimientos de cada estación
    log_processor = LogProcessor()
    used_dfs: list[pd.DataFrame] = parallelizeFunction(
        log_processor.processStation,
        list(map_estacion_orden.keys()),
        df=df_logs,
        mov_sorter=mov_sorter,
        save_info=False,
        save_dir="",
        days="",
        list_rotaciones=[],
        hour_diff=0.5,
        leave=False,
        desc=f"Procesando estaciones",
        position=1,
    )
    # used_dfs = []
    # for e in estaciones:
    #     print(e)
    #     used_dfs.append(
    #         log_processor.processStation(
    #             e,
    #             df=use_df,
    #             mov_sorter=mov_sorter,
    #             save_info=False,
    #             save_dir="",
    #             days="",
    #             list_rotaciones=[],
    #             hour_diff=0.5,
    #         )
    #     )
    df_movimientos: pd.DataFrame = pd.concat(
        list(zip(*[el for el in used_dfs if el is not None]))[0]
    ).reset_index(drop=True)
    df_movimientos["ini_maniobra"] = ~df_movimientos[["ALTA", "LLEGADA"]].any(axis=1)
    df_movimientos["fin_maniobra"] = ~df_movimientos[["SALIDA"]].any(axis=1)

    df_aux = (
        df_movimientos[
            [
                "T1",
                "T2",
                "T_seq",
                "ProductoT1",
                "ProductoT2",
                "Código",
                "Estación",
                "Vía",
                "Movimiento",
                "Previsión",
                "Aproximación",
                "InicioOcupación",
                "FinOcupación",
                "VíaPlanificada",
                "LlegadaPlanificada",
                "SalidaPlanificada",
                "Fallo",
                "ini_maniobra",
                "fin_maniobra",
            ]
        ]
        .dropna(
            subset=[
                # "Previsión",
                "Aproximación",
                "InicioOcupación",
                "FinOcupación",
            ],
            how="all",
        )
        .copy()
    )
    df_aux = df_aux.rename(
        columns={"InicioOcupación": "Llegada", "FinOcupación": "Salida"}
    )
    df_aux["min_date"] = df_aux[
        [
            # "Previsión",
            # "Aproximación",
            "Llegada",
            "Salida",
        ]
    ].apply(
        lambda x: np.min([el for el in x if pd.notna(el)] if x.any() else None), axis=1
    )
    df_aux["st_order"] = df_aux.loc[:, "Código"].apply(map_estacion_orden.get)

    def getProduct(p1: str, p2: str):
        if not p1 or not p2:
            return p1 or p2
        if p1 == p2:
            return p1
        return "UNK"

    df_aux["Producto"] = (
        df_aux[["ProductoT1", "ProductoT2"]]
        .fillna("")
        .apply(lambda x: getProduct(*x), axis=1)
        .apply(map_productos.get)
    )
    df_aux["NTécnico"] = df_aux[["T1", "T2"]].apply(
        lambda x: x["T1"] if len(set(x)) == 1 else "→".join(x), axis=1
    )
    return df_aux


def getSequenciasEstaciones(df: pd.DataFrame):
    """
    Obtiene la secuencia de movimientos de cada tren a partir de un dataframe de movimientos por estación
    """
    sequence_groups = []
    for prod in df["Producto"].unique():
        for i, (t1, t2, min_date) in (
            df.loc[df["Producto"] == prod, ["T1", "T2", "min_date"]]
            .sort_values(by=["min_date"])
            .iterrows()
        ):
            included = False
            for group in sequence_groups:
                if (
                    t1 == group[0]
                    and (min_date - group[1]) < timedelta(hours=5)
                    and not included
                ):
                    group[0] = t2
                    group[1] = min_date
                    group[2].append(i)
                    included = True
                    break
            if not included:
                sequence_groups.append([t2, min_date, [i]])
    return sequence_groups


def getSeqTracesEstaciones(
    df: pd.DataFrame,
    sequence_groups: list,
    linea: str,
    map_nombre_orden: dict[str, int],
):
    # sequence_groups = getSequenciasEstaciones(df)

    traces = []
    map_vis = {k: [] for k in set(map_productos.values())}
    vis_all = []

    df["color"] = df["Producto"].apply(map_product_color.get)

    for i, (_, _, group) in enumerate(sequence_groups):
        use_df = df.loc[group].copy().replace([pd.NA], [None])

        df_rep = []
        for sub_df in np.array_split(
            use_df, np.where(use_df["Movimiento"] == "ROTACIÓN")[0]
        ):
            if sub_df.empty:
                continue
            # sub_df = pd.DataFrame(el, columns=use_df.columns)
            if sub_df.iloc[0]["st_order"] > sub_df.iloc[-1]["st_order"]:
                ascending = [True, False]
            else:
                ascending = [True, True]
            sub_df = sub_df.sort_values(
                by=["min_date", "st_order"], ascending=ascending
            )
            df_rep.append(sub_df)
        df_rep = pd.concat(df_rep)

        # if df_rep.iloc[0]["st_order"] > df_rep.iloc[-1]["st_order"]:
        #     ascending = [True, False]
        # else:
        #     ascending = [True, True]
        # df_rep = df_rep.sort_values(by=["min_date", "st_order"], ascending=ascending)

        x = []
        y = []
        markers = []
        t1 = df_rep["T1"].iloc[0]
        t2 = df_rep["T2"].iloc[-1]
        df_rep["T_seq"] = "→".join(df_rep["T_seq"].str.split("→").explode().unique())
        df_rep["T_seq"] = df_rep[["T_seq", "NTécnico"]].apply(
            lambda x: x["T_seq"].replace(x["NTécnico"], f"<b>{x['NTécnico']}</b>"),
            axis=1,
        )
        prod = df_rep["Producto"].iloc[0]
        if not t1 == t2:
            name = t1 + "→" + t2
        else:
            name = t1
        df_rep["Nombre"] = name
        hover_info = []
        hover_cols = [
            "Nombre",
            "NTécnico",
            "T_seq",
            "Código",
            "Estación",
            "Vía",
            "Producto",
            "Movimiento",
        ]

        x.append(None)
        y.append(None)
        markers.append("line-ew")
        hover_info.append(None)

        color = df_rep["color"].dropna().iloc[0]
        traces.append(
            go.Scatter(
                x=df_rep.dropna(subset=["Previsión"])["Previsión"],
                y=df_rep.dropna(subset=["Previsión"])["st_order"],
                visible=True,
                mode="lines+markers",
                line=dict(color=color, dash="dot"),  # , width=width),
                marker_color=color,
                marker_symbol="diamond-tall",
                marker_size=10,
                hoverinfo="text",
                hovertext=setHoverInfo(
                    df_rep.dropna(subset=["Previsión"]), hover_cols + ["Previsión"]
                ),
                name=name,
                showlegend=False,
                # legend=map_prod_legend.get(prod),
                legendgroup=f"{i}",
                # legendgrouptitle={"text": prod},
            )
        )

        for _, row in df_rep.iterrows():
            st = row["st_order"]
            # seq = row["T_seq"]
            # pre = row["Previsión"]
            app = row["Aproximación"]
            arr = row["Llegada"]
            dep = row["Salida"]
            mov = row["Movimiento"]
            # color = row["color"]
            name = row["Nombre"]
            ini_maniobra = row["ini_maniobra"]
            fin_maniobra = row["fin_maniobra"]

            if app:
                x.append(app)
                y.append(st)
                markers.append("arrow-left")
                hover_info.append(
                    setHoverInfo(
                        pd.DataFrame(row).T, hover_cols + ["Aproximación"]
                    ).iloc[0]
                )

            if arr:
                x.append(arr)
                y.append(st)
                if ini_maniobra:
                    markers.append("square-open")
                else:
                    markers.append("square")
                hover_info.append(
                    setHoverInfo(pd.DataFrame(row).T, hover_cols + ["Llegada"]).iloc[0]
                )
            if dep:
                x.append(dep)
                y.append(st)
                if fin_maniobra:
                    markers.append("arrow-right-open")
                else:
                    markers.append("arrow-right")
                hover_info.append(
                    setHoverInfo(pd.DataFrame(row).T, hover_cols + ["Salida"]).iloc[0]
                )
        traces.append(
            go.Scatter(
                x=x,
                y=y,
                visible=True,
                mode="lines+markers",
                line=dict(color=color),  # , width=width, dash=dash),
                marker_color=color,
                marker_symbol=markers,
                marker_size=10,
                hoverinfo="text",
                hovertext=pd.Series(hover_info),
                name=name,
                showlegend=True,
                # legend=map_prod_legend.get(prod),
                legendgroup=f"{i}",
                # legendgrouptitle={"text": prod},
            )
        )
        vis_all.extend([True, True])
        for k in map_vis.keys():
            if prod == k:
                map_vis[k].extend([True, True])
            else:
                map_vis[k].extend([False, False])

    buttons = []
    for k, v in map_vis.items():
        button = dict(label=k, method="restyle", args=["visible", v])
        buttons.append(button)

    buttons = [
        {
            "label": "Todo",
            "method": "restyle",
            "args": ["visible", vis_all],
        }
    ] + buttons

    layout = setLayout("togglegroup", f"{linea}", map_nombre_orden)
    fig = go.Figure(data=traces, layout=layout)
    # Añadimos los botones
    fig.update_layout(
        updatemenus=[dict(type="dropdown", direction="down", buttons=buttons)],
    )
    return fig


####
# ELEMENTOS
####
def getMovType(mov_ts: str):
    llegada = r"(LLEGADA_)"
    fin = r"(BAJA_)"
    alta = r"(ALTA_)"
    salida = r"(SALIDA(_)?)"
    if regex.search(rf"\b{llegada}+{salida}+\b", mov_ts):
        return "PASO"
    elif regex.search(rf"\b{alta}+{salida}+\b", mov_ts):
        return "ORIGEN"
    elif regex.search(rf"\b{llegada}*{fin}+\b", mov_ts):
        return "FIN"
    else:
        return "UNK"


def getSecuenciaElementos(df_logs: np.ndarray, tren: str):
    df_mov = (
        df_logs[df_logs["NTécnico"] == tren]
        .sort_values(by=["Fecha", "st_mov"])
        .reset_index(drop=True)
    )
    cols = df_mov.columns
    col_fecha = np.where(cols == "Fecha")[0][0]

    # Los movimientos de un día
    mov1 = pd.DataFrame(
        np.split(
            df_mov.values,
            np.where(df_mov["Día"].diff() > timedelta(0))[0],
            axis=0,
        )[0],
        columns=cols,
    )
    mov1 = mov1.sort_values(by=["Mnemónico", "Elemento", "st_mov", "Fecha"])
    mov1["tdiff"] = mov1["Fecha"].diff()
    if mov1.empty:
        return

    # Obtener splits de cada elemento
    df_split = np.split(
        mov1.values[:, :-1],
        np.where(
            (~mov1["Mnemónico"].eq(mov1["Mnemónico"].shift()))
            | (~mov1["Elemento"].eq(mov1["Elemento"].shift()))
            | (~(mov1["tdiff"] < timedelta(hours=4)))
        )[0][1:],
    )
    df_split = sorted(df_split, key=lambda x: x[0][col_fecha])

    # Obtenemos la secuencia completa de movimientos
    secuencias = []
    for split in df_split:
        info = {}
        df_t = pd.DataFrame(split, columns=cols)
        df_t = df_t.sort_values(by="Fecha")
        info["NTécnico"] = tren
        info["Código"] = df_t.iloc[0]["Código"]
        info["Nombre"] = df_t.iloc[0]["Nombre"]
        info["Mnemónico"] = df_t.iloc[0]["Mnemónico"]
        info["Elemento"] = df_t.iloc[0]["Elemento"]
        sentido = df_t["Sentido"].unique()
        info["Sentido"] = sentido[0] if len(sentido) == 1 else sentido
        mov = df_t["Movimiento"].values
        info["Mov_seq"] = "_".join(mov)
        info["Movimiento"] = getMovType(info["Mov_seq"])
        info["ALTA"] = (
            df_t.loc[df_t["Movimiento"] == "ALTA", "Fecha"].iloc[0]
            if "ALTA" in mov
            else pd.NaT
        )
        info["OCUPA"] = (
            df_t.loc[df_t["Movimiento"] == "LLEGADA", "Fecha"].iloc[0]
            if "LLEGADA" in mov
            else pd.NaT
        )
        info["BAJA"] = (
            df_t.loc[df_t["Movimiento"] == "BAJA", "Fecha"].iloc[0]
            if "BAJA" in mov
            else pd.NaT
        )
        info["LIBERA"] = (
            df_t.loc[df_t["Movimiento"] == "SALIDA", "Fecha"].iloc[0]
            if "SALIDA" in mov
            else pd.NaT
        )
        secuencias.append(info)
    secuencia_tren = pd.DataFrame(secuencias)
    secuencia_tren["TiempoDiferencia (segundos)"] = (
        secuencia_tren["LIBERA"] - secuencia_tren["OCUPA"].shift(-1)
    ).dt.total_seconds()
    secuencia_tren["TiempoDiferencia"] = secuencia_tren[
        "TiempoDiferencia (segundos)"
    ].apply(formatTimedelta)
    return secuencia_tren
