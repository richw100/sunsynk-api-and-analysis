from datetime import datetime


class PriceData:
    def __init__(self):
        self.currentOffPeak = 0.067
        self.currentPeak = 28.26
        self.currentOffPeakStart = "00:00"
        self.currentOffPeakStop = "00:07"
        self.currentStart = datetime.strptime("2025-07-10", "%Y-%m-%d")
        self.currentStop = datetime.strptime("2026-09-10", "%Y-%m-%d")
        self.offPeakAverage = 0.96
        self.currentExport = 0.165
        self.standingCharge = 0.6
        self.ComparestandingCharge = 0.53
        self.CompareRate = 0.25
        self.originalPrice = 6210 - 3.53 # £3.53 - adjusting for solar used to charge car June '25 - May '26 (32 kWh * .2767-.0165)
        self.InterestRate = 3.7
