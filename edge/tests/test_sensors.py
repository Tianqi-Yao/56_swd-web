import shutil
from collections import namedtuple
from datetime import datetime
from pathlib import Path

import pytest

import sensors
from sensors import free_gb, read_battery, read_temp_rh

LOGS = Path(__file__).resolve().parent / "fixtures" / "logs"


def test_temp_rh_nearest_within_5min():
    # 12:01:02 差 59 秒，11:58:58 差 65 秒
    assert read_temp_rh(LOGS, datetime(2026, 7, 15, 12, 0, 3)) == (28.4, 61.2)


def test_temp_rh_skips_nan_row():
    # 12:00:30 那行是 NaN，最近的有效行是 12:01:02
    assert read_temp_rh(LOGS, datetime(2026, 7, 15, 12, 0, 30)) == (28.4, 61.2)


def test_temp_rh_outside_window_is_none():
    assert read_temp_rh(LOGS, datetime(2026, 7, 15, 12, 10, 0)) == (None, None)


def test_temp_rh_missing_file_is_none():
    assert read_temp_rh(LOGS, datetime(2026, 7, 16, 12, 0, 0)) == (None, None)


def test_temp_rh_missing_dir_is_none(tmp_path):
    assert read_temp_rh(tmp_path / "nope", datetime(2026, 7, 15, 12, 0, 0)) == (None, None)


def test_battery_reads_monthly_file_and_keeps_low_value():
    # 12:10 的 0.09 V 比 11:50 更近；低电压是真实测量，不能被过滤
    assert read_battery(LOGS, datetime(2026, 7, 15, 12, 5, 0)) == 0.09


def test_battery_skips_bad_time_row():
    assert read_battery(LOGS, datetime(2026, 6, 12, 12, 0, 0)) == 12.60


def test_battery_outside_15min_is_none():
    assert read_battery(LOGS, datetime(2026, 7, 15, 15, 0, 0)) is None


def test_battery_previous_month_file_at_month_start():
    assert read_battery(LOGS, datetime(2026, 8, 1, 0, 5, 0)) == 13.10


def test_battery_missing_dir_is_none(tmp_path):
    assert read_battery(tmp_path / "nope", datetime(2026, 7, 15, 12, 0, 0)) is None


def test_rtc_fault_date_not_matched(tmp_path):
    without = tmp_path / "logs"
    shutil.copytree(LOGS, without)
    (without / "2000-01-01.csv").unlink()
    at = datetime(2026, 7, 15, 12, 0, 3)
    assert read_temp_rh(LOGS, at) == read_temp_rh(without, at)


def test_free_gb_one_decimal(monkeypatch):
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(sensors.shutil, "disk_usage", lambda p: Usage(0, 0, 42_123_456_789))
    assert free_gb(Path("/")) == 39.2
