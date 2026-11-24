"""Unit tests for the published tariff calculation and time classification."""
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.server_ai_bems import (
    calculate_incremental_cost,
    classify_time_period,
    progressive_cost_for_increment,
    tariff_price_vnd_per_kwh,
)

ICT = ZoneInfo("Asia/Ho_Chi_Minh")


def test_decision_963_time_bands():
    monday = datetime(2026, 11, 16, 0, 0, tzinfo=ICT)
    sunday = datetime(2026, 11, 22, 18, 0, tzinfo=ICT)
    assert classify_time_period(monday.replace(hour=5, minute=59)) == "off_peak"
    assert classify_time_period(monday.replace(hour=17, minute=29)) == "normal"
    assert classify_time_period(monday.replace(hour=17, minute=30)) == "peak"
    assert classify_time_period(monday.replace(hour=22, minute=30)) == "normal"
    assert classify_time_period(sunday) == "normal"


def test_residential_five_tier_progressive_price():
    expected = 100 * 1901 + 50 * 2289
    assert progressive_cost_for_increment(150, 0) == expected
    assert progressive_cost_for_increment(50, 100) == 50 * 2289
    assert tariff_price_vnd_per_kwh(
        datetime(2026, 11, 16, 12, 0, tzinfo=ICT),
        "residential",
        month_to_date_kwh=250,
    ) == 2894


def test_education_low_voltage_flat_tariff():
    stamp = datetime(2026, 11, 16, 18, 0, tzinfo=ICT)
    assert calculate_incremental_cost(2.0, stamp, 400, "education_lv") == 4060


def test_production_low_voltage_time_of_use_tariff():
    peak = datetime(2026, 11, 16, 18, 0, tzinfo=ICT)
    off_peak = datetime(2026, 11, 17, 4, 0, tzinfo=ICT)
    assert calculate_incremental_cost(1.0, peak, 0, "production_lv") == 3586
    assert calculate_incremental_cost(1.0, off_peak, 0, "production_lv") == 1274
