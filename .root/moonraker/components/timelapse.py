# Moonraker Timelapse component, adapted from Mainsail Crew's plugin:
# https://github.com/mainsail-crew/moonraker-timelapse
# Forge-X changes:
# - Limit frame count and disk use; commit captured frames atomically.
# - Keep frames after file selection until a new print actually starts; restore
#   them after restart and cancel rendering or export when printing begins.
#   An active render is released when print preparation starts.
# - Render and export only while idle; clean temporary files and keep existing
#   outputs when names collide or an operation fails or is cancelled.
# - Export ZIP without blocking Moonraker; run stock FFmpeg with one thread,
#   low priority, and quiet output; create transformed previews separately.
# - Keep the configured snapshot URL fixed and reject invalid target lengths.
# - Hide internal housekeeping commands from console history; the Klipper macro
#   reports the active capture settings instead.
# - Report missing slicer layer updates after layer-based prints.
# - Capture an optional final frame after printing ends and before rendering.
# - Keep normal frame capture one-way; acknowledge parked captures only after
#   the snapshot attempt completes.
#
# Copyright (C) 2021 Christoph Frei <fryakatkop@gmail.com>
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import logging
import os
import glob
import shutil
import asyncio
import shlex
import threading
import uuid
from datetime import datetime
from tornado.ioloop import IOLoop
from zipfile import ZipFile
from ..common import WebRequest
# Annotation imports
from typing import (
    TYPE_CHECKING,
    Dict,
    Any,
    Optional
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from .webcam import WebcamManager, WebCam
    from websockets import WebRequest
    from . import shell_command
    from . import klippy_apis
    from . import database

    APIComp = klippy_apis.KlippyAPI
    SCMDComp = shell_command.ShellCommandFactory
    DBComp = database.MoonrakerDatabase


class Timelapse:
    MAX_FRAMES = 1000
    MIN_FREE_BYTES = 128 * 1024 * 1024

    def __init__(self, confighelper: ConfigHelper) -> None:

        # setup vars
        self.renderisrunning = False
        self.saveisrunning = False
        self.save_cancel = None
        self.takingframe = False
        self.finishing_print = False
        self.active_print_filename = ""
        self.finishing_filename = ""
        self.finishing_frame_generation = None
        self.frame_generation = 0
        self.framecount = 0
        self.lastframefile = ""
        self.lastcmdreponse = ""
        self.hyperlapserunning = False
        self.printing = False
        self.pending_file_selected = False
        self.render_command = None
        self.noWebcamDb = False
        self.confighelper = confighelper
        self.server = confighelper.get_server()
        self.klippy_apis: APIComp = self.server.lookup_component('klippy_apis')
        self.database: DBComp = self.server.lookup_component("database")
        # setup static (nonDB) settings
        out_dir_cfg = confighelper.get(
            "output_path", "~/timelapse/")
        temp_dir_cfg = confighelper.get(
            "frame_path", "/tmp/timelapse/")
        self.ffmpeg_binary_path = confighelper.get(
            "ffmpeg_binary_path", "/usr/bin/ffmpeg")
        # Setup default config
        self.config: Dict[str, Any] = {
            'enabled': True,
            'mode': "layermacro",
            'camera': "",
            'snapshoturl': "http://localhost:8080/?action=snapshot",
            'stream_delay_compensation': 0.05,
            'gcode_verbose': False,
            'parkhead': False,
            'parkpos': "back_left",
            'park_custom_pos_x': 10.0,
            'park_custom_pos_y': 10.0,
            'park_custom_pos_dz': 0.0,
            'park_travel_speed': 100,
            'park_retract_speed': 15,
            'park_extrude_speed': 15,
            'park_retract_distance': 1.0,
            'park_extrude_distance': 1.0,
            'park_time': 0.1,
            'fw_retract': False,
            'hyperlapse_cycle': 30,
            'autorender': True,
            'constant_rate_factor': 23,
            'output_framerate': 30,
            'pixelformat': "yuv420p",
            'time_format_code': "%Y%m%d_%H%M",
            'extraoutputparams': "",
            'variable_fps': False,
            'targetlength': 10,
            'variable_fps_min': 5,
            'variable_fps_max': 60,
            'rotation': 0,
            'flip_x': False,
            'flip_y': False,
            'duplicatelastframe': 5,
            'previewimage': True,
            'saveframes': False
        }
        # Get Config from Database and overwrite defaults
        dbconfig: Dict[str, Any] = self.database.get_item("timelapse",
                                                          "config",
                                                          self.config)
        if isinstance(dbconfig, asyncio.Future):
            self.config.update(dbconfig.result())
        else:
            self.config.update(dbconfig)
        # Overwrite Config with fixed config made in moonraker.conf
        # this is a fallback to older setups and when the Frontend doesn't
        # support the settings endpoint
        self.overwriteDbconfigWithConfighelper()
        # check if ffmpeg is installed
        self.ffmpeg_installed = os.path.isfile(self.ffmpeg_binary_path)
        if not self.ffmpeg_installed:
            self.config['autorender'] = False
            logging.info(f"timelapse: {self.ffmpeg_binary_path} \
                        not found please install to use render functionality")
        # setup directories
        # remove trailing "/"
        out_dir_cfg = os.path.join(out_dir_cfg, '')
        temp_dir_cfg = os.path.join(temp_dir_cfg, '')
        # evaluate and expand "~"
        self.out_dir = os.path.expanduser(out_dir_cfg)
        self.temp_dir = os.path.expanduser(temp_dir_cfg)
        # create directories if they doesn't exist
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)
        existing_frames = sorted(glob.glob(self.temp_dir + "frame*.jpg"))
        self.framecount = len(existing_frames)
        self.lastframefile = os.path.basename(existing_frames[-1]) if existing_frames else ""
        # setup eventhandlers and endpoints
        file_manager = self.server.lookup_component("file_manager")
        file_manager.register_directory("timelapse",
                                        self.out_dir,
                                        full_access=True
                                        )
        file_manager.register_directory("timelapse_frames", self.temp_dir)
        self.server.register_notification("timelapse:timelapse_event")
        self.server.register_event_handler(
            "server:gcode_response", self.handle_gcode_response)
        self.server.register_event_handler(
            "server:status_update", self.handle_status_update)
        self.server.register_event_handler(
            "server:klippy_ready", self.handle_klippy_ready)
        self.server.register_remote_method(
            "timelapse_newframe", self.call_newframe)
        self.server.register_remote_method(
            "timelapse_saveFrames", self.call_saveFramesZip)
        self.server.register_remote_method(
            "timelapse_render", self.call_render)
        self.server.register_endpoint(
            "/machine/timelapse/render", ['POST'], self.render)
        self.server.register_endpoint(
            "/machine/timelapse/saveframes", ['POST'], self.saveFramesZip)
        self.server.register_endpoint(
            "/machine/timelapse/settings", ['GET', 'POST'],
            self.webrequest_settings)
        self.server.register_endpoint(
            "/machine/timelapse/lastframeinfo", ['GET'],
            self.webrequest_lastframeinfo)
    async def component_init(self) -> None:
        await self.getWebcamConfig()

    def overwriteDbconfigWithConfighelper(self) -> None:
        blockedsettings = []
        for config in self.confighelper.get_options():
            if config in self.config:
                configtype = type(self.config[config])
                if configtype == str:
                    self.config[config] = self.confighelper.get(config)
                elif configtype == bool:
                    self.config[config] = self.confighelper.getboolean(config)
                elif configtype == int:
                    self.config[config] = self.confighelper.getint(config)
                elif configtype == float:
                    self.config[config] = self.confighelper.getfloat(config)
                # add the config to list of blockedsettings
                blockedsettings.append(config)

        # append the list of blockedsettings to the config dict
        self.config.update({'blockedsettings': blockedsettings})
        logging.debug(f"blockedsettings {self.config['blockedsettings']}")
    async def getWebcamConfig(self) -> None:
        # Read Webcam config from Database
        webcam_name = self.config['camera']
        try:
            wcmgr: WebcamManager = self.server.lookup_component("webcam")
            cams = wcmgr.get_webcams()
            if not cams:
                logging.info("WARNING: no camera configured, " +
                             "using the fallback config")
                fallback = {'snapshot_url': self.config['snapshoturl'],
                            'rotation': self.config['rotation'],
                            'flip_horizontal': self.config['flip_x'],
                            'flip_vertical': self.config['flip_y']
                            }
                self.parseWebcamConfig(fallback)
                return
            if webcam_name and webcam_name in cams:
                camera = cams[webcam_name]
            else:
                camera = list(cams.values())[0]

            self.parseWebcamConfig(camera.as_dict())

        except Exception as e:
            logging.info(f"something went wrong getting"
                         f"Cam Camera:{webcam_name} from Database. "
                         f"Exception: {e}"
                         )
    def parseWebcamConfig(self, webcamconfig) -> None:
        snapshoturl = webcamconfig['snapshot_url']
        flip_x = webcamconfig['flip_horizontal']
        flip_y = webcamconfig['flip_vertical']
        rotation = webcamconfig['rotation']
        oldWebcamConfig = {"url": self.config['snapshoturl'],
                           "flip_x": self.config['flip_x'],
                           "flip_y": self.config['flip_y'],
                           "rotation": self.config['rotation']
                           }
        self.config['snapshoturl'] = self.confighelper.get('snapshoturl',
                                                           snapshoturl
                                                           )
        self.config['flip_x'] = self.confighelper.getboolean('flip_x',
                                                             flip_x
                                                             )
        self.config['flip_y'] = self.confighelper.getboolean('flip_y',
                                                             flip_y
                                                             )
        self.config['rotation'] = self.confighelper.getint('rotation',
                                                           rotation
                                                           )
        if not self.config['snapshoturl'].startswith('http'):
            if not self.config['snapshoturl'].startswith('/'):
                self.config['snapshoturl'] = "http://localhost/" + \
                                             self.config['snapshoturl']
            else:
                self.config['snapshoturl'] = "http://localhost" + \
                                             self.config['snapshoturl']
        # check if settings have changed and if so creat log entry
        newWebcamConfig = {"url": self.config['snapshoturl'],
                           "flip_x": self.config['flip_x'],
                           "flip_y": self.config['flip_y'],
                           "rotation": self.config['rotation']
                           }
        if not oldWebcamConfig == newWebcamConfig:
            logging.info("snapshoturl: "
                         f"{self.config['snapshoturl']}, "
                         f"Flip V/H: {self.config['flip_y']}/"
                         f"{self.config['flip_y']}, "
                         f"rotation: {self.config['rotation']}"
                         )
    async def webrequest_lastframeinfo(self,
                                       webrequest: WebRequest
                                       ) -> Dict[str, Any]:
        return {
            'framecount': self.framecount,
            'lastframefile': self.lastframefile,
            'rendering': self.renderisrunning,
            'busy': (self.renderisrunning or self.saveisrunning
                     or self.takingframe or self.finishing_print),
        }

    async def webrequest_settings(self,
                                  webrequest: WebRequest
                                  ) -> Dict[str, Any]:
        action = webrequest.get_action()
        if action == 'POST':
            args = webrequest.get_args()
            logging.debug("webreq_args: " + str(args))
            gcodechange = False
            settingsWithGcodechange = [
                'enabled', 'parkhead',
                'parkpos', 'park_custom_pos_x',
                'park_custom_pos_y', 'park_custom_pos_dz',
                'park_travel_speed', 'park_retract_speed',
                'park_extrude_speed', 'park_retract_distance',
                'park_extrude_distance', 'park_time', 'fw_retract'
            ]
            modechanged = False
            for setting in args:
                if setting in self.config:
                    if setting == "snapshoturl":
                        logging.debug(
                            "snapshoturl cannot be changed via webrequest")
                        continue
                    settingtype = type(self.config[setting])
                    if settingtype == str:
                        settingvalue = webrequest.get(setting)
                    elif settingtype == bool:
                        settingvalue = webrequest.get_boolean(setting)
                    elif settingtype == int:
                        settingvalue = webrequest.get_int(setting)
                    elif settingtype == float:
                        settingvalue = webrequest.get_float(setting)
                    if setting == "targetlength" and settingvalue <= 0:
                        raise self.server.error(
                            "Timelapse target length must be greater than zero", 400)
                    self.config[setting] = settingvalue

                    self.database.insert_item(
                        "timelapse",
                        f"config.{setting}",
                        settingvalue
                    )
                    if setting == "camera":
                        if not self.noWebcamDb:
                            await self.getWebcamConfig()
                        else:
                            logging.info("Webcam Namespace not intialized, "
                                         "please restart moonraker service!")

                    if setting in settingsWithGcodechange:
                        gcodechange = True
                    if setting == "mode":
                        modechanged = True

                    logging.debug(f"changed setting: {setting} "
                                  f"value: {settingvalue} "
                                  f"type: {settingtype}"
                                  )
            if modechanged:
                if self.config['mode'] == "hyperlapse":
                    if not self.hyperlapserunning:
                        if self.printing:
                            ioloop = IOLoop.current()
                            ioloop.spawn_callback(self.start_hyperlapse)
                else:
                    if self.hyperlapserunning:
                        ioloop = IOLoop.current()
                        ioloop.spawn_callback(self.stop_hyperlapse)
            if gcodechange:
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.setgcodevariables)
        return self.config

    async def handle_klippy_ready(self) -> None:
        try:
            await self.klippy_apis.subscribe_objects({
                'gcode_macro _START_PRINT': ['print_active']})
        except Exception:
            logging.exception("Unable to subscribe to print preparation state")

        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.setgcodevariables)

        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.stop_hyperlapse)

    async def _run_gcode_without_history(self, command: str) -> None:
        # Moonraker's public run_gcode records every script in the console.
        # Use its ordinary Klippy request path for timelapse housekeeping.
        klippy = self.klippy_apis.klippy
        if not klippy.is_connected():
            raise self.server.error("Klippy Host not connected", 503)
        request = WebRequest(
            "gcode/script", {"script": command}, transport=self.klippy_apis)
        await klippy._request_standard(request)

    async def setgcodevariables(self) -> None:
        gcommand = "_SET_TIMELAPSE_SETUP " \
            + f" ENABLE={self.config['enabled']}" \
            + f" VERBOSE={self.config['gcode_verbose']}" \
            + f" PARK_ENABLE={self.config['parkhead']}" \
            + f" PARK_POS={self.config['parkpos']}" \
            + f" CUSTOM_POS_X={self.config['park_custom_pos_x']}" \
            + f" CUSTOM_POS_Y={self.config['park_custom_pos_y']}" \
            + f" CUSTOM_POS_DZ={self.config['park_custom_pos_dz']}" \
            + f" TRAVEL_SPEED={self.config['park_travel_speed']}" \
            + f" RETRACT_SPEED={self.config['park_retract_speed']}" \
            + f" EXTRUDE_SPEED={self.config['park_extrude_speed']}" \
            + f" RETRACT_DISTANCE={self.config['park_retract_distance']}" \
            + f" EXTRUDE_DISTANCE={self.config['park_extrude_distance']}" \
            + f" PARK_TIME={self.config['park_time']}" \
            + f" FW_RETRACT={self.config['fw_retract']}"
        logging.debug(f"run gcommand: {gcommand}")
        try:
            await self._run_gcode_without_history(gcommand)
        except self.server.error:
            msg = f"Error executing GCode {gcommand}"
            logging.exception(msg)
    def call_newframe(self, parked=False, hyperlapse=False, macropark=None) -> None:
        # Accept the upstream macropark payload during rolling upgrades, while
        # the Forge-X macro sends compact boolean flags.
        if macropark is not None:
            if isinstance(macropark, dict):
                parked = macropark.get('enable', False)
            else:
                parked = macropark
        if isinstance(parked, str):
            parked = parked.lower() == 'true'
        else:
            parked = bool(parked)
        if isinstance(hyperlapse, str):
            hyperlapse = hyperlapse.lower() == 'true'
        else:
            hyperlapse = bool(hyperlapse)

        if not self.config['enabled']:
            logging.info("NEW_FRAME macro ignored timelapse is disabled")
            self.release_declined_parked_frame(parked)
            return
        if self.config['mode'] == "hyperlapse" and not hyperlapse:
            logging.info("ignoring non hyperlapse triggered macros"
                         + "in hyperlapse mode")
            self.release_declined_parked_frame(parked)
            return
        if self.takingframe:
            logging.info("last take frame hasn't completed"
                         + " ignoring take frame command")
            self.release_declined_parked_frame(parked)
            return

        self.takingframe = True
        self.schedule_newframe(parked=parked)

    def release_declined_parked_frame(self, parked=False) -> None:
        # A parked macro has already paused Klipper before the remote call.
        # Release it immediately if Moonraker declines the frame instead of
        # making Klipper wait for the local timeout fallback.
        if parked:
            IOLoop.current().spawn_callback(self.release_parkedhead)

    def schedule_newframe(self, parked=False) -> None:
        # Frame capture is fire-and-forget for normal printing. Only a parked
        # capture needs a Moonraker -> Klipper acknowledgement so the local
        # macro knows when it is safe to resume motion.
        stream_delay = self.config['stream_delay_compensation']
        IOLoop.current().call_later(
            delay=stream_delay, callback=self.newframe, release_parked=parked,
            frame_generation=getattr(self, 'frame_generation', 0))

    async def release_parkedhead(self) -> None:
        gcommand = "SET_GCODE_VARIABLE " \
            + "MACRO=TIMELAPSE_TAKE_FRAME " \
            + "VARIABLE=takingframe VALUE=False"

        logging.debug(f"run gcommand: {gcommand}")
        try:
            await self._run_gcode_without_history(gcommand)
        except self.server.error:
            msg = f"Error executing GCode {gcommand}"
            logging.exception(msg)
    async def start_hyperlapse(self) -> None:
        hyperlapse_cycle = self.config['hyperlapse_cycle']
        park_time = self.config['park_time']
        timediff = hyperlapse_cycle - park_time
        if timediff >= 1:
            gcommand = "HYPERLAPSE ACTION=START" \
                       + f" CYCLE={hyperlapse_cycle}"
            logging.debug(f"run gcommand: {gcommand}")
            try:
                await self._run_gcode_without_history(gcommand)
            except self.server.error:
                msg = f"Error executing GCode {gcommand}"
                logging.exception(msg)
            self.hyperlapserunning = True
        else:
            logging.info("WARNING: Blocked start of Hyperlapse, because "
                         f"hyperlapse_cycle ({hyperlapse_cycle}s) is smaller "
                         f"then or to close to park_time ({park_time}s)"
                         )
    async def stop_hyperlapse(self) -> None:
        gcommand = "HYPERLAPSE ACTION=STOP"

        logging.debug(f"run gcommand: {gcommand}")
        try:
            await self._run_gcode_without_history(gcommand)
        except self.server.error:
            msg = f"Error executing GCode {gcommand}"
            logging.exception(msg)
        self.hyperlapserunning = False
    async def newframe(self, final_frame: bool = False,
                       release_parked: bool = False,
                       frame_generation: Optional[int] = None) -> None:
        generation = (getattr(self, 'frame_generation', 0)
                      if frame_generation is None else frame_generation)
        if generation != getattr(self, 'frame_generation', 0):
            return
        result = {'action': 'newframe', 'status': 'error'}
        try:
            if final_frame and self.printing:
                return
            free = os.statvfs(self.temp_dir)
            if (self.framecount >= self.MAX_FRAMES
                    or free.f_bavail * free.f_frsize < self.MIN_FREE_BYTES):
                logging.warning("Timelapse frame limit or disk reserve reached")
                return

            framefile = "frame" + str(self.framecount + 1).zfill(6) + ".jpg"
            framepath = os.path.join(self.temp_dir, framefile)
            candidate = framepath + "." + uuid.uuid4().hex + ".part"
            cmd = ("/usr/bin/curl --fail --silent --show-error --max-time 2 "
                   f"--output {shlex.quote(candidate)} "
                   f"{shlex.quote(self.config['snapshoturl'])}")
            shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
            scmd = shell_cmd.build_shell_command(cmd, None)
            try:
                captured = await scmd.run(timeout=3., verbose=False)
                if (captured and os.path.getsize(candidate) > 0
                        and generation == getattr(self, 'frame_generation', 0)
                        and not (final_frame and self.printing)):
                    os.replace(candidate, framepath)
                    self.framecount += 1
                    self.lastframefile = framefile
                    result.update({
                        'frame': str(self.framecount),
                        'framefile': framefile,
                        'status': 'success'
                    })
            finally:
                if os.path.exists(candidate):
                    os.remove(candidate)
        except Exception:
            logging.exception("Timelapse frame capture failed")
        finally:
            self.notify_event(result)
            if generation == getattr(self, 'frame_generation', 0):
                self.takingframe = False
            # Never inject housekeeping G-code during a normal capture. A
            # parked capture is the only path that needs an acknowledgement
            # back to Klipper, and it is sent after the snapshot has finished
            # (successfully or not) so the head cannot resume mid-capture.
            if release_parked and generation == getattr(self, 'frame_generation', 0):
                await self.release_parkedhead()
    async def handle_status_update(self, status: Dict[str, Any]) -> None:
        if 'print_stats' in status:
            printstats = status['print_stats']
            if printstats.get('filename'):
                self.active_print_filename = printstats['filename']
            if 'state' in printstats:
                state = printstats['state']
                if state in ('printing', 'paused'):
                    if not getattr(self, 'finishing_print', False):
                        self.printing = True
                        # The virtual SD file becomes active before START_PRINT.
                        if not self.renderisrunning:
                            await self._begin_print()
                if state == 'cancelled':
                    self.printing = False
                    self.pending_file_selected = False
                    ioloop = IOLoop.current()
                    ioloop.spawn_callback(self.stop_hyperlapse)

        start = status.get('gcode_macro _START_PRINT', {})
        if start.get('print_active') is True:
            if getattr(self, 'finishing_print', False):
                self.finishing_print = False
                self.finishing_filename = ""
                self.finishing_frame_generation = None
                self.pending_file_selected = True
            await self._begin_print()

    async def _begin_print(self, start_hyperlapse=True) -> None:
        self.printing = True
        if self.save_cancel is not None:
            self.save_cancel.set()

        if self.pending_file_selected:
            self.cleanup()
            self.pending_file_selected = False
            if start_hyperlapse and self.config['mode'] == "hyperlapse":
                IOLoop.current().spawn_callback(self.start_hyperlapse)
        if self.render_command is not None:
            await self.render_command.cancel()

    async def handle_gcode_response(self, gresponse: str) -> None:
        if gresponse == "File selected":
            self.pending_file_selected = True

        elif gresponse == "Done printing file":
            if self.pending_file_selected:
                await self._begin_print(start_hyperlapse=False)
            self.printing = False
            # stop hyperlapse if mode is set
            if self.config['mode'] == "hyperlapse":
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.stop_hyperlapse)
            if self.config['enabled']:
                self.finishing_filename = getattr(self, 'active_print_filename', '')
                self.finishing_frame_generation = getattr(self, 'frame_generation', 0)
                self.finishing_print = True
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self._finish_print,
                                      getattr(self, 'frame_generation', 0))

    async def _finish_print(self, generation=None):
        if generation is None:
            generation = getattr(self, 'frame_generation', 0)
        try:
            if generation == getattr(self, 'frame_generation', 0):
                await self._finish_print_frames(generation)
        finally:
            if generation == getattr(self, 'frame_generation', 0):
                self.finishing_print = False
                self.finishing_filename = ""
                self.finishing_frame_generation = None

    async def _finish_print_frames(self, generation):
        try:
            status = await self.klippy_apis.query_objects({
                'mod_params': ['variables'],
                'gcode_macro _TIMELAPSE_LAYER_CAPTURE': ['last_layer'],
                'gcode_macro TIMELAPSE_PRINT': ['enable']})
        except self.server.error:
            logging.exception("Unable to check timelapse print settings")
            status = {}

        if self.printing or generation != getattr(self, 'frame_generation', 0):
            return

        params = status.get('mod_params', {}).get('variables', {})
        last_layer = status.get('gcode_macro _TIMELAPSE_LAYER_CAPTURE', {}).get('last_layer', 0)
        print_enabled = status.get('gcode_macro TIMELAPSE_PRINT', {}).get('enable', True)
        if (params.get('timelapse') and params.get('timelapse_mode') == 'LAYER' and print_enabled
                and last_layer == 0 and self.framecount == 0):
            self.server.send_event(
                "server:gcode_response",
                "!! Timelapse: no layer updates were received. Configure "
                "SET_PRINT_STATS_INFO in the slicer or choose time/progress "
                "capture. See docs/SLICING.md in the project GitHub.")

        # An in-flight layer/time frame owns the next frame number. Do not
        # export or duplicate it until that capture has finished.
        for _ in range(100):
            if self.printing or generation != getattr(self, 'frame_generation', 0):
                return
            if not getattr(self, 'takingframe', False):
                break
            await asyncio.sleep(0.1)
        if getattr(self, 'takingframe', False):
            logging.warning("Skipping timelapse export: previous capture is still running")
            return
        if self.printing or generation != getattr(self, 'frame_generation', 0):
            return

        if params.get('timelapse') and params.get('timelapse_final_frame') and print_enabled:
            self.takingframe = True
            await self.newframe(final_frame=True)

        if self.printing or generation != getattr(self, 'frame_generation', 0):
            return

        if self.config['saveframes']:
            await self.saveFramesZip()
        if (self.config['autorender']
                and generation == getattr(self, 'frame_generation', 0)):
            await self.render()
    def cleanup(self) -> None:
        logging.debug("cleanup frame directory")
        self.frame_generation = getattr(self, 'frame_generation', 0) + 1
        self.takingframe = False
        filelist = glob.glob(self.temp_dir + "frame*.jpg")
        if filelist:
            for filepath in filelist:
                os.remove(filepath)
        self.framecount = 0
        self.lastframefile = ""

    def call_saveFramesZip(self) -> None:
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.saveFramesZip)

    def _available_output_name(self, base: str, *suffixes: str) -> str:
        name = base
        number = 2
        while any(os.path.exists(os.path.join(self.out_dir, name + suffix))
                  for suffix in suffixes):
            name = f"{base}_{number}"
            number += 1
        return name

    async def saveFramesZip(self, webrequest=None):
        if getattr(self, 'takingframe', False):
            return {'action': 'saveframes', 'status': 'running',
                    'msg': 'Frame capture is still running'}
        if not glob.glob(self.temp_dir + "frame*.jpg"):
            return {'action': 'saveframes', 'status': 'skipped'}
        generation = getattr(self, 'frame_generation', 0)
        if self.saveisrunning or self.renderisrunning:
            return {'action': 'saveframes', 'status': 'running'}

        self.saveisrunning = True
        cancel = threading.Event()
        self.save_cancel = cancel
        candidate = None
        try:
            if not (printer_status := await self._idle_status_for_render()):
                return {'action': 'saveframes', 'status': 'error',
                        'msg': 'Frame export is available only after printing stops'}
            if generation != getattr(self, 'frame_generation', 0):
                return {'action': 'saveframes', 'status': 'skipped'}
            if getattr(self, 'takingframe', False):
                return {'action': 'saveframes', 'status': 'running',
                        'msg': 'Frame capture is still running'}
            filelist = sorted(glob.glob(self.temp_dir + "frame*.jpg"))
            if not filelist:
                return {'action': 'saveframes', 'status': 'skipped'}
            if not self._has_render_space(filelist):
                return {'action': 'saveframes', 'status': 'error',
                        'msg': 'Not enough disk space to save frames'}

            self.framecount = len(filelist)
            pstats = printer_status['print_stats']
            gcodefilename = pstats.get('filename', '').split('/')[-1]
            date_time = datetime.now().strftime(self.config['time_format_code'])
            base = f"timelapse_{gcodefilename}_{date_time}"
            outfile = self._available_output_name(base, '_frames.zip') + '_frames.zip'
            output_path = os.path.join(self.out_dir, outfile)
            candidate = output_path + '.part'
            await asyncio.to_thread(self._write_frames_zip, candidate,
                                    filelist, cancel)
            if cancel.is_set() or generation != getattr(self, 'frame_generation', 0):
                raise InterruptedError("Print started during frame export")
            os.replace(candidate, output_path)
            logging.info(f"saved frames: {outfile}")
            return {'action': 'saveframes', 'status': 'finished',
                    'zipfile': outfile}
        except InterruptedError:
            return {'action': 'saveframes', 'status': 'skipped',
                    'msg': 'Frame export stopped for a new print'}
        except Exception:
            logging.exception("Unable to save timelapse frames")
            return {'action': 'saveframes', 'status': 'error',
                    'msg': 'Unable to save frames'}
        finally:
            if candidate is not None:
                try:
                    os.remove(candidate)
                except FileNotFoundError:
                    pass
                except OSError:
                    logging.exception("Unable to remove incomplete frame archive")
            self.saveisrunning = False
            self.save_cancel = None

    @staticmethod
    def _write_frames_zip(path, filelist, cancel):
        with ZipFile(path, 'w') as archive:
            for frame in filelist:
                if cancel.is_set():
                    raise InterruptedError("Print started during frame export")
                archive.write(frame, os.path.basename(frame))
    def call_render(self, byrendermacro=False) -> None:
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.render, byrendermacro=byrendermacro)

    async def render(self, webrequest=None, byrendermacro=False):
        if self.renderisrunning or getattr(self, 'takingframe', False):
            result = {'action': 'render', 'status': 'running',
                      'msg': ('render is already running' if self.renderisrunning
                              else 'Frame capture is still running')}
            self.notify_event(result)
            if byrendermacro:
                await self._release_render_macro()
            return result

        self.renderisrunning = True
        generation = getattr(self, 'frame_generation', 0)
        temporary_paths = []
        try:
            return await self._render(temporary_paths)
        except Exception:
            logging.exception("Timelapse render failed")
            result = {'action': 'render', 'status': 'error',
                      'msg': 'Rendering video failed'}
            self.notify_event(result)
            self.server.send_event(
                "server:gcode_response", "!! Timelapse: video generation failed")
            return result
        finally:
            for path in temporary_paths:
                if (generation != getattr(self, 'frame_generation', 0)
                        and os.path.basename(path).startswith('frame')):
                    continue
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                except OSError:
                    logging.exception("Unable to remove temporary timelapse file")
            self.renderisrunning = False
            if byrendermacro:
                await self._release_render_macro()

    async def _release_render_macro(self) -> None:
        gcommand = ("SET_GCODE_VARIABLE MACRO=TIMELAPSE_RENDER "
                    "VARIABLE=render VALUE=False")
        try:
            await self._run_gcode_without_history(gcommand)
        except Exception:
            logging.exception("Unable to release timelapse render macro")

    async def _render(self, temporary_paths):
        generation = getattr(self, 'frame_generation', 0)
        result = {'action': 'render'}

        # make sure webcamconfig is uptodate for the rotation/flip feature
        await self.getWebcamConfig()
        filelist = sorted(glob.glob(self.temp_dir + "frame*.jpg"))
        if generation != getattr(self, 'frame_generation', 0):
            msg = "Print started during timelapse render"
            status = "skipped"
        elif getattr(self, 'takingframe', False):
            msg = "Frame capture is still running"
            status = "running"
        elif not filelist:
            msg = "no frames to render, skip"
            status = "skipped"
        elif self.saveisrunning:
            msg = "frame export is already running"
            status = "running"
        elif not self.ffmpeg_installed:
            msg = f"{self.ffmpeg_binary_path} not found, please install ffmpeg"
            status = "error"
            # cmd = outfile = None
            logging.info(f"timelapse: {msg}")
        elif not (printer_status := await self._idle_status_for_render()):
            msg = "Render is available only after printing stops"
            status = "error"
        elif generation != getattr(self, 'frame_generation', 0):
            msg = "Print started during timelapse render"
            status = "skipped"
        elif getattr(self, 'takingframe', False):
            msg = "Frame capture is still running"
            status = "running"
        elif not self._has_render_space(filelist):
            msg = "Not enough disk space to render timelapse"
            status = "error"
        else:
            render_framecount = len(filelist)
            # get printed filename
            pstats = printer_status["print_stats"]
            gcodefilename = pstats.get("filename", "").split("/")[-1]
            # prepare output filename
            now = datetime.now()
            date_time = now.strftime(self.config['time_format_code'])
            inputfiles = self.temp_dir + "frame%06d.jpg"
            base = f"timelapse_{gcodefilename}_{date_time}"
            outfile = self._available_output_name(base, '.mp4', '.jpg')
            temp_video_path = self.temp_dir + outfile + ".mp4"
            temporary_paths.append(temp_video_path)

            # dublicate last frame
            if self.config['duplicatelastframe'] > 0:
                lastframe = filelist[-1:][0]
                for i in range(self.config['duplicatelastframe']):
                    nextframe = str(render_framecount + i + 1).zfill(6)
                    duplicate = "frame" + nextframe + ".jpg"
                    duplicatePath = self.temp_dir + duplicate
                    temporary_paths.append(duplicatePath)
                    try:
                        shutil.copy(lastframe, duplicatePath)
                    except OSError as err:
                        logging.info(f"duplicating last frame failed: {err}")
                # update Filelist
                filelist = sorted(glob.glob(self.temp_dir + "frame*.jpg"))
                render_framecount = len(filelist)
            # variable framerate
            if self.config['variable_fps']:
                fps = int(render_framecount / self.config['targetlength'])
                fps = max(min(fps,
                              self.config['variable_fps_max']),
                          self.config['variable_fps_min'])
            else:
                fps = self.config['output_framerate']
            # apply rotation
            filterParam = ""
            if self.config['rotation'] == 90 and self.config['flip_y']:
                filterParam = " -vf 'transpose=3'"
            elif self.config['rotation'] == 90:
                filterParam = " -vf 'transpose=1'"
            elif self.config['rotation'] == 180:
                filterParam = " -vf 'hflip,vflip'"
            elif self.config['rotation'] == 270 and self.config['flip_y']:
                filterParam = " -vf 'transpose=0'"
            elif self.config['rotation'] == 270:
                filterParam = " -vf 'transpose=2'"
            elif self.config['rotation'] > 0:
                pi = 3.141592653589793
                rot = str(self.config['rotation']*(pi/180))
                filterParam = " -vf 'rotate=" + rot + "'"
            elif self.config['flip_x'] and self.config['flip_y']:
                filterParam = " -vf 'hflip,vflip'"
            elif self.config['flip_x']:
                filterParam = " -vf 'hflip'"
            elif self.config['flip_y']:
                filterParam = " -vf 'vflip'"
            # build shell command
            cmd = self.ffmpeg_binary_path \
                + " -hide_banner -loglevel error -nostats" \
                + " -r " + str(fps) \
                + " -i " + shlex.quote(inputfiles) \
                + filterParam \
                + " -threads 1 -preset ultrafast -g 5" \
                + " -crf " + str(self.config['constant_rate_factor']) \
                + " -vcodec libx264" \
                + " -pix_fmt " + self.config['pixelformat'] \
                + " -an" \
                + " " + self.config['extraoutputparams'] \
                + " " + shlex.quote(temp_video_path) + " -y"
            # log and notify ws
            logging.info(f"start FFMPEG: {cmd}")
            result.update({
                'status': 'started',
                'framecount': str(render_framecount),
                'settings': {
                    'framerate': fps,
                    'crf': self.config['constant_rate_factor'],
                    'pixelformat': self.config['pixelformat']
                }
            })
            # run the command
            shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
            self.notify_event(result)
            self.lastcmdreponse = ""
            scmd = shell_cmd.build_shell_command(cmd, self.ffmpeg_cb)
            self.render_command = scmd
            self.server.send_event(
                "server:gcode_response", "// Timelapse: video generation started")
            try:
                cmdstatus = await scmd.run(verbose=True,
                                           log_complete=False,
                                           timeout=9999999999,
                                           )
            except Exception:
                logging.exception(f"Error running cmd '{cmd}'")
                cmdstatus = False
            finally:
                self.render_command = None
            if generation != getattr(self, 'frame_generation', 0):
                return {'action': 'render', 'status': 'skipped',
                        'msg': 'Print started during timelapse render'}
            # check success
            if cmdstatus:
                status = "success"
                msg = f"Rendering Video successful: {outfile}.mp4"
                # result.pop("framecount")
                result.pop("settings")
                # move finished output file to output directory
                try:
                    shutil.move(temp_video_path,
                                self.out_dir + outfile + ".mp4")
                except OSError as err:
                    status = "error"
                    msg = f"Moving rendered video failed: {err}"
                else:
                    result.update({
                        'filename': f"{outfile}.mp4",
                        'printfile': gcodefilename
                    })
                # A transformed preview must be written to a different file.
                if status == "success" and self.config['previewimage']:
                    previewFile = f"{outfile}.jpg"
                    previewFilePath = self.out_dir + previewFile
                    previewSrc = filelist[-1:][0]
                    if filterParam or self.config['extraoutputparams']:
                        preview_candidate = previewFilePath + '.part.jpg'
                        temporary_paths.append(preview_candidate)
                        cmd = (shlex.quote(self.ffmpeg_binary_path)
                               + " -hide_banner -loglevel error -nostats"
                               + " -i " + shlex.quote(previewSrc)
                               + filterParam
                               + " -frames:v 1 -update 1 -an"
                               + " " + self.config['extraoutputparams']
                               + " " + shlex.quote(preview_candidate) + " -y")
                        logging.info(f"Render preview image: {cmd}")
                        try:
                            scmd = shell_cmd.build_shell_command(cmd)
                            self.render_command = scmd
                            preview_ok = await scmd.run(verbose=True,
                                                        log_complete=False,
                                                        timeout=9999999999)
                            if (generation == getattr(self, 'frame_generation', 0)
                                    and preview_ok
                                    and os.path.getsize(preview_candidate) > 0):
                                os.replace(preview_candidate, previewFilePath)
                                result['previewimage'] = previewFile
                            else:
                                logging.warning("Timelapse preview conversion failed")
                        except Exception:
                            logging.exception("Timelapse preview conversion failed")
                        finally:
                            self.render_command = None
                    else:
                        try:
                            shutil.copy(previewSrc, previewFilePath)
                            result['previewimage'] = previewFile
                        except OSError:
                            logging.exception("Copying timelapse preview failed")
            else:
                status = "error"
                msg = f"Rendering Video failed: {cmd} : {self.lastcmdreponse}"
                result.update({
                    'cmd': cmd,
                    'cmdresponse': self.lastcmdreponse
                })

            if status == "success":
                self.server.send_event(
                    "server:gcode_response", "// Timelapse: video generation finished")
            else:
                self.server.send_event(
                    "server:gcode_response", "!! Timelapse: video generation failed")

        # log and notify ws
        logging.info(msg)
        result.update({
            'status': status,
            'msg': msg
        })
        self.notify_event(result)
        return result

    async def _idle_status_for_render(self):
        try:
            status = await self.klippy_apis.query_objects({
                'print_stats': None, 'virtual_sdcard': None,
                'gcode_macro _TIMELAPSE_START_GUARD': ['waiting']})
            idle = (status['print_stats']['state'] in ('complete', 'cancelled', 'standby')
                    and not status['virtual_sdcard']['is_active'])
            guard = status.get('gcode_macro _TIMELAPSE_START_GUARD', {})
            finishing = getattr(self, 'finishing_print', False)
            waiting = (finishing and guard.get('waiting')
                       and not status['virtual_sdcard']['is_active']
                       and status['print_stats']['state'] == 'paused')
            if idle or waiting:
                generation = getattr(self, 'frame_generation', 0)
                previous_name = (getattr(self, 'finishing_filename', '')
                                 if finishing and getattr(
                                     self, 'finishing_frame_generation', generation)
                                 == generation else '')
                if previous_name:
                    status = dict(status)
                    status['print_stats'] = dict(status['print_stats'])
                    status['print_stats']['filename'] = previous_name
                return status
        except Exception:
            logging.exception("Unable to verify idle printer before timelapse render")
        return None

    def _has_render_space(self, filelist) -> bool:
        try:
            fs = os.statvfs(self.temp_dir)
            free = fs.f_bavail * fs.f_frsize
            frame_bytes = sum(os.path.getsize(path) for path in filelist)
            return free >= frame_bytes + self.MIN_FREE_BYTES
        except OSError:
            logging.exception("Unable to verify timelapse render space")
            return False

    def ffmpeg_cb(self, response):
        self.lastcmdreponse = response.decode("utf-8", errors="replace")[-1024:]
    def notify_event(self, result: Dict[str, Any]) -> None:
        logging.debug(f"notify_event: {result}")
        self.server.send_event("timelapse:timelapse_event", result)


def load_component(config: ConfigHelper) -> Timelapse:
    return Timelapse(config)
