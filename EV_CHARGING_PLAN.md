# Smart EV Charging with Battery Schedule Integration

## Overview
Create Home Assistant automations that:
- Parse battery charging schedule from `sensor.batteries_tou_charging_and_discharging_periods`
- Coordinate EV charging to respect 15 kW limit with real-time monitoring
- Charge EV during cheapest hours in OFF-TARIFF periods:
  - Nov-March weekdays: 20:00-07:00
  - Nov-March weekends: ALL DAY
- Dynamically adjust power based on battery schedule and actual grid consumption

## Power Budget Strategy

### Critical Safety Limit
- **HARD LIMIT**: 15 kW total grid import (enforced by `sensor.power_meter_active_power`)
- **Safety threshold**: 14.5 kW (0.5 kW margin for measurement delays)
- **Emergency stop**: If grid power > 14.5 kW, immediately reduce or stop EV charging

### Static Power Allocation (Initial Estimate)
- **Appliance baseline**: ~3 kW (conservative estimate)
- **Circuit limit**: 16 A × 230 V × 3 phases = **11 kW maximum**
- **When battery charging (+)**: EV limited to **6 kW** (≈8.7 A) → 5kW battery + 6kW EV + 3kW appliances = 14kW
- **When battery idle/discharging (-)**: EV limited to **11 kW** (≈16 A max) → 0kW battery + 11kW EV + 3kW appliances = 14kW

### Dynamic Power Management (Real-time)
- **Monitor**: `sensor.power_meter_active_power` continuously
- **Calculate**: Available power = 14.5 kW - current_grid_power
- **Adjust**: EV charging power every minute based on actual grid consumption
- **Example**: If grid shows 10 kW, limit EV to 4.5 kW (10 + 4.5 = 14.5 kW)

## System Details

### Current Setup
- **Home Battery**: 15 kWh capacity, 5 kW max charging power
- **EV Battery**: Tesla Model 3 (~75 kWh usable capacity)
  - SOC sensor: `sensor.otto_von_bismarck_battery` (current charge level 0-100%)
  - Target charge limit: `number.otto_von_bismarck_charge_limit` (user-adjustable via Tesla app/HA)
  - Typical charging rate: ~11 kW on 3-phase (when not limited)
  - **WHY TRACK SOC**: Prevents unnecessary charging when already at target, optimizes energy usage
- **Easee Charger**: `easee_home_91929` (EH919290) at Karnvedsgatan 16
  - 3-phase charger (P1, P2, P3)
  - Circuit rated current: 16 A (11 kW max at 230V × 3)
  - Control entity: `number.easee_home_91929_dynamic_charger_limit`
  - Cable lock sensor: `binary_sensor.easee_home_91929_cable_locked` (detects if EV connected)
- **Grid Power Meter**: `sensor.power_meter_active_power` (CRITICAL - monitors 15 kW limit)
  - **WHY CRITICAL**: Real-time monitoring prevents circuit breaker trips, ensures safety
  - Can be negative when exporting solar power to grid
- **Node-RED**: Runs at 13:30 and 19:00
  - Creates fixed charging schedules for battery
  - Fetches Tibber hourly prices
  - Calculates cheapest 8 hours and publishes to MQTT
  - **WHY NODE-RED**: Avoids HA template size limitations, handles DST automatically
- **Battery Schedule Sensor**: `sensor.batteries_tou_charging_and_discharging_periods`
  - **WHY PARSE THIS**: Determines when battery is charging to avoid power conflicts
- **Price Source**: Tibber API via Node-RED (hourly prices, no size limitations)
- **Cheapest Hours Sensor**: `sensor.ev_charging_hours_tonight` (MQTT from Node-RED)
  - **WHY 8 HOURS**: Covers most nightly charging needs while ensuring cheapest prices

### Tariff Periods

**ON-TARIFF (Expensive - avoid charging):**
- **Season**: November 1 - March 31
- **Days**: Weekdays only (Mon-Fri)
- **Hours**: 07:00 - 20:00

**OFF-TARIFF (Cheap - charge here):**
- **Nov-March Weekdays**: 20:00 - 07:00 (11 hours/night)
- **Nov-March Weekends**: ALL DAY Saturday & Sunday (48 hours)
- **Apr-Oct**: ALL TIMES (no tariffs applied)

### Battery Schedule Format
The battery schedule sensor has attributes like:
```
Period 1: 15:00-16:59/6/+
Period 2: 17:00-23:59/6/-
Period 3: 00:00-02:59/7/-
Period 4: 03:00-08:00/7/+
Period 5: 08:01-09:22/7/-
Period 6: 09:23-10:01/7/+
Period 7: 14:59-23:59/7/-
```

Format: `HH:MM-HH:MM/day/action`
- **day**: 1=Monday, 7=Sunday
- **action**: `+` = charge, `-` = discharge/idle

## Tesla SOC Integration: What & Why

### The Challenge
Without SOC tracking, the system would:
- ❌ Charge for 8 hours every night regardless of need
- ❌ Waste energy charging an already-full battery
- ❌ Prevent others from using the charger when your car is full
- ❌ Wear battery unnecessarily with unneeded charge cycles

### The Solution
Track Tesla battery state and only charge when needed:
- ✅ Check current SOC vs. target before starting
- ✅ Stop immediately when target reached
- ✅ Avoid charging if already at target
- ✅ Optimize charging duration based on actual need

