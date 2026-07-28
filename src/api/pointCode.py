from typing import List, Dict
from trainAction import TrainAction
import pandas as pd
import numpy as np
from datetime import timedelta


class PointCode:
    JOIN_DELIMITER = ", "

    def __init__(self, code: str, name: str):
        self.code = f"{code:0>5}"
        self.name = name
        self.trains: Dict[str, TrainAction] = {}

    @staticmethod
    def getReportHeaders():
        return [
            "pointCode",
            "name",
            "technicalNumber",
            "firstStagePlatform",
            "secondStagePlatform",
            "firstStage",
            "secondStage",
            "movementType",
        ]

    def addTrainAction(self, trainAction: TrainAction):
        if not trainAction.getTechnicalNumber() in self.trains:
            self.trains[trainAction.getTechnicalNumber()] = []
        self.trains[trainAction.getTechnicalNumber()].append(trainAction)

    def getReport(self):
        csvRows = []

        # Procesamos todos los trenes
        for technicalNumber in self.trains.keys():
            csvRows.extend(self.processTrainActions(technicalNumber))

        return csvRows

    def processTrainActions(self, technicalNumber: str):
        information = []
        # Acciones del tren seleccionado
        df_train = pd.DataFrame(
            [vars(el) for el in self.trains.get(technicalNumber)]
        ).sort_values(by=["technicalNumber", "datetime"])
        df_cols = df_train.columns

        # Juntamos las acciones por tramos horarios de menos de 20 horas de diferencia
        action_split = np.split(
            df_train.values,
            np.where(df_train["datetime"].diff() > timedelta(hours=20))[0],
            axis=0,
        )

        for train_actions in action_split:
            # Define info to save
            firstStagePlatform = None
            secondStagePlatform = None
            firstStageDatetime = None
            secondStageDatetime = None
            movementType = None

            # Generate auxiliar dataframe by day for computations
            df_aux = pd.DataFrame(train_actions, columns=df_cols)
            df_aux = df_aux.groupby("actionName").agg(list)
            df_aux["valid"] = df_aux["datetime"].apply(np.argmin)
            df_aux = df_aux.reset_index()

            # If initial, only use platform and departure
            if "Platform" in df_aux["actionName"].tolist():
                movementType = "Origen"
                df_aux = df_aux[
                    df_aux["actionName"].isin(
                        [TrainAction.ACTION_PLATFORM, TrainAction.ACTION_DEPARTURE]
                    )
                ]
            else:
                if (
                    pd.Series(
                        [TrainAction.ACTION_ARRIVAL, TrainAction.ACTION_DEPARTURE]
                    )
                    .isin(df_aux["actionName"])
                    .all()
                ):
                    movementType = "Paso"
                else:
                    movementType = "Destino"
                df_aux = df_aux[
                    df_aux["actionName"].isin(
                        [TrainAction.ACTION_APPROACH, TrainAction.ACTION_ARRIVAL]
                    )
                ]

            for i in df_aux.index:
                row = df_aux.loc[i]
                valid = row["valid"]
                if row["actionName"] in [
                    TrainAction.ACTION_APPROACH,
                    TrainAction.ACTION_PLATFORM,
                ]:
                    firstStagePlatform = row["platformCode"][valid]
                    firstStageDatetime = row["datetime"][valid]
                elif row["actionName"] in [
                    TrainAction.ACTION_ARRIVAL,
                    TrainAction.ACTION_DEPARTURE,
                ]:
                    secondStagePlatform = row["platformCode"][valid]
                    secondStageDatetime = row["datetime"][valid]

            information.append(
                [
                    self.code,
                    self.name,
                    f"{technicalNumber:0>5}",
                    firstStagePlatform,
                    secondStagePlatform,
                    firstStageDatetime,
                    secondStageDatetime,
                    movementType,
                ]
            )
        return information
