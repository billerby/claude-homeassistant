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

> **Read this before cutover.** This package is read-only telemetry. The
> dashboards currently reference ~30 `tesla_custom` entities — most of them
> command/extra entities TeslaMate cannot provide. After step 2 (remove
> `tesla_custom`) **all of those tiles go "unavailable"** until you edit or
> remove them. The lists below are exhaustive, not illustrative.

### Preserved — same entity_id, dashboards keep working
| Entity | TeslaMate topic |
|---|---|
| `sensor.otto_von_bismarck_battery` | `battery_level` |
| `sensor.otto_von_bismarck_range` | `rated_battery_range_km` |
| `sensor.otto_von_bismarck_charger_power` | `charger_power` |
| `sensor.otto_von_bismarck_energy_added` | `charge_energy_added` |
| `sensor.otto_von_bismarck_odometer` | `odometer` |
| `sensor.otto_von_bismarck_temperature_inside` | `inside_temp` |
| `sensor.otto_von_bismarck_temperature_outside` | `outside_temp` |
| `sensor.otto_von_bismarck_tpms_*` (bar) | `tpms_pressure_*` |
| `binary_sensor.otto_von_bismarck_charging` | `state == charging` |
| `binary_sensor.otto_von_bismarck_online` | `state != offline/asleep` |
| `device_tracker.otto_von_bismarck_location_tracker` | `location` (JSON) |
| + new: `…_charge_limit`, `…_charging_amps`, `…_plugged_in`, `…_time_to_full_charge` | |

### Changed — needs a dashboard edit
| Old entity | Now | Where referenced | Action |
|---|---|---|---|
| `number.…charge_limit` | `sensor.…charge_limit` (read-only) | `ui-lovelace.yaml:794` **and** `configuration.yaml:329` (EV smart-charging template) | edit both. **`configuration.yaml:329` is critical**: left as `number.*` it silently resolves to `unknown`→`float(100)`, so `target_soc` is always 100 and the car is treated as needing charge to full. A `{# CUTOVER #}` comment marks the line. |
| `number.…charging_amps` | `sensor.…charging_amps` (read-only) | `ui-lovelace.yaml:804` | edit the dashboard tile |
| `sensor.…time_charge_complete` (a timestamp) | `sensor.…time_to_full_charge` (hours remaining — **different meaning**) | `ui-lovelace.yaml` | repoint the tile; note it now shows duration, not a clock time |

### Lost — no read-only TeslaMate equivalent (tiles will break)
All of these are referenced by `ui-lovelace.yaml` (and mirrored in
`.storage/lovelace`). Remove or repoint each tile, or accept "unavailable":

- `sensor.…charging_rate` — TeslaMate has no range-added-per-hour; closest is `sensor.…charger_power` (kW).
- `sensor.…shift_state`, `sensor.…data_last_update_time`.
- **Command entities** (TeslaMate is read-only by design): `lock.…doors`, `lock.…charge_port_latch`, `cover.…{windows,trunk,frunk,charger_door}`, `climate.…hvac_climate_system`, `switch.…{sentry_mode,valet_mode,charger}`, `button.…{wake_up,remote_start,horn,flash_lights,force_data_update}`, `select.…{heated_seat_*,cabin_overheat_protection}`, `update.…software_update`.
- **State binary_sensors** TeslaMate *does* publish but this package doesn't expose yet (could be added as read-only `binary_sensor`s if you want them back): `binary_sensor.…{doors,windows,user_present,parking_brake,scheduled_charging,scheduled_departure,charger}` ← TeslaMate `doors_open` / `windows_open` / `is_user_present` / etc.

If you later need to **command** the car from HA, add the official
**`tesla_fleet`** core integration alongside (its own Fleet app + signing proxy).

## Notes
- TPMS: TeslaMate publishes **bar**; the `configuration.yaml` "Tesla … Tire
  Pressure" templates were changed from psi→bar conversion to pass-through.
- The package assumes TeslaMate **car id 1**. Change every `teslamate/cars/1/`
  if yours differs.
