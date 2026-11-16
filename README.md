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
| Sensor | Grid Power (signed: feeding in +, drawing −) | W |
| Sensor | Grid Import Power | W |
| Sensor | Grid Export Power | W |
| Sensor | Usage Power | W |
| Sensor | Plant Power | W |
| Sensor | Storage DC Power | W |
| Sensor | Battery Charge Energy | kWh (integrated, for the energy dashboard) |
| Sensor | Battery Discharge Energy | kWh (integrated, for the energy dashboard) |
| Sensor | Grid Import Energy | kWh (integrated, for the energy dashboard) |
| Sensor | Grid Export Energy | kWh (integrated, for the energy dashboard) |
| Sensor | Plant Energy | kWh (integrated, for the energy dashboard) |
| Sensor | DC/DC converter 1…n state | text, e.g. "In Betrieb" (diagnostic, serial number as attribute) |
| Sensor | Battery 1…n state | text, e.g. "In Betrieb" (diagnostic, serial number as attribute) |
| Binary sensor | One per named relais | on/off |

The model name of the device (e.g. `15SMT Island (1)`) and the DC/DC converter and battery states are read from the device overview page (`device_overview.php`), at most every 5 minutes. Firmware without that page still works, only without these values.

## Swarm (master with workers)

Add only the **master**. Its response contains the data of every worker, so no second connection is needed:

- Each worker (`worker1`, `worker2`, …) appears as its own device *MaxStorageUltimate Worker N* with battery, PV and DC sensors (power and energy).
- A device *MaxStorageUltimate Swarm* shows the sums over the master and all workers: battery power, charge/discharge power and energy, total capacity, capacity-weighted state of charge and PV power.
- Grid and usage values are system-wide (measured at the master) and are only available on the master device.
- The values of the master device are the master's own values.

Workers that appear later are added automatically.

Workers are not offered as separate devices: when a discovered device reports itself as a swarm worker (`Swarming.swarmJob` = `worker`), the discovery is dropped silently. To detect this, a discovered device is queried once with the default credentials (`user` / `solarmax.com`); credentials of existing entries are never sent. When Home Assistant discovers a worker, its measurements (battery capacity, state of charge, power) are compared with the worker blocks of the master. The worker's device page is read at the same time, so its device shows the **firmware version, hardware version and serial number** like the master. If exactly one worker matches, the worker's **MAC address** and hostname are added to that worker's device, so that other integrations (e.g. UniFi) can merge it with the network client. If several workers look alike (e.g. identical idle batteries), nothing is assigned until a later discovery can tell them apart. The MAC address is read with `ping`/`arp`, so it only works if Home Assistant is in the same network segment as the worker; a MAC that is already used by another device is never reused.

If you try to add a worker manually, the setup tells you to add the master instead.

## Diagnostics

For bug reports or new firmware/swarm setups, use *Settings → Devices & services → Max.Storage Ultimate → ⋮ → Download diagnostics*. The file contains the raw device response; host, credentials, name, serial number and MAC address are redacted. Please check it before attaching it to an issue.

## Development

```
pip install -r requirements_test.txt
pytest
```
