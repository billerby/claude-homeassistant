# python_scripts/hourly_water_usage.py

# Get the current water meter reading
water_meter_state = hass.states.get('sensor.water_meter_total_m3')

# If we can't get a valid water meter reading, we can't calculate usage
if water_meter_state is None or water_meter_state.state in ['unknown', 'unavailable']:
    water_meter_value = 0.0
else:
    water_meter_value = float(water_meter_state.state)

# Get the last recorded water meter reading
last_water_usage_state = hass.states.get('input_number.last_water_usage')

# If there is no last recorded water meter reading, use the current reading
# This situation might happen the first time the script is run
if last_water_usage_state is None or last_water_usage_state.state in ['unknown', 'unavailable']:
    last_water_usage = water_meter_value
else:
    last_water_usage = float(last_water_usage_state.state)

# Calculate the hourly usage
hourly_usage = water_meter_value - last_water_usage

# Store the current water meter reading for the next calculation
hass.services.call('input_number', 'set_value', {'entity_id': 'input_number.last_water_usage', 'value': water_meter_value}, False)


# Update the hourly usage sensor
hass.states.set('sensor.hourly_water_usage', hourly_usage, {
    'unit_of_measurement': 'm3',
    'friendly_name': 'Hourly Water Usage'
})
