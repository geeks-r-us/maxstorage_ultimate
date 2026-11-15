"""Tests for binary sensor helpers."""
from custom_components.maxstorage_ultimate.binary_sensor import _relais_state


def test_relais_state():
    data = {"Relais": {"value": [1, 0]}}
    assert _relais_state(data, 0) is True
    assert _relais_state(data, 1) is False


def test_relais_state_missing_values():
    assert _relais_state({"Relais": {"value": [1]}}, 3) is None
    assert _relais_state({}, 0) is None
    assert _relais_state(None, 0) is None
