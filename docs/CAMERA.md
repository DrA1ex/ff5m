# Camera

Forge-X includes its own camera service, **forge-x-streamer**. It serves an MJPEG stream and snapshots from a USB camera and is built for the printer's 128 MiB of RAM:

- **Low memory use.** Buffers and the number of viewers are strictly limited, so the camera leaves as much RAM as possible for Klipper, Moonraker, and the screen.
- **Hardware acceleration.** Cameras that output raw video, and the screen stream, are compressed to JPEG by the printer's hardware encoder instead of the CPU. Cameras that already output MJPEG are passed through untouched.
- **Self-healing.** If the camera disconnects or stalls, the service reconnects on its own. There is no need to restart Forge-X.
- **Live image settings.** Brightness, contrast, exposure, and the rest can be tuned in the browser while watching the stream.

## Forge-X camera or stock camera

- **Feather, Guppy, or Headless:** only the Forge-X camera is available.
- **Stock screen:** you can use either one. The stock camera uses noticeably more memory, which can cause unexpected stops on heavy prints, so the Forge-X camera is recommended. To switch, follow the quick start below.

To go back to the stock camera, run `SET_MOD PARAM="camera" VALUE=0`, then enable the camera in the printer's on-screen settings. Forge-X keeps **4gx_camera** hidden while `camera` is `0`, so to watch the stock camera in Fluidd or Mainsail, add your own camera entry with the stream address `/webcam/?action=stream`.

## Quick start

1. **Feather, Guppy, or Headless:** if a camera is connected at boot and you have never changed the `camera` parameter, Forge-X turns the camera on by itself. Otherwise, run:

   ```gcode
   SET_MOD PARAM="camera" VALUE=1
   ```

2. **Stock screen:** turn off both camera photo and camera video in the printer's on-screen settings, then run:

   ```gcode
   SET_MOD PARAM="camera" VALUE=1
   ```

3. Reload Fluidd or Mainsail. The camera appears as **4gx_camera**.

Useful addresses (port `8080`):

- Stream: `http://<printer_ip>:8080/?action=stream`
- Snapshot: `http://<printer_ip>:8080/?action=snapshot`
- Image settings: `http://<printer_ip>:8080/control.htm`

> [!WARNING]
> Port `8080` and the image settings page have no password. Use them only on a trusted network.

Forge-X adds two entries to the web interface: **4gx_camera** (the camera) and **4gx_screen** (the printer screen, see [below](#viewing-the-printer-screen)). It shows and hides them itself when you turn a stream on or off, so if you flip their on/off switch in the web interface, it is reset at the next change or reboot. Everything else, such as rotation, flip, or frame rate, is yours to change in Fluidd or Mainsail, and Forge-X never overwrites it. A deleted entry is not recreated.

If you are upgrading from an older version, the next boot copies your `[webcam cam]` settings from `user.moonraker.conf`, such as rotation or flip, to **4gx_camera** and comments the section out. Until the following reboot, you may see both cameras.

## Camera settings

Camera settings live in `camera.conf`: in Fluidd, open _Configuration -> mod_data -> camera.conf_.

```cfg
WIDTH=640
HEIGHT=480
FPS=15
VIDEO=auto
REDUCE_MEMORY=0
```

- `WIDTH`, `HEIGHT`, `FPS`: resolution and frame rate. Higher values use more memory, so check the result with the `MEM` macro under your usual print workload.
- `VIDEO`: `auto` finds the camera by itself. Set `video0`, `video1`, and so on only if you have several video devices.
- `REDUCE_MEMORY`: some cameras report a Full HD-sized buffer even at 640×480 and use memory as if they streamed 1080p. With `1`, Forge-X limits that buffer. Set `0` if the camera does not work properly with it.

Changing any of these needs `CAMERA_RESTART`. After editing image settings (`E_*` lines) by hand, run `CAMERA_RELOAD` instead. It rereads the file without interrupting the stream.

### Image settings page

Open `http://<printer_ip>:8080/control.htm` to see the stream and the settings your camera supports.

- Changes apply to the running camera right away.
- Press **Save** to keep them. Unsaved changes are lost on `CAMERA_RELOAD` or a restart.
- Hold `Shift` with an arrow key to change a number by ten steps.

## If the camera does not work

- **Stream is frozen or looks wrong:** run `CAMERA_RESTART`.
- **The stream shows NO SIGNAL:** the camera is not delivering any video. Check the cable and the connection, then unplug the camera and plug it back in. The stream recovers by itself once the camera responds. If that does not help, power the printer off completely, unplug it from the outlet for a few seconds, and start it again.
- **The camera does not start or reports a format error** (for example `VIDIOC_G_FMT: failed`): set `REDUCE_MEMORY=0`, then run `CAMERA_RESTART`. See also the [camera questions in the FAQ](FAQ.md#how-do-i-adjust-the-camera-settings).
- **Anything else:** the camera and screen stream write their messages to `/data/logFiles/streamer.log`.

## Timelapse

Timelapse is off by default. Enable the Forge-X camera, then turn on `timelapse` in the mod settings while the printer is idle. You can also run:

```gcode
SET_MOD PARAM=timelapse VALUE=1
```

- **Interval:** choose **layers** (default, one photo per layer), **time**, or **progress** (default 0.5%, decimals allowed). Layer mode needs [OrcaSlicer setup](SLICING.md#timelapse); the other modes do not.
- **First photo:** taken at the end of `START_PRINT`, in any mode.
- **Park for photos:** off by default. When enabled, the head moves aside for each photo.
- **Final photo:** on by default. Adds a photo of the finished print with the head parked.
- **Skip one print:** add `TIMELAPSE=0` to its `START_PRINT` line.

Finished videos are in Mainsail's Timelapse view. On Feather, Guppy, and Headless, if the previous video is still being converted when you start a new print, you can wait for it, cancel the new print, or cancel the timelapse and print anyway.

## Viewing the printer screen

The screen appears as **4gx_screen** in Fluidd and Mainsail. You can also operate the printer from the browser with the mouse or touch.

> [!WARNING]
> The screen page has no password. Anyone who can open it can press buttons on the printer's screen. Use it only on a trusted network, or set `screen_stream` to `OFF`.

`screen_stream` is `AUTO` by default: the stream runs on Stock, Feather, and Guppy, and stays off on Headless. To change it:

```gcode
SET_MOD PARAM="screen_stream" VALUE=AUTO
SET_MOD PARAM="screen_stream" VALUE=ON
SET_MOD PARAM="screen_stream" VALUE=OFF
```

To change the frame rate (default 30) or JPEG quality (default 90), run:

```gcode
SET_MOD PARAM="screen_stream_fps" VALUE=30
SET_MOD PARAM="screen_stream_quality" VALUE=90
```

The stream restarts after each change. Higher quality makes larger frames; the real frame rate depends on what is on screen and how busy the printer is.

The interactive page is at `http://<printer_ip>/screen/` and a snapshot at `http://<printer_ip>/screen/?action=snapshot`. For a custom screen on Headless, set `screen_stream` to `ON`. See [Configuration](CONFIGURATION.md) for `touch_interface` and the other screen stream parameters.