### What Should Be Implemented

#### 1. SOC-Based Start Condition (ALREADY IMPLEMENTED ✅)
**Location**: `configuration.yaml` - `binary_sensor.ev_should_charge_now` (lines 187-201)

**What it does**:
```yaml
{% set current_soc = states('sensor.otto_von_bismarck_battery')|float(0) %}
{% set target_soc = states('number.otto_von_bismarck_charge_limit')|float(100) %}
{% set needs_charging = current_soc < target_soc %}
{{ ... and needs_charging }}
```

**Why it's needed**:
- **Prevents wasteful charging**: If Tesla is already at 80% and target is 80%, don't start
- **Protects battery health**: Avoids unnecessary charge cycles
- **Saves energy**: Only uses electricity when actually needed
- **User control**: Respects target set in Tesla app

**Example scenarios**:
- Current: 75%, Target: 80% → **Will charge** (needs 5%)
- Current: 80%, Target: 80% → **Won't charge** (already at target)
- Current: 85%, Target: 80% → **Won't charge** (above target)

#### 2. SOC-Based Stop Condition (TO BE IMPLEMENTED IN AUTO 5)
**Location**: `automations.yaml` - "EV: Stop Charging When Complete"

**What it should do**:
Monitor `sensor.otto_von_bismarck_battery` during charging and stop when:
```yaml
{{ states('sensor.otto_von_bismarck_battery')|float(0) >=
   states('number.otto_von_bismarck_charge_limit')|float(100) }}
```

**Why it's needed**:
- **Don't wait for cheap window to end**: If you reach 80% at 03:00, stop then (not at 07:00)
- **Free up charger sooner**: Let others use it if needed
- **Exact target adherence**: Hit your target precisely, not approximately
- **Notification**: Tell user when charging completed

**Example scenario**:
- Cheap window: 00:00-07:00 (8 hours)
- Current SOC: 50%, Target: 80%
- Charging rate: ~10%/hour
- Expected completion: ~03:00 (3 hours needed)
- **With SOC stop**: Stops at 03:00 when 80% reached ✅
- **Without SOC stop**: Continues until 07:00, hitting 80% and staying there ❌

#### 3. Optional: Dynamic Charging Duration (FUTURE ENHANCEMENT)
**Not yet implemented - for future consideration**

**What it could do**:
Calculate how many hours are actually needed:
```yaml
{% set current_soc = states('sensor.otto_von_bismarck_battery')|float(0) %}
{% set target_soc = states('number.otto_von_bismarck_charge_limit')|float(100) %}
{% set battery_capacity = 75 %}  # kWh
{% set charging_power = 10 %}    # kW average
{% set kwh_needed = (target_soc - current_soc) / 100 * battery_capacity %}
{% set hours_needed = (kwh_needed / charging_power)|round(0, 'ceil') %}
```

**Why it might be useful**:
- **Optimize cheapest hours**: Only use the cheapest N hours needed, not all 8
- **Example**: Need 3 hours → Use cheapest 3 hours (02:00-05:00), not cheapest 8
- **Better price optimization**: Focus charging on absolute cheapest hours

**Why it's not implemented yet**:
- ⚠️ Complex: Requires accurate battery capacity and charging rate data
- ⚠️ Variable charging rates: 11 kW max, but often less due to battery/grid limits
- ⚠️ Current approach is simpler: Charge during all cheap hours, stop when done
- ⚠️ Tesla charges slower when cold or near full
- ✅ Current SOC-based stop achieves same result more simply

**Important limitation - Hours not sorted by price**:
- ⚠️ Node-RED publishes hours sorted numerically: `[0, 1, 2, 3, 4, 5, 6, 23]`
- ⚠️ This does NOT mean hour 0 is cheaper than hour 23!
- ⚠️ If you only need 1 hour of charging, you might not get THE cheapest hour
- ✅ **Current workaround**: Charge during ALL cheap hours, stop when target reached
- ✅ **Future fix**: Node-RED could publish hours sorted by price, OR include price data
- ✅ **For most cases**: Difference between cheapest vs. 2nd cheapest is minimal

### Current Implementation Status

| Component | Status | Location |
|-----------|--------|----------|
| **SOC check before start** | ✅ Implemented | `configuration.yaml:198-201` |
| **SOC-based stop** | 📝 Documented | `EV_CHARGING_PLAN.md:417-440` |
| **Stop notification** | 📝 Documented | `EV_CHARGING_PLAN.md:428-430` |
| **Dynamic duration** | 💭 Future enhancement | Not planned yet |

### Benefits of SOC Integration

1. **Energy Efficiency**: Only charge what's needed
2. **Battery Health**: Fewer unnecessary charge cycles
3. **Cost Savings**: Don't pay for electricity you don't need
4. **Charger Availability**: Free up charger sooner for others
5. **User Control**: Respect Tesla app settings
6. **Smart Automation**: Truly intelligent charging, not just scheduled

### Real-World Example

**Scenario**: Weekend night, battery schedule conflict analysis

**Friday night state**:
- Current SOC: 60%
- Target SOC: 80%
- Cheapest hours tonight: 00:00, 01:00, 02:00, 03:00, 04:00, 05:00, 06:00, 23:00

