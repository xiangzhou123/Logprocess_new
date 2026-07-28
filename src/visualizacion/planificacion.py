import pandas as pd
import plotly.graph_objects as go

from src.utils import range_normalization, sortElements

from .color_maps import sample_random_colors
from .utils import setHoverInfo, setLayout

pd.set_option("future.no_silent_downcasting", True)


###
# PLANIFICACIONES
###


def build_hierarchical_dataframe(
    df: pd.DataFrame,
    levels: list[str],
    value_column: str,
    color_columns=None,
    top_node="total",
):
    """
    Build a hierarchy of levels for Sunburst or Treemap charts.

    Levels are given starting from the bottom to the top of the hierarchy,
    ie the last level corresponds to the root.
    """
    df_list = []
    for i, level in enumerate(levels):
        df_tree = pd.DataFrame(columns=["id", "label", "parent", "value", "color"])
        dfg = df.groupby(levels[i:]).sum()
        dfg = dfg.reset_index()
        df_tree["label"] = dfg[level].copy()
        df_tree["id"] = dfg[levels[i:]].apply(lambda x: "_".join(x), axis=1).copy()
        if i < len(levels) - 1:
            df_tree["parent"] = (
                dfg[levels[i + 1 :]].apply(lambda x: "_".join(x), axis=1).copy()
            )
        else:
            df_tree["parent"] = top_node
        df_tree["value"] = dfg[value_column]
        df_tree["color"] = dfg[color_columns[0]] / dfg[color_columns[1]]
        df_list.append(df_tree)
    total = pd.DataFrame(
        [
            dict(
                id=top_node,
                label=top_node,
                parent="",
                value=df[value_column].sum(),
                color=df[color_columns[0]].sum() / df[color_columns[1]].sum(),
            ),
        ]
    )
    df_list.append(total)
    df_all_trees = pd.concat(df_list, ignore_index=True)
    return df_all_trees


def mostrarConfusionTree(
    df: pd.DataFrame,
    title: str,
    levels: list[str],
    value_column: str,
    color_columns=None,
    top_node="total",
):
    """
    Genera un diagrama de sankey para confusión

    origen: str
        Columna origen
    destino: str
        Columna destino
    size: str
        Columna con el tamaño de la relación
    """
    df_all_trees = build_hierarchical_dataframe(
        df,
        levels,
        value_column,
        color_columns,
        top_node=top_node,
    )
    df_all_trees["color"] = df_all_trees["color"].fillna(0)
    df_all_trees["color"] = df_all_trees["color"] * 100

    traces = []
    traces.append(
        go.Treemap(
            ids=df_all_trees["id"],
            labels=df_all_trees["label"],
            parents=df_all_trees["parent"],
            values=df_all_trees["value"],
            branchvalues="total",
            marker=dict(
                colors=df_all_trees["color"],
                colorscale="rdylgn",
                cmid=50,
                cmax=100,
                cmin=40,
            ),
            hovertemplate="<b>%{label} </b> <br> Movimientos: %{value}<br> Correctos: %{color:.2f}%",
            name="",
            maxdepth=3,
            # pathbar_textfont_size=50,
            # textfont_size=20,
        )
    )
    fig = go.Figure(traces)
    fig.update_layout(
        title=title,
        margin=dict(t=50, l=25, r=25, b=25),
    )
    return fig


