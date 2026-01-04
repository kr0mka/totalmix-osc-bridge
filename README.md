# TotalMix OSC Bridge

HTTP bridge server that enables web applications to communicate with RME TotalMix FX via OSC protocol. Designed for use with https://kr0mka.squig.link/ EQ integration.

## Features

- Send EQ settings from web apps to TotalMix Room EQ (9 bands) and Parametric EQ (3 bands)
- Read current EQ settings from TotalMix channels
- Get output channel names
- Supports Bell (Peak) and Shelf filter types
- Simple HTTP REST API with CORS support

## Download

Download the latest `totalmix-bridge.exe` from the [Releases](https://github.com/kr0mka/totalmix-osc-bridge/releases) page.

## TotalMix OSC Setup

1. Open TotalMix FX
2. Go to **Options > Settings > OSC** tab
3. Configure an available **Remote Controller** (e.g., RC3 if RC1/RC2 are in use):
   - Check **"In Use"**
   - Port incoming: **7003**
   - Port outgoing: **9003**
   - IP (Remote Controller Address): **127.0.0.1**
4. Click OK

> **Note:** If using different ports, run the bridge with arguments:
> ```
> totalmix-bridge.exe --osc-send 7001 --osc-listen 9001
> ```

## Usage

1. Run `totalmix-bridge.exe`
2. The bridge runs silently in the system tray (no console window)
3. Right-click the tray icon for options:
   - **Open Log File** - View current session log
   - **Start with Windows** - Toggle auto-start
   - **Quit** - Exit the application
4. Connect from your web application (e.g., Squig's TotalMix Direct feature)

Logs are stored at: `%APPDATA%\TotalMixOSCBridge\bridge.log`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Check bridge status |
| GET | `/api/channels` | List output channel names |
| GET | `/api/channel/{n}/eq` | Read EQ settings from channel n |
| POST | `/api/channel/{n}/eq` | Write EQ settings to channel n |

### Example: Read EQ

```bash
curl http://127.0.0.1:8765/api/channel/1/eq
```

Response:
```json
{
  "filters": [
    {"type": "PK", "freq": 160, "gain": -3.0, "q": 1.0},
    {"type": "LSQ", "freq": 60, "gain": 5.0, "q": 0.7},
    {"type": "HSQ", "freq": 6500, "gain": -5.0, "q": 0.7}
  ]
}
```

### Example: Write EQ

```bash
curl -X POST http://127.0.0.1:8765/api/channel/1/eq \
  -H "Content-Type: application/json" \
  -d '{"filters": [{"type": "PK", "freq": 1000, "gain": -3.0, "q": 2.0}]}'
```

## Filter Types

| Type | Description |
|------|-------------|
| PK | Peak/Bell |
| LSQ | Low Shelf |
| HSQ | High Shelf |

> **Note:** TotalMix only supports Bell and Shelf types. HP/LP filters are not available in TotalMix EQ.

## Band Allocation

When writing EQ:
- **Room EQ (Page 4)**: First 9 filters
- **Parametric EQ (Page 2)**: Filters 10-12 (overflow)
- **Maximum**: 12 filters total

## Command Line Options

```
totalmix-bridge.exe [options]

Options:
  --http-port PORT    HTTP server port (default: 8765)
  --osc-send PORT     TotalMix OSC incoming port (default: 7003)
  --osc-listen PORT   OSC listen port (default: 9003)
  --debug             Enable debug output for OSC messages
```

## Building from Source

### Requirements

- Python 3.8+
- python-osc
- pystray (for system tray)
- pillow (for tray icon)
- PyInstaller (for building executable)

### Install Dependencies

```bash
pip install -r requirements.txt
pip install pyinstaller
```

### Build Executable

```bash
pyinstaller totalmix-bridge.spec --noconfirm
```

Or build manually:
```bash
pyinstaller --onefile --name totalmix-bridge --noconsole bridge.py
```

Or use the build script:
```bash
build.bat
```

Output: `dist/totalmix-bridge.exe`

## Troubleshooting

### "Cannot bind to port" error

Another application is using the listen port. Check:
1. Another instance of totalmix-bridge is running
2. Another OSC application is using the port
3. Try a different port with `--osc-listen`

### Channel names not showing

Ensure TotalMix OSC settings have the **IP address** field set to `127.0.0.1`.

### EQ not being applied

1. Verify TotalMix OSC is enabled and ports match
2. Run with `--debug` to see OSC traffic
3. All values must be floats (the bridge handles this automatically)

## License

MIT License - see [LICENSE](LICENSE) file.