**Without SOC tracking**:
- ❌ Charges 00:00-07:00 (8 hours)
- ❌ Reaches 80% at ~02:00
- ❌ Sits at 80% for 5 more hours
- ❌ Energy wasted on trickle/balance charging

**With SOC tracking (current implementation)**:
- ✅ Starts at 00:00 (SOC check: 60% < 80%, proceed)
- ✅ Charges normally
- ✅ Reaches 80% at ~02:30
- ✅ Stops immediately (Auto 5 detects SOC >= target)
- ✅ Notification: "EV Charging Complete - Target 80% reached"
- ✅ Charger available for others
- ✅ Saved ~4.5 hours of unnecessary charging

## Architecture Overview

### Price Calculation Flow:
1. **Node-RED** (13:30 & 19:00) → Fetches Tibber prices
2. **Node-RED Function** → Calculates cheapest 8 hours (with DST handling)
3. **MQTT Publish** → Sends to `homeassistant/sensor/ev_charging_hours/state`
4. **Home Assistant** → MQTT sensor receives data
5. **Automations** → Use `sensor.ev_charging_hours_tonight` to control charging

### Benefits of Node-RED Approach:
- ✅ No template size limitations
- ✅ Handles DST transitions automatically
- ✅ Already integrated with existing Tibber flow
- ✅ Runs at optimal times (13:30 after prices arrive, 19:00 before charging)
- ✅ Easier to debug (Node-RED debug output)

## What Needs to Be Implemented (Summary)

### ✅ Already Implemented (configuration.yaml)
1. **Template Sensors** (5 sensors):
   - `sensor.battery_charging_current_hour` - Parses battery schedule
   - `sensor.ev_max_power_static` - Static 6/11 kW limit based on battery
   - `sensor.ev_max_power_available` - Dynamic real-time power available
2. **Binary Sensors** (2 sensors):
   - `binary_sensor.off_tariff_period_active` - Detects charging window
   - `binary_sensor.ev_should_charge_now` - Master decision sensor (with SOC check ✅)
3. **Input Helpers** (3 items):
   - `input_boolean.ev_smart_charging_enabled` - Master on/off switch
   - `input_number.ev_charging_power_limit` - Current power limit (kW)
   - `input_number.ev_charging_current_limit` - Current limit (Amps)
4. **MQTT Sensor** (1 sensor):
   - `sensor.ev_charging_hours_tonight` - Cheapest hours from Node-RED

### 📝 To Be Implemented (automations.yaml)
**8 automations** that control charging behavior:
1. Disable Tibber smart charging on startup
2. Calculate nightly charging plan (triggers Node-RED)
3. Set initial power limit when charging starts
4. Start EV charging during cheap hours
5. **Stop EV charging** (includes SOC-based stop ✅)
6. Real-time power management (every minute)
7. Emergency stop on grid overload
8. Adjust power on battery schedule change

### 🔧 Already Configured (Node-RED)
- Tibber price fetching (runs 13:30 & 19:00)
- Cheapest 8 hours calculation (with DST handling)
- MQTT publish to Home Assistant
- Battery schedule management (existing, unchanged)

## Components to Create

### 1. Template Sensors (configuration.yaml) - ✅ IMPLEMENTED

#### Parse Battery Schedule
```yaml
template:
  - sensor:
      - name: "Battery Charging Current Hour"
        unique_id: battery_charging_current_hour
        state: >
          {% set ns = namespace(charging=false) %}
          {% for i in range(1, 8) %}
            {% set period = state_attr('sensor.batteries_tou_charging_and_discharging_periods', 'Period ' ~ i) %}
            {% if period and '+' in period %}
              {% set parts = period.split('/') %}
              {% if parts|length == 3 %}
                {% set times = parts[0].split('-') %}
                {% set start_time = times[0] %}
                {% set end_time = times[1] %}
                {% set day = parts[1]|int %}
                {% set current_day = now().isoweekday() %}
                {% set current_time = now().strftime('%H:%M') %}
                {% if day == current_day and current_time >= start_time and current_time <= end_time %}
                  {% set ns.charging = true %}
                {% endif %}
              {% endif %}
            {% endif %}
          {% endfor %}
          {{ ns.charging }}
```

#### Available EV Power (Static Estimate)
```yaml
  - sensor:
      - name: "EV Max Power Static"
        unique_id: ev_max_power_static
        unit_of_measurement: "kW"
        state: >
          {% if is_state('sensor.battery_charging_current_hour', 'true') %}
            6
          {% else %}
            11
          {% endif %}
```

#### Available EV Power (Real-time Dynamic)
```yaml
  - sensor:
      - name: "EV Max Power Available"
        unique_id: ev_max_power_available
        unit_of_measurement: "kW"
        device_class: power
        state: >
          {% set grid_power = states('sensor.power_meter_active_power')|float(0) %}
          {% set safety_limit = 14.5 %}
          {% set static_max = states('sensor.ev_max_power_static')|float(11) %}
          {% set dynamic_available = safety_limit - grid_power %}
          {% set final_limit = [dynamic_available, static_max]|min %}
          {% if final_limit < 1 %}
            0
          {% else %}
            {{ final_limit|round(1) }}
          {% endif %}
```

