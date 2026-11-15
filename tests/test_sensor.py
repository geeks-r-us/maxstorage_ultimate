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
