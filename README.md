# MaxStorage Ultimate component for Home Assistant

This is the MaxStorage Ultimate component for Home Assistant. It allows you to get the status of your SolarMax Max.Storage device in Home Assistant.
It provides more sensors than the official MaxStorage modbus interface like relais states, which allows to control the usage of surplus energy with Home Assistant automations.

## Installation

### HACS

Add this repository as a custom repository in HACS (category *Integration*), install **Max.Storage Ultimate** and restart Home Assistant.

### Manual

Copy `custom_components/maxstorage_ultimate/` to `<config_dir>/custom_components/maxstorage_ultimate/` and restart Home Assistant.

## Setup

The device is discovered via zeroconf. Otherwise add it via *Settings → Devices & services → Add integration → Max.Storage Ultimate* and enter host, user and password of the device's web interface (defaults: `user` / `solarmax.com`).

Host, credentials and the update interval (5–3600 s, default 30 s) can be changed later via *Configure* on the integration.

## Entities

| Type | Entity | Unit |
|------|--------|------|
| Sensor | Battery SoC | % |
| Sensor | Battery Capacity | Wh |
| Sensor | Battery Power (signed: charging +, discharging −) | W |
| Sensor | Battery Charge Power | W |
| Sensor | Battery Discharge Power | W |
| Sensor | Grid Power | W |
| Sensor | Usage Power | W |
| Sensor | Plant Power | W |
| Sensor | Storage DC Power | W |
| Sensor | Battery Charge Energy | kWh (integrated, for the energy dashboard) |
| Sensor | Battery Discharge Energy | kWh (integrated, for the energy dashboard) |
| Sensor | Plant Energy | kWh (integrated, for the energy dashboard) |
| Binary sensor | One per named relais | on/off |

## Development

```
pip install -r requirements_test.txt
pytest
```