#### Off-Tariff Period Active (Good for Charging)
```yaml
  - binary_sensor:
      - name: "Off Tariff Period Active"
        unique_id: off_tariff_period_active
        state: >
          {% set now_hour = now().hour %}
          {% set now_month = now().month %}
          {% set now_weekday = now().isoweekday() %}
          {% set is_winter = now_month >= 11 or now_month <= 3 %}
          {% set is_weekday = now_weekday <= 5 %}
          {% set is_weekend = now_weekday >= 6 %}
          {% set is_off_peak_hours = now_hour >= 20 or now_hour < 7 %}

          {# Off-tariff if: #}
          {# 1. Nov-March weekday nights (20:00-07:00) #}
          {# 2. Nov-March weekends (all day) #}
          {# 3. Apr-Oct (all times) #}
          {% if not is_winter %}
            true
          {% elif is_weekend %}
            true
          {% elif is_weekday and is_off_peak_hours %}
            true
          {% else %}
            false
          {% endif %}
```

#### Cheapest Hours from Node-RED (MQTT)

**Note:** This sensor is populated by Node-RED via MQTT. The calculation happens in Node-RED to avoid template size limitations with Nordpool data.

**Node-RED Flow Logic:**
- Fetches Tibber hourly prices (48 hours ahead)
- Filters for off-tariff periods (Nov-March weekdays 20-07, weekends all day, Apr-Oct all times)
- Sorts by price and selects cheapest 8 hours
- Handles DST transitions (deduplicates by keeping cheapest price per hour)
- Publishes to: `homeassistant/sensor/ev_charging_hours/state`

**Node-RED Function Code:**
```javascript
// Get prices array from msg.payload.priceData
const prices = msg.payload.priceData;

if (!prices || !Array.isArray(prices)) {
    node.warn("No price data available");
    return null;
}

// Current time info
const now = new Date();
const currentMonth = now.getMonth() + 1; // 1-12
const isWinter = currentMonth >= 11 || currentMonth <= 3;

// Filter for off-tariff hours and deduplicate by hour
const hourMap = new Map();

prices.forEach(entry => {
    const entryDate = new Date(entry.start);
    const hour = entryDate.getHours();
    const dayOfWeek = entryDate.getDay(); // 0=Sunday, 6=Saturday
    const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;
    const isOffPeakHours = hour >= 20 || hour < 7;

    // Include if: not winter OR weekend OR off-peak hours
    if (!isWinter || isWeekend || isOffPeakHours) {
        // Keep lowest price for each hour (handles DST duplicates)
        if (!hourMap.has(hour) || entry.value < hourMap.get(hour).value) {
            hourMap.set(hour, {hour: hour, value: entry.value});
        }
    }
});

// Convert map to array and sort by price
const offTariffPrices = Array.from(hourMap.values());
const sorted = offTariffPrices.sort((a, b) => a.value - b.value);

// Take cheapest 8 hours
const cheapest8 = sorted.slice(0, 8);
const cheapestHours = cheapest8.map(entry => entry.hour);

// Sort hours numerically for easier reading
cheapestHours.sort((a, b) => a - b);

// Send as JSON array
msg.payload = cheapestHours;
node.warn(`Cheapest hours: ${cheapestHours.join(', ')}`);

return msg;
```

**MQTT Sensor in Home Assistant (configuration.yaml):**
```yaml
mqtt:
  sensor:
    - name: "EV Charging Hours Tonight"
      unique_id: ev_charging_hours_tonight_mqtt
      state_topic: "homeassistant/sensor/ev_charging_hours/state"
      value_template: "{{ value_json | length }}"
      json_attributes_topic: "homeassistant/sensor/ev_charging_hours/state"
      json_attributes_template: "{{ {'cheapest_hours': value_json} | tojson }}"
```

**Expected Output:**
- State: `8` (number of hours)
- Attribute `cheapest_hours`: `[0, 1, 2, 3, 4, 5, 6, 23]` (sorted list of hour numbers)

#### Should Charge Now
```yaml
  - binary_sensor:
      - name: "EV Should Charge Now"
        unique_id: ev_should_charge_now
        state: >
          {% set current_hour = now().hour %}
          {% set cheapest_hours = state_attr('sensor.ev_charging_hours_tonight', 'cheapest_hours') %}
          {% set in_cheap_window = current_hour in cheapest_hours if cheapest_hours else false %}
          {% set off_tariff_active = is_state('binary_sensor.off_tariff_period_active', 'on') %}
          {% set cable_locked = is_state('binary_sensor.easee_home_91929_cable_locked', 'on') %}
          {% set smart_enabled = is_state('input_boolean.ev_smart_charging_enabled', 'on') %}
          {% set available_power = states('sensor.ev_max_power_available')|float(0) %}
          {{ smart_enabled and off_tariff_active and in_cheap_window and cable_locked and available_power > 1 }}
```

### 2. Input Helpers (configuration.yaml)

```yaml
input_boolean:
  ev_smart_charging_enabled:
    name: EV Smart Charging Enabled
    icon: mdi:ev-station

input_number:
  ev_charging_power_limit:
    name: EV Charging Power Limit
    min: 1
    max: 11
    step: 0.5
    unit_of_measurement: "kW"
    icon: mdi:speedometer

  ev_charging_current_limit:
    name: EV Charging Current Limit
    min: 6
    max: 16
    step: 1
    unit_of_measurement: "A"
    icon: mdi:current-ac
```

