import asyncio
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import faicons as fa
import numpy as np
import pandas as pd
import regex
from shiny import App, Inputs, Outputs, Session, reactive, render, req, ui

# from shiny.express import input, ui
from shinywidgets import output_widget, render_widget

from src.processor import LogProcessor
from src.utils import (
    formatTimedelta,
    # getFilesByDate,
    # map_name2use_name,
    # rellenarId,
    # map_cod2name,
    map_use_name2name,
    parallelizeFunction,
    sortStrNumbers,
)
from src.visualizacion.visualizaciones import (
    planificacionVias,
    visualizacionOcupacionVia,
    visualizacionSaturacionVia,
)

pd.set_option("future.no_silent_downcasting", True)

ICONS = {
    "user": fa.icon_svg("user", "regular"),
    "gear": fa.icon_svg("gear"),
    "train": fa.icon_svg("train"),
    "clock": fa.icon_svg("clock"),
    "map-pin": fa.icon_svg("map-pin"),
    "loading": fa.icon_svg("spinner").add_class(
        "fa fa-spinner fa-pulse fa-3x fa-align-right"
    ),
    "download": fa.icon_svg("download"),
}

LOADING_STEPS = 15


dir_data = Path(r"data/shiny/xsiv/PRO")

grandes = list(dir_data.iterdir())
# grandes_names = [el.name for el in grandes]
grandes_names = {
    k: v
    for k, v in sorted(
        [
            (
                map_use_name2name.get(regex.split(r"\d{5}", fname.name)[-1].strip()),
                fname,
            )
            for fname in grandes
        ],
        key=lambda x: x[1],
    )
}
today = datetime.now().date()
log_processor = LogProcessor()

