# Klippy job state event handlers
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import logging

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Dict,
    List,
)
from ..common import JobEvent, KlippyState
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .klippy_apis import KlippyAPI

class JobState:
    SUBSCRIPTION_RETRY_DELAY = 1.0
    MAX_SUBSCRIPTION_ATTEMPTS = 3

    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.last_print_stats: Dict[str, Any] = {}
        self.last_event: JobEvent = JobEvent.STANDBY
        self._subscription_generation = 0
        self._subscribe_retry_handle: Optional[asyncio.TimerHandle] = None
        self.server.register_event_handler(
            "server:klippy_started", self._handle_started
        )
        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_disconnect
        )

    def _handle_disconnect(self):
        self._reset_subscription()
        state = self.last_print_stats.get("state", "")
        if state in ("printing", "paused"):
            # set error state
            self.last_print_stats["state"] = "error"
            self.last_event = JobEvent.ERROR

    async def _handle_started(self, state: KlippyState) -> None:
        self._reset_subscription()
        if state != KlippyState.READY:
            return
        await self._subscribe_print_stats(self._subscription_generation, 1)

    def _reset_subscription(self) -> None:
        self._subscription_generation += 1
        if self._subscribe_retry_handle is not None:
            self._subscribe_retry_handle.cancel()
            self._subscribe_retry_handle = None

    async def _subscribe_print_stats(self, generation: int, attempt: int) -> None:
        if generation != self._subscription_generation:
            return
        self._subscribe_retry_handle = None
        kapis: KlippyAPI = self.server.lookup_component('klippy_apis')
        sub: Dict[str, Optional[List[str]]] = {"print_stats": None}
        try:
            result = await kapis.subscribe_objects(sub, self._status_update)
        except self.server.error:
            if generation != self._subscription_generation:
                return
            logging.info("Error subscribing to print_stats (attempt %d/%d)",
                         attempt, self.MAX_SUBSCRIPTION_ATTEMPTS)
            if attempt < self.MAX_SUBSCRIPTION_ATTEMPTS:
                self._subscribe_retry_handle = (
                    self.server.get_event_loop().delay_callback(
                        self.SUBSCRIPTION_RETRY_DELAY,
                        self._subscribe_print_stats, generation, attempt + 1))
            else:
                logging.warning(
                    "Unable to subscribe to print_stats; job completion events "
                    "are unavailable until Klipper reconnects")
            return
        # Ignore a response belonging to a disconnected or replaced session.
        if generation != self._subscription_generation:
            return
        self.last_print_stats = result.get("print_stats", {})
        if "state" in self.last_print_stats:
            logging.info("Job state initialized: %s", self.last_print_stats["state"])

    async def close(self) -> None:
        self._reset_subscription()

    async def _status_update(self, data: Dict[str, Any], _: float) -> None:
        if 'print_stats' not in data:
            return
        ps = data['print_stats']
        if "state" in ps:
            prev_ps = dict(self.last_print_stats)
            old_state: str = prev_ps['state']
            new_state: str = ps['state']
            new_ps = dict(self.last_print_stats)
            new_ps.update(ps)
            if new_state is not old_state:
                if new_state == "printing":
                    # The "printing" state needs some special handling
                    # to detect "resets" and a transition from pause to resume
                    if self._check_resumed(prev_ps, new_ps):
                        new_state = "resumed"
                    else:
                        logging.info(
                            f"Job Started: {new_ps['filename']}"
                        )
                        new_state = "started"
                logging.debug(
                    f"Job State Changed - Prev State: {old_state}, "
                    f"New State: {new_state}"
                )
                # NOTE: Individual job_state events are DEPRECATED.  New modules
                # should register handlers for "job_state: status_changed" and
                # match against the JobEvent object provided.
                self.server.send_event(f"job_state:{new_state}", prev_ps, new_ps)
                self.last_event = JobEvent.from_string(new_state)
                self.server.send_event(
                    "job_state:state_changed",
                    self.last_event,
                    prev_ps,
                    new_ps
                )
        if "info" in ps:
            cur_layer: Optional[int] = ps["info"].get("current_layer")
            if cur_layer is not None:
                total: int = ps["info"].get("total_layer", 0)
                self.server.send_event(
                    "job_state:layer_changed", cur_layer, total
                )
        self.last_print_stats.update(ps)

    def _check_resumed(self,
                       prev_ps: Dict[str, Any],
                       new_ps: Dict[str, Any]
                       ) -> bool:
        return (
            prev_ps['state'] == "paused" and
            prev_ps['filename'] == new_ps['filename'] and
            prev_ps['total_duration'] < new_ps['total_duration']
        )

    def get_last_stats(self) -> Dict[str, Any]:
        return dict(self.last_print_stats)

    def get_last_job_event(self) -> JobEvent:
        return self.last_event

def load_component(config: ConfigHelper) -> JobState:
    return JobState(config)
