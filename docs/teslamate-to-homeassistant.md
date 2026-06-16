# Tesla in Home Assistant — migrating off `tesla_custom` to TeslaMate/MQTT

The `alandtse/tesla` (`tesla_custom`) integration broke with Tesla's June 2026
Owner API shutdown. Rather than re-auth HA against the Fleet API (a second
consumer competing for quota and keeping the car awake), we make **TeslaMate the
single Fleet API consumer** and feed HA its telemetry over MQTT. HA's use of the
Tesla data is read-only (the EV charging plan reads SoC and actuates the **Easee**
wallbox, not the car), so nothing of value is lost.

## Architecture

```
AWS Lightsail (my-teslamate)                   Home (HAOS)
  TeslaMate ──Fleet API──▶ Tesla
     │ MQTT
  mosquitto :1883 (local)
  mosquitto :8883 (TLS+auth) ◀──bridge── HA mosquitto add-on
                                              │ teslamate/cars/1/#
                                         packages/otto_von_bismarck.yaml (read-only entities)
```

Both ends are scaffolded:
- Lightsail broker + TLS listener: `my-teslamate/mqtt-bridge/` (+ compose edit)
- HA bridge: `config/mosquitto/teslamate-bridge.conf`
- HA entities: `config/packages/otto_von_bismarck.yaml`

## Cutover order (important)

1. **Bring up the MQTT bridge** (Lightsail + HA sides) per
   `my-teslamate/mqtt-bridge/README.md`. Confirm `teslamate/#` arrives in HA's
   MQTT "Listen to a topic" tool. **Confirm the car id** (topics are
   `teslamate/cars/1/...`; if not `1`, fix the id in the package).
2. **Remove `tesla_custom` FIRST**: Settings → Devices & Services → Tesla Custom
   → delete both config entries (`tesla_custom` and `billerby@gmail.com`). This
   frees the `otto_von_bismarck_*` entity_ids in the registry. If you skip this,
   the new MQTT entities collide and get `_2` suffixes.
3. **Add the package**: it's already in `packages/`, loaded via
   `homeassistant: packages: !include_dir_named packages`. Restart HA (or
   reload YAML). The `otto_von_bismarck_*` entities reappear, now MQTT-backed.
4. **Optionally remove the HACS repo** `alandtse/tesla` once happy.

## Entity map

### Preserved (same entity_id, read-only) — dashboards/templates keep working
| Entity | TeslaMate topic |
|---|---|
| `sensor.otto_von_bismarck_battery` | `battery_level` |
| `sensor.otto_von_bismarck_range` | `rated_battery_range_km` |
| `sensor.otto_von_bismarck_charger_power` | `charger_power` |
| `sensor.otto_von_bismarck_energy_added` | `charge_energy_added` |
| `sensor.otto_von_bismarck_odometer` | `odometer` |
| `sensor.otto_von_bismarck_tpms_*` (bar) | `tpms_pressure_*` |
| `binary_sensor.otto_von_bismarck_charging` | `state == charging` |
| `device_tracker.otto_von_bismarck_location_tracker` | `location` (JSON) |
| + extras: inside/outside temp, plugged-in, online, time-to-full | |

### Changed — needs a small dashboard edit
These were settable `number.*` entities; read-only MQTT makes them `sensor.*`:
| Old (number) | New (sensor) | Used in |
|---|---|---|
| `number.otto_von_bismarck_charge_limit` | `sensor.otto_von_bismarck_charge_limit` | `ui-lovelace.yaml:794` |
| `number.otto_von_bismarck_charging_amps` | `sensor.otto_von_bismarck_charging_amps` | `ui-lovelace.yaml:804` |

Update those two lines in `ui-lovelace.yaml`, and the matching tiles in the
UI-managed dashboard (`.storage/lovelace` — edit via the UI, not by hand).

### Lost — no TeslaMate equivalent
| Entity | Note |
|---|---|
| `sensor.otto_von_bismarck_charging_rate` | TeslaMate has no range-added-per-hour. Dashboard `ui-lovelace.yaml:798` — swap the tile to `sensor.otto_von_bismarck_charger_power` (kW). |
| all **command** entities (climate, locks, wake, sentry, charge start/stop, set amps/limit, seat heaters, …) | TeslaMate is read-only by design. Nothing in your automations used them. If you ever need to command the car from HA, add the official **`tesla_fleet`** core integration alongside (its own Fleet app + signing proxy). |

## Notes
- TPMS: TeslaMate publishes **bar**; the `configuration.yaml` "Tesla … Tire
  Pressure" templates were changed from psi→bar conversion to pass-through.
- The package assumes TeslaMate **car id 1**. Change every `teslamate/cars/1/`
  if yours differs.
