## Camera Configuration Documentation
### Purpose of Alternative Camera Configuration

The stock camera implementation on the Flashforge AD5M (Pro) consumes significant system resources, particularly RAM. Given the printer's limited 128MiB of RAM, this can lead to performance degradation during operation. To address this, the mod provides an optimized camera implementation with reduced RAM usage, achieved through specific patches to `mjpg_streamer`.

While the stock camera remains available, the mod's camera is optimized for minimal resource consumption, making it the preferred choice for stable printing.  

> [!NOTE]
> If you choose to use the alternative display implementation (e.g., Feather Screen), the stock camera will not be available, as the stock firmware is completely disabled in this mode. In such cases, the mod's camera is the only option.

If you still want to use stock camera functionality, read the next section.

### Using the Stock Camera

If you prefer to use the stock camera functionality, you can skip Steps 1–3 and start directly with Step 4. Configure the camera settings in Fluidd or Mainsail as described, and ensure the stock camera is enabled in the printer's on-screen settings. However, be aware that the stock camera consumes significantly more resources, which may impact overall printer performance and could lead to print failures, such as unexpected print stoppages. You have been warned. Proceed at your own risk.


### Configuring the Mod's Camera

#### Step 1: Modify Camera Configuration
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

Open `http://printer_ip:8080/control.htm` to see the live MJPEG stream and the
current image controls. The image updates continuously over one stream
connection and reconnects if camera recovery closes that connection.

- Every edit is applied to the running camera automatically after a short
  delay, without writing `camera.conf`. Temporary values remain active across
  an in-process camera recovery, but are replaced by the file on
  `CAMERA_RELOAD` or service restart. While an arrow key is held, intermediate
  values are applied at least once per second; the final value is applied
  shortly after the key is released.
- The panel reads supported ranges, current values, and menu entries from the
  active V4L2 driver. Camera menus and small discrete integer ranges are shown
  as selectors, so invalid intermediate values cannot be entered. A numeric
  selector whose advertised minimum is above zero also includes `0 (special)`
  for camera drivers that accept zero as an undocumented off value. Hold
  `Shift` while pressing an arrow key to move a numeric control by ten steps.
- **Save** atomically updates the image-control entries in `camera.conf` and
  preserves unrelated settings and comments. Saving is available because
  `S98camera` passes the configuration path through `--controls-file`.
- After editing `camera.conf` manually, run `CAMERA_RELOAD`. The streamer
  rereads the file without restarting the HTTP service.

The panel, controls API, and MJPEG stream share the streamer's existing HTTP
listener. Opening the panel does not start another HTTP server or allocate a
second server-side frame buffer; its `<img>` connects to the existing MJPEG
route. A controls request uses one bounded HTTP client slot only while the
request is active.

On every service start and camera recovery, saved controls are applied after
the first completed frame so UVC initialization cannot immediately overwrite
them. Changes to resolution, FPS, video device, or memory-reduction mode still
require `CAMERA_RESTART`.

The control page has no separate authentication layer. Treat port 8080 as a
trusted-LAN interface and do not expose it directly to an untrusted network.

#### Step 2: Disable Stock Camera
To ensure the mod's camera is used, you need to disable the stock camera functionality. Here’s how:

1. Go to the printer's on-screen settings.
2. Disable both camera photo and camera video.

This step is crucial to avoid conflicts between the stock and mod camera implementations.

#### Step 3: Enable Mod's Camera
Once the stock camera is disabled, enable the mod's camera by running the following command in the console:

```
SET_MOD PARAM="camera" VALUE=1
```

This command activates the mod's camera implementation.

#### Step 4: Reload Fluidd
After completing the configuration, reload the Fluidd page. The camera should now be operational, and you should be able to view the stream and take snapshots.

#### Notes for Mainsail Users
If you’re using Mainsail, the configuration process is nearly identical to Fluidd. Simply follow the steps above, and you’ll be good to go. If you run into any issues, double-check the URLs and ensure the stock camera is disabled.
