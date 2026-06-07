from dataclasses import dataclass
from datetime import datetime


@dataclass
class PriceData:
    current_off_peak: float = 0.067
    current_peak: float = 28.26
    current_off_peak_start: str = "00:00"
    current_off_peak_stop: str = "00:07"
    current_start: datetime = datetime.strptime("2025-07-10", "%Y-%m-%d")
    current_stop: datetime = datetime.strptime("2026-09-10", "%Y-%m-%d")
    off_peak_average: float = 0.96         # computed by EnergySummary; do not set directly
    off_peak_baseline_kwh: float = 0.96   # expected kWh/day at off-peak for a 7-hour window
    current_export: float = 0.165
    standing_charge: float = 0.6
    original_price: float = 6210 
    interest_rate: float = 3.7
