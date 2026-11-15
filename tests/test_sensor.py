"""Tests for derived sensor values."""
import pytest

from custom_components.maxstorage_ultimate.sensor import (
    _battery_charge_power,
    _battery_discharge_power,
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
