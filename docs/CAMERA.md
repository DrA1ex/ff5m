# Camera

Forge-X includes its own camera service, **forge-x-streamer**. It serves an MJPEG stream and snapshots from a USB camera while using as little of the printer's 128 MiB of RAM as possible. The stock camera service also works, but it uses much more memory.

## Quick start

1. **Feather, Guppy, or Headless mode:** nothing to do. On boot, Forge-X enables its camera if a working video device is found and the `camera` parameter has never been set.
2. **Stock screen mode:** turn off both camera photo and camera video in the printer's on-screen settings, then run:

   ```gcode
   SET_MOD PARAM="camera" VALUE=1
   ```

3. Reload Fluidd or Mainsail. The camera should appear in the web interface.

Useful addresses (port `8080`):

- Stream: `http://<printer_ip>:8080/?action=stream`
- Snapshot: `http://<printer_ip>:8080/?action=snapshot`
- Image settings: `http://<printer_ip>:8080/control.htm`

If the stream stops or looks wrong, run `CAMERA_RESTART`. After editing `camera.conf` by hand, run `CAMERA_RELOAD`.

> [!WARNING]
> Port `8080` and the control page have no password. Use them only on a trusted network.

**Details:**
[Configure the camera](#configuring-the-mods-camera) ·
[Use the stock camera](#using-the-stock-camera) ·
[Why a dedicated camera service](#why-a-separate-camera-service) ·
[How it behaves](#how-it-behaves) ·
[Image settings](#image-settings-page) ·
[Camera questions in the FAQ](FAQ.md#how-do-i-adjust-the-camera-settings)

## Configuring the Mod's Camera

Edit `camera.conf` to change the resolution, frame rate, or image settings. Steps 2 and 3 are needed only if you use the Stock screen mode.

### Step 1: Modify Camera Configuration
The camera settings are defined in the `camera.conf` file, located in Fluidd under _Configuration -> mod_data -> camera.conf_. Below is the default configuration:

```cfg
# Resolution width
WIDTH=640

# Resolution height
HEIGHT=480

# Frame per second
FPS=15

# Video device: 'auto' or video<N> (like video0)
VIDEO=auto

# Reduce camera memory usage
# This may be handy if your camera consumes too much memory.
# For example, even for 640x480 resolution, it may uses memory as it 1080p stream.
# Disable it if you experiencing issues with your camera
REDUCE_MEMORY=0

# Image post-processing settings.
# Enable this to use image post-processing
POST_PROCESSING=1

# Preview and tune these settings at:
# http://printer_ip:8080/control.htm
# Changes apply to the running camera automatically.
# "Save" updates this file and keeps the values after a restart.
# You can also edit the values below manually and run CAMERA_RELOAD.

# E_BRIGHTNESS=0
# E_CONTRAST=35
# E_GAIN=1
# E_GAMMA=100
# E_HUE=300
# E_SATURATION=42  
# E_SHARPNESS=7
# E_POWER_LINE_FREQUENCY=0
# E_WHITE_BALANCE_TEMPERATURE=4500
# E_BACKLIGHT_COMPENSATION=0
# E_EXPOSURE_AUTO="3"
# E_EXPOSURE_ABSOLUTE=80
```

You can adjust these parameters to suit your camera. Increasing resolution or
FPS can increase memory use, so check the result with the `MEM` macro under the
same workload used while printing.

#### Image settings page

Open `http://<printer_ip>:8080/control.htm` to see the stream and the camera's image settings.

- Changes apply to the running camera after a short delay. They are not written to `camera.conf` until you press **Save**, and are replaced by the file on `CAMERA_RELOAD` or a service restart.
- The page shows only the settings your camera reports, with its own ranges and options. Hold `Shift` with an arrow key to change a number by ten steps. Some settings include `0 (special)` for cameras that use zero as "off".
- **Save** writes the image settings to `camera.conf` and keeps the rest of the file unchanged.
- After editing `camera.conf` by hand, run `CAMERA_RELOAD`. The service rereads the file without restarting.
- Saved settings are applied again every time the camera starts or reconnects.
- Changing resolution, FPS, video device, or `REDUCE_MEMORY` requires `CAMERA_RESTART`.

The page uses the same HTTP server and stream as the camera, so opening it does not add a second server or frame buffer.

### Step 2: Disable Stock Camera
To ensure the mod's camera is used, you need to disable the stock camera functionality. Here’s how:

1. Go to the printer's on-screen settings.
2. Disable both camera photo and camera video.

This step prevents conflicts between the stock and mod camera implementations.

### Step 3: Enable Mod's Camera
Once the stock camera is disabled, enable the mod's camera by running the following command in the console:

```
SET_MOD PARAM="camera" VALUE=1
```

This command activates the mod's camera implementation.

### Step 4: Reload Fluidd
After completing the configuration, reload the Fluidd page. The camera should now be operational, and you should be able to view the stream and take snapshots.

### Notes for Mainsail Users
If you’re using Mainsail, the configuration process is nearly identical to Fluidd. Follow the same steps. If something does not work, check the URLs and make sure the stock camera is disabled.

### Timelapse

Timelapse is off by default. Enable the mod camera, then turn on `timelapse`
in the mod settings while the printer is idle. You can also run:

```gcode
SET_MOD PARAM=timelapse VALUE=1
```

Choose **layers**, **time**, or **progress** under Timelapse interval. The
default is one photo per layer. Layer mode needs
[OrcaSlicer setup](SLICING.md#timelapse); time and progress modes do not. The
default progress interval is 0.5%, and you can enter decimals.

A first photo is taken at the end of `START_PRINT`, in any mode. The nozzle
cleaning line may be visible in it. Further photos follow the chosen interval.

**Park for photos** is off by default. When enabled, the head moves aside for
photos during printing. **Final photo** is on by default and adds a photo of
the finished print with the head parked. Turn it off to skip that photo.

To skip photos for one print, add `TIMELAPSE=0` to its `START_PRINT` line. This
does not change the setting for later prints.

Find finished videos in Mainsail's Timelapse view. Video creation can take
time; starting another print stops an unfinished video.

## Using the Stock Camera

If you prefer to use the stock camera functionality, you can skip Steps 1–3 and start directly with Step 4. Configure the camera settings in Fluidd or Mainsail as described, and ensure the stock camera is enabled in the printer's on-screen settings. The stock camera uses noticeably more memory, which can lead to print failures such as unexpected stops on heavy prints.

## About forge-x-streamer

This section explains how the camera service works. You do not need it to set up the camera.

### Why a separate camera service

The printer has 128 MiB of RAM, and every megabyte used by the camera is taken from Klipper, Moonraker, the screen, and your own additions. forge-x-streamer is written for this limit: capture, HTTP streaming, image controls, and reconnect logic are in one small program with fixed limits on buffers and clients. It uses less memory than general-purpose streamers such as `ustreamer` that were used on the AD5M before.

Actual memory use depends on the camera, format, resolution, and number of viewers. Use the `MEM` macro to check your own setup. The source is at [DrA1ex/forge-x-streamer](https://github.com/DrA1ex/forge-x-streamer).

### How it behaves

- `VIDEO=auto` scans `/dev/video0..63` and picks a device that can stream.
- If the camera times out, disconnects, or returns errors, the service closes it and keeps trying to reopen or find it again. Forge-X does not need to be restarted.
- While the camera is offline, open streams and snapshots get a generated **NO SIGNAL** image (streams at 2 FPS). A single bad frame is dropped without switching to NO SIGNAL.
- A camera that starts in an unsupported format is asked to switch to a supported one when it is reopened.
- A slow browser cannot hold the camera's capture buffer.
- Extra clients and oversized or broken frames are rejected instead of using more memory.
- A failed `CAMERA_RELOAD` keeps the last valid settings.

### Technical details

- **Endpoints:** `/?action=stream` (MJPEG; `POST /stream` also works), `/?action=snapshot` (JPEG), `/healthz` (returns `503` while the camera is offline), `/control.htm` (control page), and `/controls` (read, apply, and save image controls). The stream format matches the previous MJPEG streamer, so existing clients keep working.
- **Defaults:** 640×480, 15 FPS, one V4L2 capture buffer, and three video clients. One extra worker is kept for snapshots and controls. Requests over the limit get HTTP `429`.
- **Memory limits:** each worker thread has a 128 KiB stack. Frame buffers grow only to the largest frame actually seen. The control page is a 6 KiB static page.
- **`REDUCE_MEMORY`:** maps to `--frame-cap`, which limits the capture buffer for cameras that report a 1080p-sized buffer at every resolution.
- **Raw formats:** when built with libjpeg (`WITH_RAW_INPUT`), YUYV, UYVY, RGB24, and RGB565 input is encoded to JPEG. The MJPEG-only build does not link libjpeg.
- **Standalone use:** forge-x-streamer builds on other Linux systems with V4L2 and is licensed under GPL-2.0-or-later. Its CI builds both variants and runs the tests. See its [repository](https://github.com/DrA1ex/forge-x-streamer) for build and runtime options.