app_ui = ui.page_fluid(
    ui.page_navbar(
        # ui.nav_spacer(),
        ui.nav_control(ui.input_dark_mode(mode="light")),
        # title="Dark mode switch in navbar",
    ),
    ui.layout_columns(
        ui.layout_columns(
            ui.card(
                ui.card(
                    ui.layout_columns(
                        ui.input_date_range(
                            "daterange",
                            "Fecha",
                            # start=today,
                            # end=today,
                            start="2024-06-10",
                            end="2024-06-10",
                            language="es",
                        ),
                        ui.input_select(
                            "selectEnclavamiento",
                            "Enclavamiento",
                            list(grandes_names.keys()),
                            selected=list(grandes_names.keys())[-1],
                        ),
                    ),
                    ui.layout_columns(
                        ui.input_select(
                            "checkbox_vias",
                            "Vías",
                            [],
                            multiple=True,
                            size=6,
                        ),
                        ui.input_radio_buttons(
                            "tipo_vias",
                            "Tipo vías",
                            ["Todo", "AV", "RC"],
                        ),
                        # ui.input_action_button("actualizar_datos", "Actualizar"),
                    ),
                ),
            ),
            ui.card(
                ui.value_box(
                    "Trenes",
                    ui.output_ui("updateKPItrenes"),
                    showcase=ICONS["train"],
                    # theme="bg-gradient-orange-red",
                    # full_screen=True,
                ),
                ui.value_box(
                    f"Vías",
                    ui.output_ui("updateKPIvias"),
                    showcase=ICONS["map-pin"],
                    # full_screen=True,
                ),
                ui.value_box(
                    "Ocupación media",
                    ui.output_ui("updateKPIocupacion"),
                    showcase=ICONS["clock"],
                    # full_screen=True,
                ),
                full_screen=True,
            ),
        ),
        ui.input_action_button("actualizar_vista", "Actualizar vista"),
        ui.navset_pill(
            ui.nav_panel(
                "Ocupación",
                ui.card(
                    ui.card_header(
                        ui.popover(
                            ui.span(
                                ICONS["gear"],
                                style="position:absolute; top: 5px; right: 7px;",
                            ),
                            ui.download_button(
                                "downloadOcupacion", "", icon=ICONS["download"]
                            ),
                            ui.input_checkbox_group(
                                "visualizar_ocupacion",
                                "Visualizar",
                                [
                                    # "Ocupación",
                                    "Libre",
                                    "Planificación",
                                    "Fallos",
                                ],
                                # selected=["Ocupación", "Libre", "Planificación", "Fallos"],
                            ),
                            ui.card(
                                ui.card_header("Tiempo ocupación"),
                                ui.input_numeric(
                                    "t_range_occ_min",
                                    "Mínimo",
                                    value=0,
                                    min=0,
                                    max=0,
                                    step=1,
                                ),
                                ui.input_numeric(
                                    "t_range_occ_max",
                                    "Máximo",
                                    value=0,
                                    min=0,
                                    max=0,
                                    step=1,
                                ),
                            ),
                            ui.input_numeric(
                                "margen_ocupacion",
                                "Tiempo de margen (en minutos) entre ocupaciones.",
                                10,
                            ),
                            ui.input_numeric(
                                "t_min_ocupacion",
                                "Tiempo mínimo de ocupación (en minutos).",
                                5,
                            ),
                            placement="right",
                            id="card_popover_ocupacion",
                        ),
                    ),
                    ui.value_box(
                        None,
                        output_widget("plotOcupacion"),
                        # theme="bg-gradient-orange-red",
                        full_screen=True,
                    ),
                ),
            ),
            ui.nav_panel(
                "Saturación",
                ui.card(
                    ui.card_header(
                        ui.popover(
                            ui.span(
                                ICONS["gear"],
                                style="position:absolute; top: 5px; right: 7px;",
                            ),
                            ui.input_numeric(
                                "periodo_horas_saturacion",
                                "Periodos (horas)",
                                1,
                            ),
                            ui.input_numeric(
                                "margen_saturacion",
                                "Tiempo de margen (en minutos) entre ocupaciones.",
                                0,
                            ),
                            ui.input_numeric(
                                "t_min_saturacion",
                                "Tiempo mínimo de ocupación (en minutos).",
                                5,
                            ),
                            # ui.download_button("downloadSaturacion", "Descargar"),
                            placement="right",
                            id="card_popover_saturacion",
                        ),
                    ),
                    ui.value_box(
                        None,
                        output_widget("plotSaturacion"),
                        # theme="bg-gradient-orange-red",
                        full_screen=True,
                    ),
                ),
            ),
            ui.nav_panel(
                "Planificación vías",
                ui.card(
                    ui.card_header(
                        ui.popover(
                            ui.span(
                                ICONS["gear"],
                                style="position:absolute; top: 5px; right: 7px;",
                            ),
                            ui.input_radio_buttons(
                                "confusionRadioSelect",
                                "",
                                ["Absoluto", "Relativo"],
                                selected="Absoluto",
                            ),
                            placement="right",
                            id="card_popover_planificacion",
                        ),
                    ),
                    ui.value_box(
                        None,
                        output_widget("plotConfusion"),
                        # theme="bg-gradient-orange-red",
                        full_screen=True,
                    ),
                ),
            ),
            ui.nav_panel(
                "Datos",
                ui.card(
                    ui.card_header(
                        "",
                        ui.popover(
                            ui.span(
                                ICONS["gear"],
                                style="position:absolute; top: 5px; right: 7px;",
                            ),
                            ui.download_button("downloadDatos", "Descargar"),
                            placement="right",
                            id="card_popover_datos",
                        ),
                    ),
                    ui.output_data_frame("updateDatos"),
                ),
            ),
            # id="tab",
            # output_widget("plot"),
        ),
        col_widths=[12, 12],
        # row_heights=[1, 1],
    ),
)


