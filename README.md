## Library to handle Wolf SmartSet communication.

### Features:
- Built-in authentication
- Session creation
- Fetch all devices you have
- Fetch all parameters description
- Fetch value for specific parameter

### Parameter descriptions
Parameters verified only for ISM7 with gas/solar-system.
Other like heatpumps should work too.
Keep in mind that core implementation of fetching parameters is removing duplications by value_id and name.
