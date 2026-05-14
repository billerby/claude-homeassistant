# Grafana dashboards

Companion Grafana dashboards for this Home Assistant instance. Stored
outside `config/` so the HA validators ignore them.

## Datasource

All dashboards expect a **Prometheus-flavoured datasource** pointing
at the VictoriaMetrics instance that backs this HA setup. In Grafana
the datasource is typically registered as type `Prometheus` even
though the backend is VM.

HA entities are stored as metrics with the naming pattern
`{entity_id}_value`. For example, `sensor.jean_luc_battery` is queried
as the metric `sensor.jean_luc_battery_value`. Dots in metric names
are kept (VM allows this).

> **Note on the ingest path:** the `influxdb:` block in
> `config/configuration.yaml` defaults to `localhost:8086`, where
> nothing listens — those writes fail silently. Whatever is actually
> feeding VM lives outside this repo (probably a vmagent, telegraf, or
> a separately-configured InfluxDB add-on with a non-default port).
> If a sensor never shows up in VM, that's where to look.

## Dashboards

### `jean_luc_visited.json` — where Jean-Luc has been

TeslaMate-style "Visited" map plus aggregate stats. The map plots one
marker per reported GPS snapshot and connects consecutive snapshots
with straight line segments. **The lines are not real driven routes**
— Renault Kamereon only reports location when the car wakes up
(typically 1-2 points per drive), so a segment is a straight chord
between two snapshots, not the actual road taken. Documented in the
panel description.

The map reads `sensor.jean_luc_latitude_value` and
`sensor.jean_luc_longitude_value`, two trigger-based template sensors
defined in `config/packages/jean_luc.yaml` that mirror
`device_tracker.jean_luc_location.attributes.{latitude,longitude}`
into numeric form for VictoriaMetrics ingest. **No backfill** —
positions reported before the package was deployed do not appear.

Stats row (aggregated over the dashboard time range):
- **Mileage** = `last(mileage) - first(mileage)`
- **Total Energy added** = `sum_over_time(last_session_kwh_added)`
- **Total Energy used** = `sum_over_time(last_trip_energy_used)`
- **Efficiency** = `total energy used / mileage × 100` (kWh/100km)

The two `sum_over_time` aggregates rely on HA's InfluxDB integration
writing only on state change (its default), so each session/trip
contributes one data point in VM. If you see inflated values, that
assumption may not hold and the stat panels need a different shape
(cumulative HA helper sensor instead).

### `jean_luc.json` — Renault 5 E-Tech

TeslaMate-lite dashboard for the `renault` integration. Reads
HA-published metrics named `sensor.jean_luc_*_value` and
`binary_sensor.jean_luc_*_value`.

Phase 1 panels:
- Stat row: SoC, range, odometer, plug, charging, usable kWh
- SoC over time
- Range over time
- Charging power profile
- Battery + outside temperature
- Battery degradation (one point per full charge — requires the
  `sensor.jean_luc_capacity_at_full` template sensor defined in
  `config/packages/jean_luc.yaml`)

To import:

1. **First verify** the metrics exist in VM: Grafana → Explore → pick
   your Prometheus datasource → type `sensor.jean_luc_` in the metric
   browser. If nothing autocompletes, the Renault entities aren't
   being ingested and the dashboard will be empty regardless.
2. Grafana → Dashboards → New → Import
3. Upload `jean_luc.json`
4. When prompted for the `Prometheus / VictoriaMetrics` datasource,
   pick the same one Explore was using.
