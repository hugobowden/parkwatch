# ParkWatch

Connect a camera that overlooks a car park and ParkWatch shows which spaces are free,
live, and learns when the lot is busy so it can tell you the best times to find a spot.

## Run it

```bash
./run.sh
```

Then open http://localhost:8000. The first run sets up a Python environment (needs
[uv](https://docs.astral.sh/uv/) or Python 3.10+) and creates a **Demo lot**, a simulated
camera with three weeks of made-up history, so you can explore straight away.

Use `PORT=8080 ./run.sh` to change the port, or `HOST=0.0.0.0 ./run.sh` to open it to
other devices on your network. There is no login, so only do that on a network you trust.

## Run it on a Raspberry Pi (always on)

**You need:** a Raspberry Pi 4 (2 GB or more) or Pi 5, its official power supply, and a
32 GB+ microSD card. Use a wired network connection if you can; video streams are more
reliable over Ethernet than Wi-Fi.

1. **Set up the SD card.** On your computer, install [Raspberry Pi Imager](https://www.raspberrypi.com/software/),
   choose your Pi model and **Raspberry Pi OS Lite (64-bit)**. When it offers to customise
   settings, set:
   - hostname: `parkwatch`
   - a username and password
   - your Wi-Fi (if not using Ethernet)
   - under Services: **Enable SSH** (password authentication)

   Write the card, put it in the Pi and power it on. Give it a couple of minutes.

2. **Copy ParkWatch to the Pi.** From this folder on your computer (replace `pi` with the
   username you chose):

   ```bash
   rsync -av --exclude '.venv*' --exclude 'data*' --exclude __pycache__ ./ pi@parkwatch.local:parkwatch/
   ```

3. **Install it.** Log in and run the installer:

   ```bash
   ssh pi@parkwatch.local
   cd parkwatch && ./pi/install.sh
   ```

   It installs everything, then prints the address to open, usually
   **http://parkwatch.local:8000**, from any phone or computer on your network. ParkWatch
   starts automatically whenever the Pi boots.

**Updating:** repeat steps 2 and 3. Your cameras, spaces and history are kept.
**Logs:** `journalctl -u parkwatch -f` on the Pi.

The built-in detector runs comfortably on a Pi. The optional YOLO detector (below) works on a
Pi 5 but is slow: set the camera to check every 10 seconds or more.

## Connect your camera

1. **Cameras → Add camera.** Enter a source and press **Test connection**:
   - `rtsp://user:pass@192.168.1.20:554/stream1`: most IP/CCTV cameras. The path varies by
     brand; search "<your camera model> RTSP URL".
   - `http://…/video.mjpg`: MJPEG streams.
   - `http://…/snapshot.jpg`: a still image URL, fetched on each check.
   - `0`: a webcam plugged into this computer.
   - `/path/to/video.mp4`: a recorded video, played on a loop (handy for testing).
2. **Draw the spaces.** Click **Draw space**, then the 4 corners of a bay. Drag the white
   dots to adjust. **Duplicate** copies the selected bay alongside it for quick rows.
   **Save spaces** when done.
3. Watch **Live**. If parked cars are missed, raise **Sensitivity** in the camera settings;
   if empty bays show as taken, lower it.

**Insights** fills in as ParkWatch runs. It records lot occupancy every minute, and patterns
become useful after about a week.

## Tips for accuracy

- A high camera looking down on the lot works best. Low angles where cars hide each other
  are hard for any system.
- Draw the space around the ground area of the bay, not the air above it.
- The built-in **texture** detector needs no downloads and works well on steady overhead
  views. For real-world footage (angles, night, weather) install the optional vehicle AI:

  ```bash
  .venv/bin/pip install ultralytics
  ```

  Then pick **Vehicle AI (YOLO)** (or leave **Auto**) in the camera settings. Note that
  `ultralytics` is AGPL-licensed, which matters if you ever sell or distribute this.

## How it works

| Part | File |
| --- | --- |
| Web server + API | `app/main.py` |
| Camera input (RTSP/HTTP/webcam/file, auto-reconnect) | `app/camera.py` |
| Occupancy detection (texture / YOLO) | `app/detector.py` |
| Per-camera worker, smoothing, recording | `app/monitor.py` |
| Best times, heatmap, per-space stats | `app/analytics.py` |
| Simulated demo lot | `app/demo.py` |
| Dashboard | `static/` |

A space only switches between free and taken after two consistent readings, so passing
shadows and people walking through don't cause flicker. Data is stored in `data/parkwatch.db`
(SQLite); delete that file to start fresh.
