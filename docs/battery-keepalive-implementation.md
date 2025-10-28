# Huawei Battery Keep-Alive Implementation

**Status:** Planning
**Version:** 1.2
**Created:** 2025-10-26
**Last Updated:** 2025-10-26
**Inverter Model:** SUN2000-10KTL-M1
**Home Assistant Integration:** Huawei Solar (wlcrs/huawei_solar)

---

## Table of Contents

1. [Problem Statement](#problem-statement)
2. [Root Cause Analysis](#root-cause-analysis)
3. [Solution Design](#solution-design)
4. [Technical Specifications](#technical-specifications)
5. [Implementation Checklist](#implementation-checklist)
6. [Testing & Verification](#testing--verification)
7. [Maintenance & Monitoring](#maintenance--monitoring)
8. [Troubleshooting](#troubleshooting)
9. [References](#references)

---

## Problem Statement

### Issue Description

The Huawei SUN2000-10KTL-M1 inverter enters standby mode during nighttime when there is:
- No solar production (nighttime)
- No battery activity (gap between TOU periods)
- A scheduled battery charge period that should start at 03:30

**Observed Behavior:**
- Discharge period ends: 02:30
- Gap period: 02:30 - 03:30 (no activity scheduled)
- Charge period scheduled: 03:30 - 10:00
- **Problem:** Inverter goes into standby at 02:30 and fails to wake at 03:30
- **Result:** Charging only starts when solar production begins (sunrise)

### Impact

- Battery charge periods are missed during expensive tariff hours
- Optimization based on dynamic Tibber pricing fails
- Node-RED scheduler calculations are wasted
- Cannot take advantage of cheap nighttime electricity rates

### Current Configuration

- **Battery Working Mode:** Time of Use (TOU)
- **Pricing:** Dynamic (Tibber)
- **Scheduler:** Node-RED (creates custom schedules based on prices and solar forecast)
- **TOU Schedule Management:** Node-RED writes schedule to inverter twice daily
- **Capacity Control Mode:** Disabled

---

## Root Cause Analysis

### Research Findings

1. **Inverter Standby Behavior**
   - SUN2000 inverters enter standby mode when "external environment does not meet requirements for starting"
   - Primary trigger: PV string output power not suitable for grid connection
   - During standby, inverter "continuously detects operation status" to determine when to resume

2. **M1 Model Characteristics**
   - M1 models have mixed nighttime behavior (better than M0, not as robust as M2)
   - Some users report Modbus connectivity issues during standby
   - Battery can enter hibernation/standby/operating modes independently

3. **TOU Mode Limitations**
   - Grid charging ONLY permitted during defined "charge time segments"
   - Outside charge periods: "batteries do not discharge, PV system and grid supply loads"
   - Gap periods (neither charge nor discharge) may allow inverter to sleep
   - No ability to set different power levels per TOU period

4. **Evidence from Community**
   - GitHub issues show battery configuration loss during nighttime standby
   - Users report "Modbus client is not connected" errors at night
   - Some implementations use "charge from grid always enabled" to prevent standby
   - Battery status shows "standby" vs "running" depending on activity

### Why TOU Schedule Alone Doesn't Work

**Hypothesis:** When the inverter enters standby due to no solar + no battery activity, it does NOT reliably wake up for scheduled TOU charge periods. It only wakes when actual solar production is detected.

**Supporting Evidence:**
- User observation: "As soon as the sun went up, the charge from grid started"
- This indicates the 03:30 TOU schedule was ignored, sunrise triggered wake-up
- No documented "wake-up" mechanism for TOU schedule start times

---

## Solution Design

### Selected Approach: Mode Switching with Keep-Alive

**Strategy:** During gap periods at night, temporarily switch to "Maximise Self Consumption" mode with minimal grid charging to keep inverter awake.

### Solution Overview

```
Timeline:
├─ 00:00-02:30: TOU Discharge Period (controlled by Node-RED schedule)
├─ 02:30-03:30: GAP - Keep-Alive Activates (if sun below horizon)
│               └─ Switch to "Maximise Self Consumption"
│               └─ Set power to 20W (charge_from_grid stays ON)
├─ 03:30-10:00: TOU Charge Period (switch back to TOU mode)
│               └─ Set power to 5000W
└─ Rest of day: Normal TOU operation
                 (charge_from_grid remains ON throughout)
```

### Why This Works

1. **Maximise Self Consumption Mode** allows grid charging when `switch.battery_charge_from_grid` is enabled (unlike TOU which restricts to time segments)
2. **20W charging** provides minimal activity to keep battery in "operating" mode without significant cost
3. **Sun condition** prevents unnecessary keep-alive during daytime (solar keeps inverter awake)
4. **Mode switching at 03:30** returns to TOU for the main optimized charge period

### Handling the 100% SOC Edge Case

**Critical Scenario:** What if battery is fully charged when keep-alive should activate?

**The Problem:**
```
02:30: Discharge period ends, battery at 100% SOC
       Keep-alive tries to charge at 20W
       → Battery refuses (can't overcharge)
       → No battery activity occurs
       → Inverter goes to standby anyway
       → Keep-alive fails!

Later:
17:00: TOU discharge period should start (expensive tariff)
       → Inverter still in standby
       → Doesn't wake for discharge period
       → You buy expensive grid power instead of using battery
       → 💸 Major cost impact
```

**The Solution: Dual Power Limiting**

Instead of trying to charge a full battery, we limit **both charge AND discharge** power:

```yaml
During Keep-Alive:
  - number.battery_grid_charge_maximum_power = 20W
  - number.battery_maximum_discharging_power = 20W
  - Mode: "Maximise Self Consumption"

Result:
  If SOC < 100%: Battery charges from grid at max 20W
  If SOC = 100%: Battery discharges to house at max 20W

Either way: Battery has activity → Stays in "operating" mode → Inverter stays awake
```

**Key Insight:** House load is always >20W (fridge, heating, baseload), so:
- At 100% SOC, battery supplies 20W to house
- Grid supplies the remaining load (e.g., if house uses 500W, grid supplies 480W)
- This 20W discharge activity keeps battery/inverter active
- Prevents missing critical discharge periods later in the day

**Why Limit Discharge Power?**

Without discharge limit at 100% SOC:
- House load: 500W → Battery discharges 500W
- Over 1 hour gap: 500Wh drained from battery
- 03:30 charge period: Must recharge those 500Wh
- **Result:** Wasteful battery cycling with no benefit

With 20W discharge limit:
- House load: 500W → Battery discharges 20W, grid supplies 480W
- Over 1 hour gap: Only 20Wh from battery
- **Result:** Minimal battery usage, still prevents standby

### Simplified Switch Management

**Design Decision: Keep `switch.battery_charge_from_grid` ON Permanently**

Instead of toggling the `battery_charge_from_grid` switch on and off, we keep it enabled at all times and control charging solely through:
1. Battery working mode (TOU vs Maximise Self Consumption)
2. Power setting (`battery_grid_charge_maximum_power`)

**Why This Works:**
- In **TOU mode**: The TOU schedule controls when grid charging actually occurs, regardless of switch state
  - During charge periods: Grid charges (per schedule)
  - During discharge periods: Battery discharges (per schedule)
  - The switch being ON doesn't interfere with discharge periods
- In **Maximise Self Consumption mode**: The switch being ON enables grid charging, power setting controls rate
- **Simpler logic**: One less state to manage, fewer opportunities for errors
- **No interference**: The switch controls charging *capability*, TOU schedule controls charging *behavior*

**Power Setting Strategy:**

We limit **BOTH** charge and discharge power during keep-alive to prevent unnecessary battery cycling:

- **During keep-alive (self-consumption mode):**
  - Charge power: 20W (minimal grid charging if SOC < 100%)
  - Discharge power: 20W (minimal house supply if SOC = 100%)
- **When returning to TOU:**
  - Both charge and discharge: 5000W (full power for normal operation)

**Why Limit Both?**
- **Prevents wasteful discharge:** At 100% SOC with high house load (e.g., 500W), battery would discharge 500Wh during gap, then need recharging during expensive charge period
- **Minimal activity only:** 20W discharge is enough to keep inverter awake
- **Grid supplies excess:** House load >20W comes from grid during gap (acceptable for 1 hour)

**Summary:**
```
switch.battery_charge_from_grid:     ON (always)
                                     └─> Never toggled

During gap (self-consumption mode):
  ├─> number.battery_grid_charge_maximum_power: 20W
  └─> number.battery_maximum_discharging_power: 20W
      └─> Minimal charge/discharge activity to prevent standby

During TOU operation:
  ├─> number.battery_grid_charge_maximum_power: 5000W
  └─> number.battery_maximum_discharging_power: 5000W
      └─> TOU schedule controls actual charging/discharging at full power
```

### Alternative Approaches Considered

#### Option B: Extend TOU Schedule
- **Idea:** Add a 02:30-03:30 charge period at 200W in TOU schedule
- **Problem:** Cannot set different power levels per TOU period (only one global `battery_grid_charge_maximum_power`)
- **Result:** Would charge at same rate during gap as main period (wasteful) or low rate during main period (ineffective)
- **Status:** Not viable

#### Option C: Full Home Assistant Control
- **Idea:** Use "Maximise Self Consumption" permanently, HA controls all charging via automations
- **Problem:** Requires extensive Node-RED refactoring, more complex maintenance
- **Benefit:** Most control and reliability
- **Status:** Future enhancement option

### Energy Cost Analysis

**Keep-Alive Energy Usage:**
- Power: 20W (charge or discharge)
- Duration: ~1 hour per night (gap period)
- Energy: 0.02 kWh per night (from grid if charging, to house if discharging)
- Cost: ~€0.01 per night (assuming €0.50/kWh worst case)
- **Annual cost: ~€3.65**

**Battery Cycling Prevention:**
- Without discharge limit: Up to 500Wh wasteful discharge during gap
- With 20W discharge limit: Only 20Wh from battery
- **Savings:** ~480Wh per night not wasted on cycling
- **Battery longevity:** Reduced unnecessary charge/discharge cycles

**Benefit:**
- Ensures reliable charging during cheap hours
- Prevents missing expensive-hour discharge periods
- Prevents wasteful battery cycling
- Savings from optimized charging + avoiding expensive tariffs >> keep-alive cost

---

## Technical Specifications

### Home Assistant Entities Used

#### Sensors (Read)
- `sensor.battery_time_of_use_periods` - Contains TOU schedule from Node-RED
- `sun.sun` - Sun position and state (above_horizon / below_horizon)
- `sensor.battery_status` - Battery operational status
- `sensor.battery_charge_discharge_power` - Current battery power

#### Controls (Write)
- `select.battery_working_mode` - Switch between TOU / Maximise Self Consumption
- `switch.battery_charge_from_grid` - Enable/disable grid charging (kept ON permanently)
- `number.battery_grid_charge_maximum_power` - Set charging power limit (W)
- `number.battery_maximum_discharging_power` - Set discharging power limit (W)

#### New Entities to Create
- `input_boolean.battery_keepalive_enabled` - Master enable/disable switch
- `input_number.battery_keepalive_power` - Keep-alive power setting (default: 20W)
- `sensor.battery_keepalive_should_be_active` - Template sensor to detect gap periods

### Automation Logic

#### Trigger Conditions
- **Time pattern:** Every 5 minutes (to check state)
- **State change:** `sun.sun` state changes
- **State change:** `sensor.battery_time_of_use_periods` updates

#### Keep-Alive Activation Conditions
```yaml
ALL of:
  - input_boolean.battery_keepalive_enabled = ON
  - sun.sun state = "below_horizon"
  - Current time is in gap period (after discharge, before charge)
  - select.battery_working_mode = "TOU" (not already in keep-alive)
```

#### Keep-Alive Deactivation Conditions
```yaml
ANY of:
  - input_boolean.battery_keepalive_enabled = OFF
  - sun.sun state = "above_horizon"
  - Charge period has started
  - No longer in gap period
```

#### Actions on Activation
1. Set `number.battery_grid_charge_maximum_power` to 20W
2. Set `number.battery_maximum_discharging_power` to 20W
3. Set `select.battery_working_mode` to "Maximise Self Consumption"
4. Log: "Battery keep-alive activated (20W charge/discharge limit in self-consumption mode)"

**Note:** `switch.battery_charge_from_grid` remains ON permanently (see "Simplified Switch Management" above)

#### Actions on Deactivation
1. Set `number.battery_grid_charge_maximum_power` to 5000W
2. Set `number.battery_maximum_discharging_power` to 5000W
3. Set `select.battery_working_mode` to "TOU"
4. Log: "Battery keep-alive deactivated, returning to TOU mode with full power"

**Note:** `switch.battery_charge_from_grid` remains ON (TOU schedule controls actual charging behavior)

### Template Sensor Logic

**Purpose:** Detect if we are currently in a gap period where keep-alive should run.

**Requirements:**
- Parse `sensor.battery_time_of_use_periods` attribute data
- Identify discharge period end time
- Identify charge period start time
- Determine if current time is between these (gap)

**Format of battery_time_of_use_periods:**
```
Period 1: HH:MM-HH:MM/DAY/+POWER or -POWER
Period 2: HH:MM-HH:MM/DAY/+POWER or -POWER
...
```
- `+POWER` = charge period
- `-POWER` = discharge period
- `DAY` = day of week (1-7)

---

## Implementation Checklist

### Phase 1: Preparation
- [ ] Pull latest config from Home Assistant (`make pull`)
- [ ] Backup current configuration (`make backup`)
- [ ] Review current TOU schedule in `sensor.battery_time_of_use_periods`
- [ ] Document current battery working mode and settings
- [ ] **Set `switch.battery_charge_from_grid` to ON** (will remain ON permanently)
- [ ] Note current sunrise/sunset times for test planning
- [ ] Verify Huawei Solar integration is functioning properly
- [ ] Check current battery SOC and status

### Phase 2: Create Input Helpers

#### Create Master Enable Switch
- [ ] Add `input_boolean.battery_keepalive_enabled` to configuration.yaml
- [ ] Set initial value: `true`
- [ ] Add icon: `mdi:battery-heart`
- [ ] Add friendly name: "Battery Keep-Alive Enabled"

#### Create Power Setting Helper
- [ ] Add `input_number.battery_keepalive_power` to configuration.yaml
- [ ] Set min: 10, max: 500, step: 10, unit: W
- [ ] Set initial/default value: 20W
- [ ] Add icon: `mdi:flash`
- [ ] Add friendly name: "Battery Keep-Alive Power"

### Phase 3: Create Template Sensor

#### Design Template Logic
- [ ] Determine how to parse `sensor.battery_time_of_use_periods` attributes
- [ ] Identify attribute structure (test with Developer Tools → States)
- [ ] Write logic to extract discharge end time
- [ ] Write logic to extract charge start time
- [ ] Write logic to compare current time against gap window
- [ ] Handle edge cases (no schedule, malformed data, day transitions)

#### Implement Template Sensor
- [ ] Add `sensor.battery_keepalive_should_be_active` to configuration.yaml
- [ ] Set device_class: `running` or appropriate class
- [ ] Add state: true/false based on gap detection
- [ ] Add attributes: discharge_end, charge_start, gap_duration
- [ ] Add friendly name: "Battery Keep-Alive Should Be Active"
- [ ] Test template in Developer Tools → Template before deploying

### Phase 4: Create Main Automation

#### Automation: Battery Keep-Alive Manager
- [ ] Create new automation in automations.yaml
- [ ] Add ID and alias: "Battery Keep-Alive Manager"
- [ ] Add description documenting purpose

#### Configure Triggers
- [ ] Add time_pattern trigger: every 5 minutes
- [ ] Add state trigger: `sun.sun` state changes
- [ ] Add state trigger: `sensor.battery_time_of_use_periods` changes
- [ ] Add state trigger: `input_boolean.battery_keepalive_enabled` changes
- [ ] Set mode: `restart` (to handle rapid state changes)

#### Configure Conditions (Choose Block)
- [ ] Create "Activate Keep-Alive" branch
  - [ ] Condition: `input_boolean.battery_keepalive_enabled` is ON
  - [ ] Condition: `sun.sun` state is `below_horizon`
  - [ ] Condition: `sensor.battery_keepalive_should_be_active` is ON
  - [ ] Condition: `select.battery_working_mode` is NOT "Maximise Self Consumption"

- [ ] Create "Deactivate Keep-Alive" branch
  - [ ] Condition: OR block
    - [ ] `input_boolean.battery_keepalive_enabled` is OFF
    - [ ] `sun.sun` state is `above_horizon`
    - [ ] `sensor.battery_keepalive_should_be_active` is OFF
  - [ ] Condition: `select.battery_working_mode` IS "Maximise Self Consumption"

#### Configure Actions - Activation
- [ ] Set `number.battery_grid_charge_maximum_power` to `{{ states('input_number.battery_keepalive_power') }}`
- [ ] Set `number.battery_maximum_discharging_power` to `{{ states('input_number.battery_keepalive_power') }}`
- [ ] Delay 2 seconds (allow power settings to apply)
- [ ] Set `select.battery_working_mode` to "Maximise Self Consumption"
- [ ] Delay 2 seconds (optional - allow mode change to settle)
- [ ] Send notification (optional): "Battery keep-alive activated"
- [ ] Log to system: "Battery keep-alive: Self-consumption mode with {{X}}W charge/discharge limit"

**Note:** Do NOT toggle `switch.battery_charge_from_grid` - it stays ON permanently

#### Configure Actions - Deactivation
- [ ] Set `number.battery_grid_charge_maximum_power` to 5000
- [ ] Set `number.battery_maximum_discharging_power` to 5000
- [ ] Delay 2 seconds (allow power settings to apply)
- [ ] Set `select.battery_working_mode` to "TOU"
- [ ] Delay 2 seconds (optional - allow mode change to settle)
- [ ] Send notification (optional): "Battery keep-alive deactivated, TOU resumed"
- [ ] Log to system: "Battery keep-alive: TOU mode restored with full power"

**Note:** Do NOT toggle `switch.battery_charge_from_grid` - it stays ON permanently

### Phase 5: Validation & Testing

#### Pre-Deployment Validation
- [ ] Run YAML syntax validation: `python tools/yaml_validator.py`
- [ ] Run reference validation: `python tools/reference_validator.py`
- [ ] Run full validation: `make validate`
- [ ] Fix any validation errors
- [ ] Review automation in HA UI automation editor (after push)

#### Deployment
- [ ] Push configuration to Home Assistant: `make push`
- [ ] Wait for HA to reload configuration
- [ ] Check for errors in HA logs
- [ ] Verify new entities appear in Developer Tools → States
- [ ] Check automation is enabled and not in error state

#### Initial Testing (Daytime)
- [ ] Manually verify `sensor.battery_keepalive_should_be_active` calculation
- [ ] Test template sensor with different mock TOU schedules
- [ ] Verify sun.sun state is correctly detected
- [ ] Manually trigger automation to test logic paths
- [ ] Set `input_boolean.battery_keepalive_enabled` OFF and verify no action
- [ ] Set it back ON for nighttime test

#### Nighttime Test 1: Manual Override Test
- [ ] Before automatic gap (e.g., 02:00), manually trigger keep-alive:
  - [ ] Ensure `switch.battery_charge_from_grid` is ON (should be ON permanently)
  - [ ] Set `number.battery_grid_charge_maximum_power` to 20W
  - [ ] Set `number.battery_maximum_discharging_power` to 20W
  - [ ] Set `select.battery_working_mode` to "Maximise Self Consumption"
- [ ] Observe battery behavior (check `sensor.battery_charge_discharge_power`):
  - [ ] If SOC < 100%: Should charge at ~20W
  - [ ] If SOC = 100%: Should discharge at ~20W (supplying house)
- [ ] Monitor `sensor.battery_status` - should show "running" not "standby"
- [ ] Verify Modbus connection remains active (entities don't go unavailable)
- [ ] Check house is drawing from grid for load >20W (if applicable)
- [ ] Document results and battery SOC

#### Nighttime Test 2: Automatic Keep-Alive Test
- [ ] Ensure `input_boolean.battery_keepalive_enabled` is ON
- [ ] Verify `switch.battery_charge_from_grid` is ON (should already be ON)
- [ ] Note battery SOC before gap period
- [ ] Monitor automation from 02:00 onwards
- [ ] At ~02:30 (discharge end), verify:
  - [ ] `sensor.battery_keepalive_should_be_active` becomes true
  - [ ] Automation triggers and switches to self-consumption mode
  - [ ] Both charge AND discharge power set to 20W
  - [ ] `switch.battery_charge_from_grid` remains ON (unchanged)
  - [ ] Battery activity begins (charge if <100%, discharge if =100%)
  - [ ] Battery status remains "running"
- [ ] Monitor through gap period (02:30-03:30):
  - [ ] Verify battery power stays at ~20W (charge or discharge)
  - [ ] If house load >20W, verify grid supplies the difference
  - [ ] Verify battery SOC changes minimally (<0.05 kWh)
  - [ ] Verify no standby mode entry
  - [ ] Verify Modbus connection stable
- [ ] At 03:30 (charge period start), verify:
  - [ ] Automation detects end of gap
  - [ ] Mode switches back to TOU
  - [ ] Both charge and discharge power increase to 5000W
  - [ ] Main charge period executes normally
- [ ] Document all observations, timestamps, and SOC changes

#### Sunrise Edge Case Test
- [ ] Test what happens if sunrise occurs during keep-alive period
- [ ] Verify automation deactivates keep-alive when sun rises
- [ ] Confirm smooth transition to normal operation
- [ ] Document behavior

### Phase 6: Monitoring Setup

#### Create Dashboard Cards (Optional)
- [ ] Create entities card showing:
  - [ ] `input_boolean.battery_keepalive_enabled` (toggle)
  - [ ] `input_number.battery_keepalive_power` (slider)
  - [ ] `sensor.battery_keepalive_should_be_active` (status)
  - [ ] `select.battery_working_mode` (current mode)
  - [ ] `sensor.battery_status` (battery status)
  - [ ] `sensor.battery_charge_discharge_power` (current power)
- [ ] Create history graph showing mode changes over 24h
- [ ] Create logbook card filtered to keep-alive automation

#### Setup Notifications (Optional)
- [ ] Add notification when keep-alive activates
- [ ] Add notification when keep-alive deactivates
- [ ] Add notification if keep-alive fails (mode change unsuccessful)
- [ ] Configure notification targets (mobile app, persistent notification)

#### Setup Long-Term Monitoring
- [ ] Ensure `sensor.battery_status` is logged to InfluxDB
- [ ] Ensure `select.battery_working_mode` is logged
- [ ] Ensure `sensor.battery_charge_discharge_power` is logged
- [ ] Create Grafana dashboard (if applicable) to track:
  - [ ] Keep-alive activation times
  - [ ] Battery status changes
  - [ ] Charge success rate at 03:30

### Phase 7: Documentation & Rollback

#### Documentation
- [ ] Update this document with final implementation details
- [ ] Document any deviations from plan
- [ ] Add actual entity IDs used
- [ ] Document observed behavior during testing
- [ ] Note any issues encountered and resolutions
- [ ] Update CLAUDE.md if needed with keep-alive information

#### Rollback Plan Preparation
- [ ] Document steps to disable keep-alive:
  1. Set `input_boolean.battery_keepalive_enabled` to OFF
  2. Manually verify mode is back to TOU
  3. If issues persist, disable automation
  4. Restore from backup if necessary
- [ ] Keep backup accessible for 1 week
- [ ] Document what to monitor to determine if rollback needed

---

## Testing & Verification

### Pre-Deployment Tests

**Syntax Validation:**
```bash
make validate
```

**Expected:** All validation passes without errors.

**Manual Checks:**
- All entity IDs referenced in templates and automations exist
- Template sensors can be evaluated in Developer Tools
- No circular dependencies in template logic

### Deployment Tests

**Configuration Reload:**
```bash
make push
# Then in HA: Configuration → Server Controls → Check Configuration
```

**Expected:**
- Configuration valid
- No errors in home-assistant.log
- New entities appear within 60 seconds

### Functional Tests

#### Test 1: Gap Detection (Daytime)

**Setup:** Current time is outside any TOU period, sun is up.

**Steps:**
1. Check `sensor.battery_keepalive_should_be_active` state
2. Check `sun.sun` state

**Expected:**
- Gap sensor should reflect actual schedule gap status
- Keep-alive should NOT activate (sun is up)

#### Test 2: Manual Mode Switch

**Setup:** Daytime, manual control.

**Steps:**
1. Verify `switch.battery_charge_from_grid` is ON (should already be ON)
2. Set `number.battery_grid_charge_maximum_power` to 20W
3. Set `number.battery_maximum_discharging_power` to 20W
4. Set `select.battery_working_mode` to "Maximise Self Consumption"
5. Observe for 5 minutes

**Expected:**
- Mode changes successfully
- Battery behavior depends on SOC:
  - If SOC < 100%: Charges at ~20W
  - If SOC = 100%: Discharges at ~20W (if house has load)
- No errors in logs
- Can manually switch back to TOU
- Switch remains ON throughout (never toggled)
- Discharge power limited to 20W even if house load is higher

#### Test 3: Automation Dry Run

**Setup:** Before nighttime gap, with sun below horizon (or manually set sensor).

**Steps:**
1. Enable automation
2. Manually set `sensor.battery_keepalive_should_be_active` to ON (if possible via helper)
3. Wait for automation to trigger
4. Observe actions

**Expected:**
- Automation triggers within 5 minutes
- Mode switches to self-consumption
- Grid charging enables
- Power sets to 20W
- Logs show activation

#### Test 4: Full Nighttime Cycle

**Setup:** Night with actual gap period (02:30-03:30).

**Steps:**
1. Monitor from 02:00 to 04:00
2. Record state changes at:
   - 02:30 (discharge ends, gap begins)
   - 03:30 (charge period starts)
   - 04:00 (verify stable charging)

**Expected Timeline:**

| Time  | Expected State | Battery Mode | Charge from Grid | Charge Power | Discharge Power | Battery Status |
|-------|----------------|--------------|------------------|--------------|-----------------|----------------|
| 02:00 | TOU Discharge  | TOU          | ON               | 5000W        | 5000W           | Running        |
| 02:30 | Keep-Alive On  | Self-Cons.   | ON               | 20W          | 20W             | Running        |
| 02:35 | Keep-Alive Active | Self-Cons. | ON             | 20W          | 20W             | Running        |
| 03:30 | TOU Charge     | TOU          | ON               | 5000W        | 5000W           | Running        |
| 04:00 | TOU Charge     | TOU          | ON               | 5000W        | 5000W           | Running        |

**Key Verification Points:**
- [ ] No standby entry at 02:30
- [ ] Inverter remains responsive throughout
- [ ] Smooth transition to TOU at 03:30
- [ ] Main charge executes as scheduled

### Success Criteria

**Primary:**
- ✅ Inverter does NOT enter standby during gap period
- ✅ TOU charge period starts successfully at 03:30
- ✅ Battery charges at expected rate during main period

**Secondary:**
- ✅ Keep-alive only activates when sun is down
- ✅ Energy consumption during keep-alive ≤ 0.025 kWh
- ✅ No Modbus connection issues during night
- ✅ Automation runs reliably every night

**Failure Criteria (Requires Troubleshooting):**
- ❌ Inverter still enters standby despite keep-alive
- ❌ Mode switching fails (errors in log)
- ❌ Charging does NOT start at 03:30
- ❌ Excessive energy consumption (>0.1 kWh per gap)

---

## Maintenance & Monitoring

### Daily Monitoring (First Week)

**What to Check:**
- Battery status sensor at 02:30 and 03:30
- Automation execution log (Settings → Automations → Battery Keep-Alive Manager → Traces)
- Battery charge history graph (verify 03:30 charge starts)
- Energy dashboard (verify minimal keep-alive consumption)

**Where to Check:**
- Home Assistant Logbook (filter by keep-alive entities)
- History graphs (battery power, mode, status)
- InfluxDB/Grafana if configured

### Weekly Monitoring (Ongoing)

**Metrics to Track:**
- Number of successful keep-alive activations
- Number of successful 03:30 charge starts
- Total keep-alive energy consumption (weekly)
- Any missed charge periods or errors

**Review:**
- Automation traces for any failures
- Home Assistant logs for errors related to Huawei integration
- Battery performance (is charging completing as expected?)

### Seasonal Adjustments

**Sunrise/Sunset Changes:**
- Keep-alive automatically adjusts (uses `sun.sun` state)
- No manual intervention needed
- Verify behavior during summer (very early sunrise)

**TOU Schedule Changes:**
- If Node-RED modifies gap timing, keep-alive adapts automatically
- Template sensor recalculates based on new schedule
- No configuration changes needed

### Performance Indicators

**Healthy Operation:**
- Keep-alive activates every night (if gap exists)
- Battery never shows "standby" during gap
- All charge periods execute on schedule
- Energy consumption ~0.02 kWh per night

**Concerning Patterns:**
- Keep-alive activates but battery still enters standby
- Frequent automation failures or errors
- Charge periods still missed occasionally
- Higher than expected energy consumption

---

## Troubleshooting

### Problem: Keep-Alive Doesn't Activate

**Symptoms:**
- Gap period occurs, sun is down, but mode doesn't switch

**Checks:**
1. Is `input_boolean.battery_keepalive_enabled` ON?
2. Is `sensor.battery_keepalive_should_be_active` calculating correctly?
3. Is automation enabled?
4. Check automation traces for last execution
5. Check conditions - which one is failing?

**Solutions:**
- Verify gap detection logic in template sensor
- Check sun.sun state is actually "below_horizon"
- Review automation conditions in trace
- Test template manually in Developer Tools

### Problem: Mode Switch Fails

**Symptoms:**
- Automation runs but mode doesn't change, or errors in log

**Checks:**
1. Check HA logs for errors: `Settings → System → Logs`
2. Verify Huawei Solar integration is connected
3. Check if inverter is responsive (other entities updating?)
4. Look for Modbus errors

**Solutions:**
- Restart Huawei Solar integration
- Check Modbus connection (network, dongle, etc.)
- Verify inverter firmware supports mode changes via Modbus
- Increase delays between action steps (currently 2s)

### Problem: Charging Doesn't Start

**Symptoms:**
- Mode switches, `charge_from_grid` enables, but no charging occurs

**Checks:**
1. Is battery already at 100% SOC?
2. Check `sensor.battery_charge_discharge_power` value
3. Verify `number.battery_grid_charge_maximum_power` actually set to 20W
4. Check inverter display/app - what does it show?
5. Is there a battery fault or alarm?

**Solutions:**
- Verify battery SOC has room to charge
- Check battery health and status sensors
- Verify grid connection is active
- Test with higher power (50W) to confirm charging capability
- Review inverter logs/display for error codes

### Problem: Charge Period Still Missed

**Symptoms:**
- Keep-alive works, but 03:30 charge still doesn't start

**Checks:**
1. Did mode switch back to TOU at 03:30?
2. Is TOU schedule still valid on inverter?
3. Did Node-RED update the schedule correctly?
4. Check `sensor.battery_time_of_use_periods` at 03:30

**Solutions:**
- Verify TOU schedule is still programmed on inverter
- Check Node-RED flow for schedule writing errors
- Consider extending keep-alive through start of charge period
- May need to keep battery in self-consumption longer (delay TOU return)

### Problem: Excessive Energy Consumption

**Symptoms:**
- Keep-alive consuming >0.1 kWh per night

**Checks:**
1. Verify power is actually set to 20W (not 200W or higher)
2. Check `input_number.battery_keepalive_power` value
3. Review when keep-alive activates/deactivates (duration)
4. Is keep-alive running during the day accidentally?

**Solutions:**
- Reduce `battery_keepalive_power` to 10W if effective
- Verify sun condition is working correctly
- Check for automation logic errors (activating too early/late)
- Review automation traces for unexpected triggers

### Problem: Inverter Still Enters Standby

**Symptoms:**
- Despite keep-alive, `sensor.battery_status` shows "standby"

**Checks:**
1. Is battery actually charging OR discharging? (check power sensor)
2. What is battery SOC? (100% needs discharge, <100% needs charge)
3. Is discharge power limit also set to 20W?
4. What does inverter display show?
5. Is Modbus connection lost (entities unavailable)?
6. Is 20W sufficient to prevent standby?

**Solutions:**
- Verify BOTH `battery_grid_charge_maximum_power` AND `battery_maximum_discharging_power` are set to 20W
- If battery at 100%, ensure it can discharge to house (check house load >0W)
- Increase power to 50W or 100W if 20W insufficient
- Check if M1 model has specific standby prevention settings
- Contact Huawei support for inverter-specific behavior

### Problem: Battery at 100% But Not Discharging

**Symptoms:**
- Battery SOC = 100%
- Keep-alive active but no discharge activity
- Inverter still goes to standby

**Checks:**
1. Is `number.battery_maximum_discharging_power` set to 20W?
2. Is house consuming any power? (check `sensor.power_meter_active_power`)
3. Is battery working mode set to "Maximise Self Consumption"?
4. Check battery status sensor

**Solutions:**
- Verify discharge power limit is actually applied
- Ensure house has some baseload (should always be >20W)
- Check if battery is allowed to discharge in current mode
- Verify no battery faults or alarms preventing discharge
- As last resort, increase discharge power to 50W

### Emergency Rollback

**If system is unstable or causing issues:**

1. **Immediate Disable:**
   ```
   Settings → Automations → Battery Keep-Alive Manager → Disable
   ```

2. **Manual Reset:**
   ```
   - Set select.battery_working_mode → "TOU"
   - Leave switch.battery_charge_from_grid → ON (keep it ON)
   - Set number.battery_grid_charge_maximum_power → 5000
   - Set number.battery_maximum_discharging_power → 5000
   ```

3. **Configuration Rollback:**
   ```bash
   # Restore from backup
   cp backup_YYYYMMDD/configuration.yaml config/configuration.yaml
   cp backup_YYYYMMDD/automations.yaml config/automations.yaml
   make push
   ```

4. **Verify Normal Operation:**
   - Check battery returns to normal TOU operation
   - Monitor for one full day/night cycle
   - Review what went wrong before re-attempting

---

## References

### Documentation
- [Huawei LUNA2000 User Manual - TOU Mode](https://support.huawei.com/enterprise/en/doc/EDOC1100167258/ff9f37a6/setting-the-mode-for-the-grid-tied-ess)
- [Huawei SUN2000 Working Modes](https://support.huawei.com/enterprise/en/doc/EDOC1100163578/9ba1033/working-modes)
- [wlcrs/huawei_solar GitHub](https://github.com/wlcrs/huawei_solar)

### Community Resources
- [GitHub Discussion #125 - Charge and Discharge](https://github.com/wlcrs/huawei_solar/discussions/125)
- [GitHub Issue #494 - Battery Configuration Loss at Night](https://github.com/wlcrs/huawei_solar/issues/494)
- [Battery Optimization Package](https://github.com/heinoskov/huawei-solar-battery-optimizations)

### Related Home Assistant Documentation
- [Template Sensors](https://www.home-assistant.io/integrations/template/)
- [Time-based Automations](https://www.home-assistant.io/docs/automation/trigger/#time-pattern-trigger)
- [Sun Integration](https://www.home-assistant.io/integrations/sun/)

---

## Implementation Notes

### Actual Implementation Details

**Date Implemented:** _TBD_

**Entities Created:**
- `input_boolean.battery_keepalive_enabled`: _entity_id_
- `input_number.battery_keepalive_power`: _entity_id_
- `sensor.battery_keepalive_should_be_active`: _entity_id_
- Automation ID: _automation_id_

**Deviations from Plan:**
- _Document any changes made during implementation_

**Issues Encountered:**
- _Document any problems and their solutions_

**Test Results:**
- _Document actual test outcomes_

**Performance Metrics:**
- Average keep-alive energy per night: _X_ kWh
- Successful charge starts: _X_/_Y_ nights
- Standby prevention rate: _X_%

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2025-10-26 | 1.0 | Initial documentation created |
| 2025-10-26 | 1.1 | Updated to simplified switch management approach:<br>- Keep `switch.battery_charge_from_grid` ON permanently<br>- Control charging via power setting and mode only<br>- Removed switch toggling from all automation steps<br>- Added "Simplified Switch Management" explanation section |
| 2025-10-26 | 1.2 | **Added dual power limiting approach:**<br>- Limit BOTH charge and discharge power to 20W during keep-alive<br>- Handles 100% SOC edge case (battery discharges to house at 20W)<br>- Prevents wasteful battery cycling (saves ~480Wh per night)<br>- Prevents missing future discharge periods during expensive tariffs<br>- Updated all automation steps, tests, and troubleshooting<br>- Added "Handling the 100% SOC Edge Case" section<br>- Updated energy cost analysis with battery longevity benefits |