def server(input: Inputs, output: Outputs, session: Session):
    session.df = None
    session.vias = []
    session.tipo_vias = ""
    _loading = reactive.value(False)
    n_trenes = reactive.value(0)
    n_vias = reactive.value(0)
    t_occ_medio = reactive.value("")

    #######################################
    ################ DATOS ################
    #######################################

    def cargarDatos(
        station_name: str,
        # start: str = "2024-02-19",
        # end: str = "2024-02-20",
    ):
        # start = pd.to_datetime(start)
        # end = pd.to_datetime(end)
        # use_station = np.where(np.array(grandes_names) == station_name)[0][0]
        # # files = getFilesByDate(grandes[use_station], start=start, end=end)
        # files = list(grandes[use_station].rglob("*.*"))
        use_station = grandes_names[station_name]
        files = list(use_station.rglob("*.*"))
        if not files or files is None:
            return
        # files, _ = list(zip(*files))
        files = [el for el in files if el.suffix == ".xlsx"]
        dfs = [
            el.dropna(axis=1, how="all")
            for el in parallelizeFunction(pd.read_excel, files, show_progress=False)
            if el is not None and not el.empty
        ]
        if not dfs:
            return
        # for el in dfs:
        #     if el.isna().all(axis=0).any():
        #         print(el.head())
        #         print(el.isna().all(axis=0))
        df = pd.concat(dfs).drop_duplicates().reset_index(drop=True).copy()
        df["Ocupación (segundos)"] = (
            (df["FinOcupación"] - df["InicioOcupación"]).dt.total_seconds().fillna(0)
        )
        df["Ocupación planificada (segundos)"] = (
            (df["SalidaPlanificada"] - df["LlegadaPlanificada"])
            .dt.total_seconds()
            .fillna(0)
        )
        fechas_movs = df[
            ["ALTA", "APROXIMACIÓN", "MANIOBRALLEGADA", "LLEGADA", "SALIDA"]
            + ["MANIOBRASALIDA", "FIN", "BAJA", "MANIOBRA"]
        ].apply(pd.to_datetime)
        df["Inicio"] = fechas_movs.min(axis=1)
        df["Fin"] = fechas_movs.max(axis=1)

        # Formatos
        # df[["T1", "T2"]] = pd.concat(
        #     parallelizeFunction(
        #         lambda x: x.map(rellenarId),
        #         data=np.array_split(
        #             df[["T1", "T2"]],
        #             len(df) // np.min((len(df), 1000)),
        #         ),
        #         show_progress=True,
        #         desc="Formateando fechas.",
        #         output="series",
        #     )
        # )
        for c in ["Vía", "VíaPlanificada"]:
            # print(df[c].dtype, type(df[c].dtype))
            if not isinstance(df[c].dtype, np.dtypes.ObjectDType):
                # print(c)
                df = df.rename(columns={c: "_aux"})
                df[c] = None
                idx = df.loc[df["_aux"].notna(), "_aux"].index
                df.loc[idx, c] = df.loc[idx, "_aux"].astype(int).astype(str)
                df.drop(["_aux"], axis=1, inplace=True)

        return df

    @reactive.effect
    @reactive.event(input.selectEnclavamiento)
    async def cargarEnclavamiento():
        session.enclavamiento = input.selectEnclavamiento()
        print(f"ACTIVAR: {session.enclavamiento}")
        # Obtener figura
        coro = asyncio.to_thread(cargarDatos, session.enclavamiento)
        task = asyncio.create_task(coro)
        await asyncio.sleep(0)
        print("Cargando enclavamiento")
        m = ui.modal(
            ui.layout_columns(
                ui.img(ICONS["loading"]),
                col_widths=[7],
            ),
            title="Cargando enclavamiento",
            footer=None,
        )
        ui.modal_show(m)
        session.df = await task
        if session.df is not None and not session.df.empty:
            session.vias = sortStrNumbers(session.df["Vía"].unique())
        else:
            session.vias = []
        ui.update_select(
            "checkbox_vias", label="Vías", choices=session.vias, selected=session.vias
        )
        # _loading.set(False)
        ui.modal_remove()

    @reactive.effect
    @reactive.event(input.daterange, input.checkbox_vias, input.tipo_vias)
    def filterDF():
        if session.df is None or session.df.empty:
            return {}

        # Fecha
        fecha_ini, fecha_end = input.daterange()
        fecha_ini = pd.to_datetime(fecha_ini)
        fecha_end = pd.to_datetime(fecha_end)
        if fecha_ini == fecha_end:
            fecha_end = fecha_end + timedelta(days=1)

        # Vías
        vias = input.checkbox_vias()
        tipo_vias = input.tipo_vias()
        if tipo_vias == "Todo":
            tipo_vias = ["AV", "RC"]
            session.tipo_vias = "todo"
        else:
            tipo_vias = [tipo_vias]
            session.tipo_vias = tipo_vias

        print(f"Filter: {fecha_ini} - {fecha_end}. {vias}, {tipo_vias}")
        session.filt_df = session.df[
            (session.df["Inicio"] >= fecha_ini)
            & (session.df["Fin"] >= fecha_ini)
            & (session.df["Inicio"] <= fecha_end)
            & (session.df["Fin"] <= fecha_end)
            & (session.df["Vía"].isin(vias))
            & (session.df["TipoVía"].isin(tipo_vias))
        ]

        # Actualizamos información que mostramos
        n_trenes.set(len(session.filt_df["T1"].unique()))
        n_vias.set(len(session.filt_df["Vía"].unique()))
        t_occ_medio.set(session.filt_df["Ocupación (segundos)"].fillna(0).mean())

        # Actualizar inputs
        if session.filt_df.empty:
            occ_min = 0
            occ_max = 0
        else:
            occ_min = session.filt_df["Ocupación (segundos)"].fillna(0).min()
            occ_max = session.filt_df["Ocupación (segundos)"].fillna(0).max()
        ui.update_numeric("t_range_occ_min", value=occ_min, min=occ_min, max=occ_max)
        ui.update_numeric("t_range_occ_max", value=occ_max, min=occ_min, max=occ_max)

    #######################################
    ############### VALORES ###############
    #######################################

    @render.text
    def updateKPItrenes():
        value = n_trenes.get()
        if pd.notna(value) and value:
            return f"{value}"
        return "0"

    @render.text
    def updateKPIvias():
        value = n_vias.get()
        if pd.notna(value) and value:
            return f"{value}"
        return "0"

    @render.text
    def updateKPIocupacion():
        value = t_occ_medio.get()
        if pd.notna(value) and value:
            # return f"{value:.0f} segundos"
            return f"{formatTimedelta(value)}"
        return "0 segundos"

    @render.text
    def updateTitulo():
        return f"{session.enclavamiento}"

    @render.data_frame
    def updateDatos():
        return session.filt_df

    #######################################
    ################ PLOTS ################
    #######################################

    def getOcupacion():
        visualizar = input.visualizar_ocupacion()
        margen = input.margen_ocupacion()
        t_min = input.t_min_ocupacion()
        t_range_occ_min = input.t_range_occ_min()
        t_range_occ_max = input.t_range_occ_max()
        print(visualizar)
        if "Libre" in visualizar:
            df_free = (
                log_processor.getTramosLibres(session.filt_df, margen, t_min)
                .sort_values(by=["InicioLibre"])
                .reset_index(drop=True)
            )
        else:
            df_free = None

        use_df = session.filt_df[
            (session.filt_df["Ocupación (segundos)"] >= t_range_occ_min)
            & (session.filt_df["Ocupación (segundos)"] <= t_range_occ_max)
        ]

        fig_occ = visualizacionOcupacionVia(
            use_df,
            title=f"Ocupación de vías <b>{session.enclavamiento}</b>",
            df_free=df_free,
            show_plan="Planificación" in visualizar,
            show_fail="Fallos" in visualizar,
        )
        _loading.set(False)
        return fig_occ

    def getSaturation():
        margen = input.margen_saturacion()
        t_min = input.t_min_saturacion()
        hour_period = input.periodo_horas_saturacion()
        min_date = pd.to_datetime(
            (session.filt_df["Fin"].min() - timedelta(hours=0.5)).strftime(
                "%Y-%m-%d %H"
            )
        ) - timedelta(hours=1)
        max_date = pd.to_datetime(
            (session.filt_df["Fin"].max() + timedelta(hours=0.5)).strftime(
                "%Y-%m-%d %H"
            )
        ) + timedelta(hours=1)

        df_free = (
            log_processor.getTramosLibres(session.filt_df, margen, t_min)
            .sort_values(by=["InicioLibre"])
            .reset_index(drop=True)
        )

        # Obtenemos la saturación de las vías
        saturation = log_processor.getSaturation(
            session.filt_df, min_date, max_date, hour_period=hour_period, margen=margen
        )

        # Generamos visualización
        if df_free is not None:
            saturation_libre = log_processor.getSaturation(
                df_free.rename(
                    columns={
                        "InicioLibre": "InicioOcupación",
                        "FinLibre": "FinOcupación",
                    }
                ),
                min_date,
                max_date,
                hour_period=hour_period,
                margen=0,
                modo="libre",
            )
        else:
            saturation_libre = None
        fig_sat = visualizacionSaturacionVia(
            saturation,
            saturation_libre,
            title=f"Saturación de vías en <b>{session.enclavamiento}</b> (periodos {hour_period}h)",
        )
        _loading.set(False)
        return fig_sat

    @render_widget
    @reactive.event(input.actualizar_vista)
    async def plotOcupacion():
        # _loading.set(~_loading.get())
        _loading.set(True)
        # print(session.filt_df)
        if session.filt_df is None or session.filt_df.empty:
            return None
        fig_occ = None
        counter = 0
        # Obtener figura
        coro = asyncio.to_thread(getOcupacion)
        task = asyncio.create_task(coro)
        await asyncio.sleep(0)
        print("task")
        with ui.Progress(min=1, max=LOADING_STEPS) as p:
            while _loading.get():
                counter += 1
                p.set(counter, message="Generando vista")
                await asyncio.sleep(0.1)
                if counter >= LOADING_STEPS:
                    counter = 0

        # ui.update_select(
        #     "checkbox_vias", label="Vías", choices=session.vias, selected=session.vias
        # )

        fig_occ = await task
        return fig_occ

    @render_widget
    @reactive.event(input.actualizar_vista)
    async def plotSaturacion():
        print("plotSaturacion")
        # print(session.filt_df)
        _loading.set(True)
        if session.filt_df is None or session.filt_df.empty:
            return None
        fig_sat = None
        counter = 0
        # Obtener figura
        coro = asyncio.to_thread(getSaturation)
        task = asyncio.create_task(coro)
        await asyncio.sleep(0)
        print("task")

        with ui.Progress(min=1, max=LOADING_STEPS) as p:
            while _loading.get():
                counter += 1
                p.set(counter, message="Generando vista")
                await asyncio.sleep(0.1)
                if counter >= LOADING_STEPS:
                    counter = 0
        fig_sat = await task
        print("done")
        return fig_sat

    @render_widget
    @reactive.event(input.actualizar_vista, input.confusionRadioSelect)
    async def plotConfusion():
        confusionRadioSelect = input.confusionRadioSelect()

        use_df = (
            session.filt_df[["Vía", "VíaPlanificada"]]
            .dropna()
            .groupby(["Vía", "VíaPlanificada"])
            .size()
            .unstack()
        )
        if confusionRadioSelect == "Relativo":
            x1 = use_df.T
            x2 = use_df.sum(axis=1)
            use_df = (x1 / x2).T * 100

        # print(use_df.head())
        vias = sortStrNumbers(set(list(use_df.index) + list(use_df.columns)))
        use_df[[v for v in vias if v not in use_df.columns]] = None
        for v in vias:
            if v not in use_df.index:
                use_df.loc[v] = None
        use_df = use_df.loc[
            sortStrNumbers(use_df.index), sortStrNumbers(use_df.columns)
        ]

        # use_df = use_df.fillna(0)
        use_df = use_df.fillna(0).replace([0], [None])
        title = f"Confusión de vías en <b>{session.enclavamiento}</b>"
        fig_conf = planificacionVias(use_df, confusionRadioSelect, title)
        return fig_conf

    @render.download(filename=f"{today}.xlsx")
    async def downloadDatos():
        data = session.filt_df
        output = BytesIO()
        with pd.ExcelWriter(output, mode="w") as writer:
            writer.book.formats[0].set_text_wrap()
            data.reset_index(drop=True).style.set_properties(
                **{"vertical-align": "middle"}
            ).to_excel(
                writer, sheet_name="Sheet1", startrow=1, header=False, index=False
            )

            # Get the xlsxwriter workbook and worksheet objects.
            workbook = writer.book
            worksheet = writer.sheets["Sheet1"]

            # Get the dimensions of the dataframe.
            (max_row, max_col) = data.shape

            # Create a list of column headers, to use in add_table().
            column_settings = []
            for header in data.columns:
                column_settings.append({"header": header})

            # Add the table.
            worksheet.add_table(
                first_row=0,
                first_col=0,
                last_row=max_row,
                last_col=max_col - 1,
                options={"columns": column_settings},
            )

            # Make the columns wider for clarity.
            worksheet.set_column(0, max_col - 1, 12)

        processed_data = output.getvalue()
        size = len(processed_data)
        for batch in range(size // 1000 + 1):
            yield processed_data[batch * 1000 : (batch + 1) * 1000]

        # @render.download(filename=f"{today}.xlsx")
        # async def downloadOcupacion():
        #     data = session.filt_df
        #     output = BytesIO()
        #     with pd.ExcelWriter(output, mode="w") as writer:
        #         writer.book.formats[0].set_text_wrap()
        #         data.reset_index(drop=True).style.set_properties(
        #             **{"vertical-align": "middle"}
        #         ).to_excel(
        #             writer, sheet_name="Sheet1", startrow=1, header=False, index=False
        #         )

        #         # Get the xlsxwriter workbook and worksheet objects.
        #         workbook = writer.book
        #         worksheet = writer.sheets["Sheet1"]

        #         # Get the dimensions of the dataframe.
        #         (max_row, max_col) = data.shape

        #         # Create a list of column headers, to use in add_table().
        #         column_settings = []
        #         for header in data.columns:
        #             column_settings.append({"header": header})

        #         # Add the table.
        #         worksheet.add_table(
        #             first_row=0,
        #             first_col=0,
        #             last_row=max_row,
        #             last_col=max_col - 1,
        #             options={"columns": column_settings},
        #         )

        #         # Make the columns wider for clarity.
        #         worksheet.set_column(0, max_col - 1, 12)

        processed_data = output.getvalue()
        size = len(processed_data)
        for batch in range(size // 1000 + 1):
            yield processed_data[batch * 1000 : (batch + 1) * 1000]


app = App(app_ui, server, debug=False)