def mostrarConfusionSankey(
    df: pd.DataFrame, title: str, origen: str, destino: str, size: str
):
    """
    Genera un diagrama de sankey para confusión

    origen: str
        Columna origen
    destino: str
        Columna destino
    size: str
        Columna con el tamaño de la relación
    """
    df = df.rename(columns={size: "Total"}).copy()

    # Origen
    group_src = (
        df[[origen, destino, "Total"]]
        .groupby(origen)
        .agg({destino: "size", "Total": "sum"})
        .reset_index()
        .rename(columns={destino: "Destinos"})
        .copy()
    )
    _ord_map = {k: v for v, k in enumerate(sortElements(group_src[origen].values))}
    group_src["_ord"] = group_src[origen].apply(_ord_map.get)
    group_src = group_src.sort_values(by="_ord").reset_index(drop=True)
    group_src["_c_sum"] = group_src["Total"].cumsum()
    group_src["x_pos"] = 0.2
    group_src["y_pos"] = range_normalization(group_src["_c_sum"])
    hover_cols = [origen, "Destinos", "Total"]
    group_src["hover_text"] = setHoverInfo(group_src, hover_cols)

    # Destino
    group_dst = (
        df[[origen, destino, "Total"]]
        .groupby(destino)
        .agg({origen: "size", "Total": "sum"})
        .reset_index()
        .rename(columns={origen: "Orígenes"})
        .copy()
    )
    _ord_map = {k: v for v, k in enumerate(sortElements(group_dst[destino].values))}
    group_dst["_ord"] = group_dst[destino].apply(_ord_map.get)
    group_dst = group_dst.sort_values(by="_ord").reset_index(drop=True)
    group_dst["_c_sum"] = group_dst["Total"].cumsum()
    group_dst["x_pos"] = 0.8
    group_dst["y_pos"] = range_normalization(group_dst["_c_sum"])
    hover_cols = [destino, "Orígenes", "Total"]
    group_dst["hover_text"] = setHoverInfo(group_dst, hover_cols)

    # Nodos
    nodes = pd.concat(
        [
            group_src[[origen, "x_pos", "y_pos", "hover_text"]].rename(
                columns={origen: "Elemento"}
            ),
            group_dst[[destino, "x_pos", "y_pos", "hover_text"]].rename(
                columns={destino: "Elemento"}
            ),
        ],
        ignore_index=True,
    ).reset_index()
    elementos = nodes["Elemento"].unique()
    cmap_elementos = sample_random_colors(elementos)
    nodes["color"] = nodes["Elemento"].apply(cmap_elementos.get)

    # Enlaces
    hover_cols = [origen, destino, "Total"]
    hover_text_link = setHoverInfo(df, hover_cols)

    map_group_src = {
        k: v for v, k in enumerate(sortElements(group_src[origen].unique().tolist()))
    }
    map_group_dst = {
        k: v
        for v, k in enumerate(
            sortElements(group_dst[destino].unique().tolist()),
            start=len(map_group_src),
        )
    }

    traces = [
        go.Sankey(
            arrangement="snap",
            node=dict(
                pad=10,
                thickness=30,
                label=nodes["Elemento"],
                x=nodes["x_pos"],
                y=nodes["y_pos"],
                color=nodes["color"],
                align="left",
                customdata=nodes["hover_text"],
                hovertemplate="%{customdata}",
            ),
            link=dict(
                arrowlen=20,
                source=df[origen].apply(map_group_src.get),
                target=df[destino].apply(map_group_dst.get),
                value=df["Total"],  # .apply(np.log10),
                customdata=hover_text_link,
                hovertemplate="%{customdata}",
                hovercolor=[cmap_elementos[v] for v in df[origen]],
            ),
        )
    ]

    layout = setLayout(
        "togglegroup",
        title=title,
    )
    annotations = [
        {
            "xref": "paper",
            "yref": "paper",
            "x": 0.17,
            "y": -0.2,
            "text": "Planificación",
            "showarrow": False,
            "font": {"size": 15, "color": "black"},
        },
        {
            "xref": "paper",
            "yref": "paper",
            "x": 0.81,
            "y": -0.2,
            "text": "Real",
            "showarrow": False,
            "font": {"size": 15, "color": "black"},
        },
    ]
    fig = go.Figure(data=traces, layout=layout)
    fig = fig.update_layout(
        autosize=False,
        width=1200,
        height=500,
        title_x=0.5,
        margin=dict(l=0, r=0),
        annotations=annotations,
    )
    return fig