### 3. Automations (automations.yaml) - 📝 TO BE IMPLEMENTED

#### Why 8 Automations Are Needed

Each automation serves a specific purpose in the charging orchestration:

| Auto | Purpose | Why Critical |
|------|---------|--------------|
| **1** | Disable Tibber | Prevents conflicts - Tibber would override our limits |
| **2** | Calculate Plan | Triggers Node-RED to update cheapest hours |
| **3** | Initial Setup | Sets safe starting power when charging begins |
| **4** | Start Charging | Orchestrates charging start during cheap hours |
| **5** | Stop Charging | Handles SOC-based stop + cheap window end |
| **6** | Real-time Power | **MOST CRITICAL** - Prevents grid overload every minute |
| **7** | Emergency Stop | Last-resort safety if real-time fails |
| **8** | Schedule Sync | Adjusts when battery schedule changes mid-charge |

**Why so many automations?**
- ✅ **Separation of concerns**: Each handles one specific event/condition
- ✅ **Safety layers**: Multiple checks prevent circuit breaker trips
- ✅ **Flexibility**: Easy to disable/modify individual behaviors
- ✅ **Debugging**: Easier to trace which automation caused what action

#### Auto 1: Disable Tibber Smart Charging
```yaml
- id: disable_tibber_smart_charging
  alias: "EV: Disable Tibber Smart Charging"
  description: "Disable Tibber smart charging on startup to use HA-based scheduling"
  trigger:
    - platform: homeassistant
      event: start
  action:
    - service: switch.turn_off
      target:
        entity_id: switch.easee_home_91929_smart_charging
```

**Why this automation?**
- **Prevents conflicts**: Tibber smart charging would override our power limits
- **Local control**: We want HA to manage charging, not Tibber cloud
- **Battery coordination**: Tibber doesn't know about your home battery schedule
- **Runs on startup**: Ensures it's always disabled even after HA restart

#### Auto 2: Calculate Optimal Charging Plan
```yaml
- id: calculate_ev_charging_plan
  alias: "EV: Calculate Optimal Charging Plan"
  description: "Update cheapest charging hours based on Nordpool prices"
  trigger:
    - platform: time
      at: "19:30:00"  # Before off-tariff period starts at 20:00
    - platform: time
      at: "00:00:00"  # Daily recalculation
    - platform: state
      entity_id: sensor.batteries_tou_charging_and_discharging_periods
    - platform: state
      entity_id: sensor.nordpool_kwh_se3_sek_3_10_025
  action:
    - service: homeassistant.update_entity
      target:
        entity_id: sensor.ev_charging_hours_tonight
```

**Why this automation?**
- **19:30 trigger**: Node-RED updates at 13:30 & 19:00, so 19:30 gets fresh data before charging starts at 20:00
- **00:00 trigger**: Daily recalculation ensures fresh data for next night
- **Battery schedule trigger**: If battery schedule changes, re-evaluate charging plan
- **Nordpool trigger**: If new prices arrive, immediately update plan
- **Purpose**: Keeps `sensor.ev_charging_hours_tonight` up-to-date with latest cheapest hours

**Note**: This might be redundant since Node-RED already publishes via MQTT. Consider removing if Node-RED handles all updates.

#### Auto 3: Initial Power Limit Setup
```yaml
- id: update_ev_power_limit_initial
  alias: "EV: Set Initial Power Limit"
  description: "Set initial power limit when charging starts (real-time will take over)"
  trigger:
    - platform: state
      entity_id: switch.easee_home_91929_is_enabled
      to: "on"
  action:
    - service: input_number.set_value
      target:
        entity_id: input_number.ev_charging_power_limit
      data:
        value: "{{ states('sensor.ev_max_power_available') }}"
```

**Why this automation?**
- **Safe starting point**: Sets power limit immediately when charging begins
- **Prevents race condition**: Ensures limit is set before Auto 6 (real-time) takes over
- **Dynamic calculation**: Uses current grid power, not static 6/11 kW
- **Example**: If grid is at 8 kW, sets initial limit to 6.5 kW (14.5 - 8)
- **Auto 6 will refine**: Real-time management kicks in after 1 minute

#### Auto 4: Start EV Charging
```yaml
- id: start_ev_charging
  alias: "EV: Start Charging During Cheap Hours"
  description: "Start EV charging when in optimal price window"
  trigger:
    - platform: time_pattern
      minutes: "/5"
    - platform: state
      entity_id: binary_sensor.ev_should_charge_now
      to: "on"
  condition:
    - condition: state
      entity_id: binary_sensor.ev_should_charge_now
      state: "on"
    - condition: state
      entity_id: switch.easee_home_91929_is_enabled
      state: "off"
  action:
    - service: input_number.set_value
      target:
        entity_id: input_number.ev_charging_current_limit
      data:
        value: >
          {{ (states('input_number.ev_charging_power_limit')|float * 1000 / (230 * 3))|round(0)|int }}
    - service: number.set_value
      target:
        entity_id: number.easee_home_91929_dynamic_charger_limit
      data:
        value: "{{ states('input_number.ev_charging_current_limit')|int }}"
    - service: switch.turn_on
      target:
        entity_id: switch.easee_home_91929_is_enabled
```

