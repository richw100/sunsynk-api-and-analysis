from datetime import datetime


class PriceData:
    def __init__(self):
        self.current_off_peak = 0.067
        self.current_peak = 28.26
        self.current_off_peak_start = "00:00"
        self.current_off_peak_stop = "00:07"
        self.current_start = datetime.strptime("2025-07-10", "%Y-%m-%d")
        self.current_stop = datetime.strptime("2026-09-10", "%Y-%m-%d")
        self.off_peak_average = 0.96
        self.current_export = 0.165
        self.standing_charge = 0.6
        self.compare_standing_charge = 0.53
        self.compare_rate = 0.25
        self.original_price = 6210 - 3.53  # £3.53 - adjusting for solar used to charge car June '25 - May '26 (32 kWh * .2767-.0165)
        self.interest_rate = 3.7
