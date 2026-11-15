"""Tests for derived sensor values."""
import pytest

from custom_components.maxstorage_ultimate.sensor import (
    _battery_charge_power,
    _battery_discharge_power,
    _grid_export_power,
    _grid_import_power,
)


@pytest.mark.parametrize(
    ("power", "charge", "discharge"),
    [(500, 500, 0), (-300, 0, 300), (0, 0, 0), (12.5, 12.5, 0)],
)
def test_battery_split(power, charge, discharge):
    data = {"batteryPower": power}
    assert _battery_charge_power(data) == charge
    assert _battery_discharge_power(data) == discharge


def test_battery_split_missing_value():
    assert _battery_charge_power({}) is None
    assert _battery_discharge_power({}) is None


@pytest.mark.parametrize(
    ("power", "imported", "exported"),
    [(500, 0, 500), (-300, 300, 0), (0, 0, 0)],
)
def test_grid_split(power, imported, exported):
    data = {"gridPower": power}
    assert _grid_import_power(data) == imported
    assert _grid_export_power(data) == exported


def test_grid_split_missing_value():
    assert _grid_import_power({}) is None
    assert _grid_export_power({}) is None


@pytest.mark.parametrize("value", ["-", "", None, "abc", True])
def test_split_ignores_non_numeric_values(value):
    data = {"batteryPower": value, "gridPower": value}
    assert _battery_charge_power(data) is None
    assert _battery_discharge_power(data) is None
    assert _grid_import_power(data) is None
    assert _grid_export_power(data) is None


def test_split_handles_numeric_strings():
    # the device reports some values as strings, e.g. gridPower "-23.5"
    assert _grid_import_power({"gridPower": "-23.5"}) == 23.5
    assert _grid_export_power({"gridPower": "-23.5"}) == 0
    assert _battery_discharge_power({"batteryPower": "300"}) == 0
    assert _battery_charge_power({"batteryPower": "300"}) == 300