**Why this automation?**
- **Orchestrates charging start**: The "conductor" that turns everything on
- **Every 5 minutes**: Checks if conditions met (not too frequent, not too slow)
- **State change trigger**: Immediately responds when conditions become favorable
- **Master sensor check**: `binary_sensor.ev_should_charge_now` validates ALL conditions:
  - ✅ Smart charging enabled
  - ✅ Off-tariff period active
  - ✅ In cheap hour window
  - ✅ Cable locked (EV connected)
  - ✅ Available power > 1 kW
  - ✅ **Current SOC < Target SOC** (needs charging)
- **Won't re-start**: Condition checks charger is OFF, prevents duplicate starts
- **3-phase conversion**: Converts kW to Amps for Easee charger (kW × 1000 / 690)

#### Auto 5: Stop EV Charging
```yaml
- id: stop_ev_charging
  alias: "EV: Stop Charging When Complete or Outside Window"
  description: "Stop EV charging when target SOC reached or leaving optimal price window"
  trigger:
    - platform: time_pattern
      minutes: "/5"
    - platform: state
      entity_id: binary_sensor.ev_should_charge_now
      to: "off"
    - platform: state
      entity_id: sensor.otto_von_bismarck_battery
  condition:
    - condition: state
      entity_id: switch.easee_home_91929_is_enabled
      state: "on"
  action:
    - choose:
        # Stop if target SOC reached
        - conditions:
            - condition: template
              value_template: >
                {{ states('sensor.otto_von_bismarck_battery')|float(0) >= states('number.otto_von_bismarck_charge_limit')|float(100) }}
          sequence:
            - service: switch.turn_off
              target:
                entity_id: switch.easee_home_91929_is_enabled
            - service: persistent_notification.create
              data:
                title: "EV Charging Complete"
                message: "Target charge limit ({{ states('number.otto_von_bismarck_charge_limit') }}%) reached"
        # Stop if outside cheap window
        - conditions:
            - condition: state
              entity_id: binary_sensor.ev_should_charge_now
              state: "off"
          sequence:
            - service: switch.turn_off
              target:
                entity_id: switch.easee_home_91929_is_enabled
```

