from datetime import datetime


class TrainAction:
    ACTION_APPROACH = "Approach"
    ACTION_ARRIVAL = "Arrival"
    ACTION_PLATFORM = "Platform"
    ACTION_DEPARTURE = "Departure"

    def __init__(
        self, technicalNumber: str, actionName: str, platformCode: str, time: str
    ):
        self.technicalNumber = technicalNumber
        self.actionName = actionName
        self.platformCode = platformCode
        # time = time.split(".")[0]
        # self.datetime = datetime.strptime(time, "%Y-%m-%dT%H:%M:%S")
        self.datetime = datetime.strptime(
            datetime.strptime(time, "%Y-%m-%dT%H:%M:%S.%f%z")
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S"),
            "%Y-%m-%d %H:%M:%S",
        )

    def getTechnicalNumber(self):
        return self.technicalNumber

    def getActionName(self):
        return self.actionName

    def getPlatformCode(self):
        return self.platformCode

    def getDatetime(self):
        return self.datetime
