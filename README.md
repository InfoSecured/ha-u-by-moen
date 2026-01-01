# U by Moen Home Assistant Integration

A custom Home Assistant integration for U by Moen smart shower systems (Alexa/Android version, not HomeKit).

## Features

- **Climate Control**: Control your shower temperature and power through Home Assistant's climate entity
- **Preset Activation**: Buttons to activate your configured shower presets (e.g., "Jason", "Lauren", "Fill The Tub")
- **Outlet Control**: Individual switches for each water outlet (shower head, hand shower, tub spout, body spray)
- **Status Monitoring**: Sensors for current temperature, target temperature, active preset, timer, and more
- **Real-time Updates**: Uses Pusher WebSocket for instant status updates (coming soon)

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=InfoSecured&repository=ha-u-by-moen)

1. Open HACS in Home Assistant
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL and select "Integration" as the category
6. Click "Install"
7. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/u_by_moen` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "U by Moen"
4. Enter your U by Moen account credentials (email and password)
5. Click Submit

Your devices will be automatically discovered and added to Home Assistant.

## Entities Created

For each U by Moen shower, the integration creates:

### Climate Entity
- **Master Bathroom** (Climate)
  - Control temperature and turn shower on/off
  - Shows current and target temperature

### Switches
- **Master Bathroom Power** - Main on/off control
- **Master Bathroom Shower Head** - Control individual outlet 1
- **Master Bathroom Hand Shower** - Control individual outlet 2
- **Master Bathroom Tub Spout** - Control individual outlet 3
- **Master Bathroom Body Spray** - Control individual outlet 4

### Buttons (Presets)
- **Master Bathroom Jason** - Activate Jason preset
- **Master Bathroom Lauren** - Activate Lauren preset
- **Master Bathroom Fill The Tub** - Activate Fill The Tub preset

### Sensors
- **Master Bathroom Mode** - Current mode (off/on/pause)
- **Master Bathroom Current Temperature** - Current water temperature
- **Master Bathroom Target Temperature** - Target temperature setting
- **Master Bathroom Active Preset** - Currently active preset name
- **Master Bathroom Time Remaining** - Timer countdown (if active)
- **Master Bathroom Firmware** - Firmware version

## Usage Examples

### Automations

Turn on shower at target temperature:
```yaml
automation:
  - alias: "Morning Shower Ready"
    trigger:
      - platform: time
        at: "06:30:00"
    action:
      - service: climate.set_temperature
        target:
          entity_id: climate.master_bathroom
        data:
          temperature: 102
          hvac_mode: heat
```

Activate a preset:
```yaml
automation:
  - alias: "Activate Jason's Shower"
    trigger:
      - platform: state
        entity_id: binary_sensor.jason_home
        to: "on"
    action:
      - service: button.press
        target:
          entity_id: button.master_bathroom_jason
```

Turn off shower after 15 minutes:
```yaml
automation:
  - alias: "Shower Timeout"
    trigger:
      - platform: state
        entity_id: climate.master_bathroom
        to: "heat"
        for:
          minutes: 15
    action:
      - service: climate.set_hvac_mode
        target:
          entity_id: climate.master_bathroom
        data:
          hvac_mode: "off"
```

### Lovelace Card Example

```yaml
type: thermostat
entity: climate.master_bathroom
```

Or a more detailed card:
```yaml
type: entities
title: Master Bathroom Shower
entities:
  - entity: climate.master_bathroom
  - entity: sensor.master_bathroom_current_temperature
  - entity: sensor.master_bathroom_active_preset
  - type: divider
  - entity: button.master_bathroom_jason
  - entity: button.master_bathroom_lauren
  - entity: button.master_bathroom_fill_the_tub
  - type: divider
  - entity: switch.master_bathroom_shower_head
  - entity: switch.master_bathroom_hand_shower
  - entity: switch.master_bathroom_tub_spout
```

## Known Limitations

- **Pusher WebSocket Events**: The control commands (turning on/off, changing temperature, activating presets) use Pusher client events. The exact event names are currently based on common patterns and may need to be refined through testing. If controls don't work immediately, we'll need to capture the actual WebSocket messages from the mobile app.

- **Real-time Updates**: Status updates currently rely on polling (every 30 seconds). Full Pusher WebSocket integration for instant updates is planned.

## Troubleshooting

### Integration not appearing
- Make sure you've restarted Home Assistant after installation
- Check the Home Assistant logs for any errors

### Authentication fails
- Verify your email and password are correct
- Make sure you're using the credentials for the Alexa/Android version of U by Moen (not HomeKit)

### Controls not working
- Check Home Assistant logs for errors
- The Pusher event names may need adjustment (see Known Limitations)
- Open an issue with debug logs

### Enable debug logging
Add to your `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.u_by_moen: debug
```

## API Information

This integration uses the Moen IoT API:
- **Base URL**: `https://www.moen-iot.com`
- **Authentication**: Token-based (obtained via email/password)
- **Real-time**: Pusher WebSocket (app_key: `dcc28ccb5296f18f8eae`, cluster: `us2`)

## Contributing

Contributions are welcome! Please open an issue or pull request.

Come see our other apps and integrations at [WeaveHub](https://weavehub.app).

### Development Setup

1. Clone this repository
2. Install dependencies: `pip install -r requirements.txt`
3. Make your changes
4. Test with your Home Assistant instance

### Capturing WebSocket Events

If you want to help improve the Pusher control commands:

1. Use Charles Proxy or similar tool
2. Enable SSL proxying for `*.pusher.com`
3. Monitor traffic while using the Moen mobile app
4. Capture the `client-*` events sent when controlling the shower
5. Share findings in an issue

## License

MIT License - see LICENSE file for details

## Credits

Created by Jason Lazerus

## Disclaimer

This is an unofficial integration and is not affiliated with or endorsed by Moen or Fortune Brands.