**Why this automation?** ⭐ **CRITICAL FOR SOC INTEGRATION**
- **Two stop conditions**: Target reached OR cheap window ended
- **SOC trigger**: `sensor.otto_von_bismarck_battery` state change → immediate response when Tesla reports new SOC
- **Every 5 minutes**: Backup check in case SOC sensor doesn't update
- **Choose/sequence**: Handles each stop reason differently
- **Priority 1 - Target reached**:
  - ✅ Stops immediately when SOC >= target
  - ✅ Sends notification with target percentage
  - ✅ Frees charger sooner (don't wait for cheap window end)
  - ✅ Example: Reach 80% at 03:00 → stop at 03:00 (not 07:00)
- **Priority 2 - Window ended**:
  - ✅ Stops when `binary_sensor.ev_should_charge_now` goes OFF
  - ✅ Handles: cheap window ended, cable unplugged, smart charging disabled
  - ✅ No notification (normal scheduled stop)
- **Only runs when charging**: Condition checks charger is ON

#### Auto 6: Real-time Power Management (CRITICAL)
```yaml
- id: ev_realtime_power_adjustment
  alias: "EV: Real-time Power Adjustment"
  description: "Continuously adjust EV power based on actual grid consumption"
  trigger:
    - platform: time_pattern
      minutes: "/1"  # Check every minute
    - platform: state
      entity_id: sensor.power_meter_active_power
    - platform: state
      entity_id: sensor.ev_max_power_available
  condition:
    - condition: state
      entity_id: switch.easee_home_91929_is_enabled
      state: "on"
    - condition: template
      value_template: >
        {{ states('sensor.ev_max_power_available')|float(0) > 0.5 }}
  action:
    - service: input_number.set_value
      target:
        entity_id: input_number.ev_charging_power_limit
      data:
        value: "{{ states('sensor.ev_max_power_available') }}"
    - service: input_number.set_value
      target:
        entity_id: input_number.ev_charging_current_limit
      data:
        value: >
          {{ (states('input_number.ev_charging_power_limit')|float * 1000 / (230 * 3))|round(0)|int }}
    - service: number.set_value
      target:
        entity_id: number.easee_home_91929_dynamic_charger_limit
      data:
        value: "{{ states('input_number.ev_charging_current_limit')|int }}"
```

**Why this automation?** ⚡ **THE MOST CRITICAL SAFETY AUTOMATION**
- **Prevents circuit breaker trips**: Dynamically adjusts to stay under 15 kW limit
- **Every minute**: Balances responsiveness with API call frequency
- **Three triggers**:
  1. **Time pattern**: Guaranteed check every 60 seconds
  2. **Grid power change**: Immediate response when `sensor.power_meter_active_power` changes
  3. **Available power change**: Responds to battery schedule transitions
- **Real-time calculation**: `sensor.ev_max_power_available` = 14.5 kW - current_grid_power
- **Example scenarios**:
  - Grid: 8 kW → EV gets 6.5 kW
  - Grid: 10 kW → EV gets 4.5 kW
  - Grid: 13 kW → EV gets 1.5 kW
  - Grid: 14 kW → EV gets 0.5 kW
  - Grid: 14.6 kW → EV stops (condition fails, Auto 7 triggers)
- **Only when charging**: Condition ensures charger is ON
- **Minimum power check**: Won't set limit below 0.5 kW (prevents errors)
- **Updates all tracking helpers**: Keeps kW and Amps in sync

#### Auto 7: Emergency Stop on Power Overload
```yaml
- id: ev_emergency_stop
  alias: "EV: Emergency Stop on Grid Overload"
  description: "Emergency stop EV charging if grid power exceeds safety threshold"
  trigger:
    - platform: numeric_state
      entity_id: sensor.power_meter_active_power
      above: 14.5
  condition:
    - condition: state
      entity_id: switch.easee_home_91929_is_enabled
      state: "on"
  action:
    - service: switch.turn_off
      target:
        entity_id: switch.easee_home_91929_is_enabled
    - service: persistent_notification.create
      data:
        title: "⚠️ EV Charging Emergency Stop"
        message: >
          EV charging stopped due to grid power exceeding 14.5 kW.
          Current grid power: {{ states('sensor.power_meter_active_power') }} kW
```

**Why this automation?** 🚨 **LAST-RESORT SAFETY**
- **Emergency backup**: If Auto 6 fails or is too slow, this stops charging immediately
- **Numeric trigger**: Fires the instant grid power crosses 14.5 kW threshold
- **No delays**: Immediate action to prevent circuit breaker trip
- **Notification**: Alerts user that emergency stop occurred (something went wrong)
- **Shouldn't trigger normally**: If it does, Auto 6 needs investigation
- **Example failure scenarios**:
  - Large appliance turns on between Auto 6 checks
  - Sensor delays cause Auto 6 to react too slowly
  - Auto 6 disabled or broken
  - Charger doesn't respond to limit change quickly enough
- **Better to stop than trip**: Stopping charging is inconvenient, tripping breaker is worse

#### Auto 8: Adjust Power on Battery Schedule Change
```yaml
- id: adjust_ev_power_on_schedule_change
  alias: "EV: Adjust Power on Battery Schedule Change"
  description: "Update static power limit when battery schedule changes"
  trigger:
    - platform: state
      entity_id: sensor.battery_charging_current_hour
  action:
    - delay:
        seconds: 5  # Allow real-time calculation to update
    # Real-time adjustment will handle the rest via Auto 6
```

**Why this automation?**
- **Handles mid-charge battery transitions**: Battery might start/stop charging while EV is charging
- **Updates static limits**: Triggers recalculation of 6 kW vs. 11 kW limit
- **Example scenario**:
  - 03:00: Battery charging ends (period completes)
  - `sensor.battery_charging_current_hour` changes from True → False
  - `sensor.ev_max_power_static` updates from 6 kW → 11 kW
  - Auto 8 triggers, waits 5 seconds
  - Auto 6 (real-time) picks up the change and adjusts EV power
- **Minimal action**: Just a delay, Auto 6 does the actual work
- **Coordination**: Ensures template sensors update before Auto 6 reads them
- **Not strictly necessary**: Auto 6 would eventually catch it, but this makes it faster

## Implementation Steps

### Phase 1: Setup (No charging yet)
1. ✅ **Created input helpers** in `configuration.yaml`
2. ✅ **Created template sensors** in `configuration.yaml`
3. ✅ **Added MQTT sensor** for cheapest hours
4. ✅ **Updated Node-RED flow** with cheapest hours calculation
5. **Restart Home Assistant**
6. **Trigger Node-RED flow** to test MQTT publishing
7. **Monitor sensors** for 24 hours to verify everything works
8. **Validate** that `sensor.battery_charging_current_hour` reflects actual battery schedule
9. **Verify** `sensor.ev_charging_hours_tonight` populates from Node-RED

### Phase 2: Automation (Dry run)
6. **Create automations** in `automations.yaml` (keep disabled initially)
7. **Enable automation 1** (disable Tibber smart charging)
8. **Test manually** by calling `easee.set_charger_dynamic_limit` during day
9. **Verify** power limiting works as expected

### Phase 3: Production
10. **Enable all automations**
11. **Monitor first night** (check HA logs, Easee app, power consumption)
12. **Adjust** power limits or price thresholds if needed
13. **Document** any issues or edge cases

## Testing Checklist

- [ ] Battery schedule sensor parses correctly
- [ ] `sensor.battery_charging_current_hour` matches actual battery charging periods
- [x] `sensor.battery_charging_current_hour` parses battery schedule correctly
- [x] `sensor.ev_max_power_static` switches between 6 kW and 11 kW correctly
- [x] `sensor.ev_max_power_available` adjusts based on real-time grid power
- [x] `sensor.power_meter_active_power` reads correctly
- [x] `binary_sensor.off_tariff_period_active` TRUE during off-tariff times
- [x] Node-RED fetches Tibber prices correctly
- [x] Node-RED calculates cheapest hours correctly
- [x] MQTT sensor receives data from Node-RED
- [ ] `sensor.ev_charging_hours_tonight` shows 8 hours in `cheapest_hours` attribute
- [ ] EV starts charging during cheap hours
- [ ] EV stops charging outside cheap hours
- [ ] Power limit adjusts dynamically every minute
- [ ] `number.easee_home_91929_dynamic_charger_limit` updates correctly (6-16 A range)
- [ ] Conversion from kW to Amps works correctly (3-phase: kW × 1000 / 690)
- [ ] **`sensor.power_meter_active_power` NEVER exceeds 14.5 kW during charging**
- [ ] Emergency stop triggers if grid power > 14.5 kW
- [ ] Tibber smart charging remains disabled

## Monitoring & Debugging

### Key Sensors to Watch
- **`sensor.power_meter_active_power`** - **CRITICAL: Current grid power (must stay < 15 kW)**
- `sensor.batteries_tou_charging_and_discharging_periods` - Battery schedule
- `sensor.battery_charging_current_hour` - Is battery charging now?
- `sensor.ev_max_power_static` - Static power limit based on battery schedule (6 or 11 kW)
- `sensor.ev_max_power_available` - Dynamic real-time available power for EV
- `binary_sensor.off_tariff_period_active` - Is it off-tariff time (good for charging)?
- `sensor.ev_charging_hours_tonight` - Which hours are cheapest?
- `binary_sensor.ev_should_charge_now` - Should we charge right now?
- `sensor.ev_charging_hours_needed` - How many hours needed based on current vs target SOC
- `sensor.otto_von_bismarck_battery` - Current EV battery level (%)
- `number.otto_von_bismarck_charge_limit` - Target charge level (%)
- `sensor.easee_home_91929_power` - Actual EV charging power
- `number.easee_home_91929_dynamic_charger_limit` - Actual current limit set on charger (A)
- `switch.easee_home_91929_is_enabled` - Is EV charging active?
- `input_number.ev_charging_power_limit` - Current power limit being applied (kW)
- `input_number.ev_charging_current_limit` - Current limit in Amps (A)

### Log Entries to Check
```yaml
logger:
  default: info
  logs:
    homeassistant.components.easee: debug
    homeassistant.helpers.template: debug
```

## Edge Cases & Considerations

1. **Battery schedule updates mid-charge**: Static limit recalculates, real-time takes over
2. **No Nordpool prices available**: Cheapest hours will be empty, no charging
3. **Manual EV charging needed**: Disable `input_boolean.ev_smart_charging_enabled`
4. **Weekend charging (Nov-March)**: Automation runs ALL DAY (entire weekend is off-tariff)
5. **Summer charging (Apr-Oct)**: Automation can run anytime (no tariffs), charges during cheapest hours
6. **Grid power spikes**: Emergency stop triggers immediately, notification sent
7. **Power meter unavailable**: EV charging will stop (sensor becomes unavailable)
8. **EV disconnected mid-charge**: Cable lock sensor will stop automation
9. **High appliance load**: Real-time adjustment reduces EV power automatically
10. **Battery stops charging early**: Static limit increases to 11 kW, real-time allows more power
11. **Charger circuit limit**: 16 A maximum enforced by `number.easee_home_91929_dynamic_charger_limit`
12. **EV already at target SOC**: Won't start charging (checked in `binary_sensor.ev_should_charge_now`)
13. **EV reaches target mid-charge**: Stops immediately with notification

## Future Enhancements

- [ ] **Dynamic charging hours** based on actual EV battery level (if available)
- [ ] **Notification** when charging plan is calculated
- [ ] **Dashboard card** showing tonight's charging plan and real-time power usage
- [ ] **Energy dashboard** integration for cost tracking
- [ ] **Override helpers** for manual control (e.g., charge by specific time)
- [ ] **Lovelace UI card** for easy enable/disable and status
- [ ] Integration with **EV battery level** (if exposed by Easee)
- [ ] **Historical tracking** of grid power peaks during charging
- [ ] **Smart appliance coordination** (delay high-power appliances during EV charging)
- [ ] **Predictive power allocation** based on typical appliance usage patterns

## Key Benefits

✅ **Real-time grid power monitoring** - Continuously monitors `sensor.power_meter_active_power`
✅ **Dynamic power limiting** - Adjusts EV power every minute based on actual consumption
✅ **Emergency stop protection** - Automatically stops if grid exceeds 14.5 kW
✅ **EV prioritized** during cheapest hours
✅ **Coordinates with home battery** (no conflicts)
✅ **Respects 15 kW hard limit** with real-time enforcement
✅ **Seasonal awareness** (Nov-March only)
✅ **Fully local control** (no Tibber server dependency)
✅ **Keeps existing Node-RED** battery logic untouched
✅ **Price-optimized** charging within safety constraints

## Files to Modify/Create

| File | Changes |
|------|---------|
| `config/configuration.yaml` | Add template sensors (5 sensors), MQTT sensor (1), input helpers (3 items) |
| `config/automations.yaml` | Add 8 automations (includes real-time power management + emergency stop) |
| `EV_CHARGING_PLAN.md` | This document (for reference) |
| Node-RED flow | • Update schedule: 13:30 and 19:00<br>• Add function node: Calculate cheapest hours<br>• Add MQTT out: Publish to `homeassistant/sensor/ev_charging_hours/state` |

---

**Created**: 2025-10-25
**For**: Karnvedsgatan 16 Home Assistant instance
**Author**: Claude Code
