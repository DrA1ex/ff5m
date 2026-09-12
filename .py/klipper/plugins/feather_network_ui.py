# Feather UI adaptation for the netd client.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

"""Network pages and product-facing policy for Feather.

Transport lifecycle and wire parsing live below this layer. This module keeps
only UI state and the product decisions that belong near the screen: password
prompting, presentation, foreground request completion and explicit CANCEL.
"""

import logging

import feather_netd_protocol as protocol
import feather_network
from feather_keyboard import TEXT_KEYBOARD, is_keyboard_action
from feather_pagination import Pagination
from ui import ThemeColor
from ff5m_ui.screen import ScreenPage


NETWORK_ROWS = 5
NETWORK_WATCHDOG = 20.0
NETWORK_WATCHDOG_PROBE = 5.0

NETWORK_ERRORS = {
    "WRONG_KEY": "Wrong Wi-Fi password",
    "AUTH_FAILED": "Wi-Fi authentication failed",
    "ASSOC_REJECT": "The access point refused the connection",
    "NOT_FOUND": "Network not found",
    "NO_CARRIER": "The link went down",
    "DHCP_TIMEOUT": "No address received from DHCP",
    "CANCELLED": "Network operation cancelled",
    "TIMEOUT": "Network operation timed out",
    "DISCONNECTED": "Wi-Fi connection was lost",
    "SUPPLICANT_GONE": "The Wi-Fi service stopped",
    "WIFI_START_FAILED": "Unable to start the Wi-Fi connection",
    "NO_CONFIG": "No saved Wi-Fi network",
    "BUSY": "Another network operation is already running",
    "SCAN_IN_PROGRESS": "A Wi-Fi scan is already running",
    "SCAN_FAILED": "Wi-Fi scan failed",
    "MISSING_SSID": "No network selected",
    "PASSWORD_REQUIRED": "This network requires a password",
    "INVALID_PSK": "Invalid Wi-Fi password",
    "PERSIST_FAILED": "Unable to save network configuration",
    "NO_PROFILE": "Saved Wi-Fi profile is unavailable",
    "WPA_CONTROL_FAILED": "Wi-Fi service did not respond",
    "WPA_CONFIG_FAILED": "Unable to configure the Wi-Fi connection",
    "WPA_SELECT_FAILED": "Unable to select the Wi-Fi network",
}

NETWORK_PHASES = {
    "STARTUP": "Checking current network...",
    "PREPARING": "Preparing Wi-Fi...",
    "FINDING_NETWORK": "Looking for the selected network...",
    "ASSOCIATING": "Associating...",
    "HANDSHAKE": "Checking the password...",
    "RETRYING": "Restarting Wi-Fi for a fresh attempt...",
    "ASSOCIATED": "Associated, requesting an address...",
    "DHCP_WAIT": "Waiting for an address...",
    "ETHERNET_DHCP": "Ethernet: waiting for an address...",
    "RECOVERY": "Recovering Wi-Fi...",
    "NO_CARRIER": "Waiting for Ethernet carrier...",
    "CANCELLING": "Cancelling network operation...",
}


class FeatherNetworkPagesMixin:
    """Application/UI side of networking; netd remains the network owner."""

    def _init_network_ui(self):
        self.network_client = feather_network.NetworkClient(
            self.reactor, self._on_network_event)
        # This is the client's authoritative received snapshot, not a second
        # independently synchronized UI representation.
        self.network_status = self.network_client.status
        self.network_operation = None
        self.network_return_page = ScreenPage.NETWORK_HOME
        self.network_parent_page = ScreenPage.MAIN_MENU
        self.network_deadline = 0.0
        self.network_probe_pending = False
        self.network_cancel_pending = False
        self.networks = []
        self.network_page = 0
        self.selected_network = None
        self.password = ""
        self.password_cursor = 0
        self.keyboard_shift = False
        self.keyboard_symbols = False
        self.password_visible = False

    def _render_network_home(self):
        status = self.network_status
        commands = self.renderer.begin_page("Network", back=True)
        lines = ["Mode: %s   State: %s" % (
            status.get("mode", "OFFLINE"),
            status.get("state", "DISCONNECTED"))]
        if status.get("ssid"):
            lines.append("SSID: %s   Signal: %s dBm" %
                         (status["ssid"], status.get("signal") or "?"))
        if not self._network_available():
            lines.append("Network daemon unavailable")
        lines.append("IP: %s" % (status.get("ip") or "Offline"))
        for index, line in enumerate(lines):
            commands.append(self.renderer.text(
                400, 75 + index * 35, line, ThemeColor.BRIGHT, "Roboto 10pt", "center",
                max_width=660, truncate=True))
        available = self._network_available() and not self._network_busy()
        commands += self.renderer.button(
            "net.scan", 55, 190, 320, 150, "WI-FI", active=available,
            font="JetBrainsMono 12pt")
        ethernet_selected = (
            status.get("mode") == "ETHERNET"
            and status.get("state") == "CONNECTED")
        commands += self.renderer.button(
            "net.ethernet", 425, 190, 320, 150, "ETHERNET DHCP",
            active=available and not ethernet_selected,
            font="JetBrainsMono 12pt",
            subtitle="(ALREADY SELECTED)" if ethernet_selected else None)
        self.renderer.send(commands)

    def _open_network_page(self):
        # CONNECTING from boot, the CLI or another client is daemon state. The
        # UI does not reconstruct operation ownership from marker files.
        if self._network_busy():
            self.network_operation = None
            self._show_page(ScreenPage.NETWORK_PROGRESS)
            return
        self._request_network_snapshot()
        self._show_page(ScreenPage.NETWORK_HOME)

    def _handle_network_action(self, action):
        if action in ("net.scan", "net.rescan"):
            self._start_scan()
        elif action == "net.ethernet":
            self._start_ethernet()
        elif action in ("net.prev", "net.next"):
            self.network_page += -1 if action == "net.prev" else 1
            self._render_wifi_scan()
        elif action.startswith("net.item"):
            index = int(action[len("net.item"):])
            pagination = Pagination(
                self.networks, self.network_page, NETWORK_ROWS)
            offset = pagination.absolute_index(index)
            if offset is not None:
                self.selected_network = self.networks[offset]
                self.password = ""
                self.password_cursor = 0
                self.keyboard_shift = self.keyboard_symbols = False
                if self.selected_network.get("saved"):
                    self._connect_saved_wifi()
                else:
                    self._show_page(ScreenPage.WIFI_PASSWORD)
        elif action == "net.reset.saved":
            if (self.selected_network is None
                    or not self.selected_network.get("saved")):
                raise RuntimeError("No saved Wi-Fi password is selected")
            self.password = ""
            self.password_cursor = 0
            self.keyboard_shift = self.keyboard_symbols = False
            self._show_page(ScreenPage.WIFI_PASSWORD)
        elif action == "net.connect":
            self._connect_wifi()
        elif action == "net.cancel":
            self._cancel_network_operation()
        elif action == "net.keep":
            self._show_page(getattr(
                self, "network_parent_page", ScreenPage.IDLE_HOME))
        elif action == "net.password.toggle":
            self.password_visible = not self.password_visible
            self._render_keyboard()
        elif is_keyboard_action(action):
            (self.password, self.password_cursor, self.keyboard_shift,
             self.keyboard_symbols) = TEXT_KEYBOARD.apply(
                self.password, self.password_cursor, action, self.keyboard_shift,
                self.keyboard_symbols, max_length=64)
            self._render_keyboard()

    def _begin_network_operation(self, operation, return_page):
        self.network_operation = operation
        self.network_cancel_pending = False
        self.network_return_page = return_page
        self.network_deadline = self.reactor.monotonic() + NETWORK_WATCHDOG
        self.network_probe_pending = False
        self._show_page(ScreenPage.NETWORK_PROGRESS)

    def _require_network_ready(self):
        if self.network_operation is not None:
            raise RuntimeError("A network operation is already running")
        if not self._network_available():
            raise RuntimeError("Network service is unavailable")

    def _start_scan(self):
        self._require_network_ready()
        self.networks = []
        if not self.network_client.scan():
            raise RuntimeError("Network service is unavailable")
        self._begin_network_operation("scan", ScreenPage.NETWORK_HOME)

    def _start_ethernet(self):
        self._require_network_ready()
        if not self.network_client.use_ethernet():
            raise RuntimeError("Network service is unavailable")
        self._begin_network_operation("ethernet", ScreenPage.NETWORK_HOME)

    def _network_available(self):
        return self.network_client is not None and self.network_client.connected

    def _network_busy(self):
        return (self.network_operation is not None
                or self.network_cancel_pending
                or self.network_status.get("state") == "CONNECTING")

    def _request_network_snapshot(self):
        if self._network_available():
            self.network_client.get_state()

    def _initialize_network_monitoring(self):
        self.network_client.start()

    def _service_network(self, eventtime):
        """Service only the Python <-> netd IPC lifecycle.

        Re-attaching this client is not network reconnection policy. The daemon
        continues its own operation independently if this socket disappears.
        """
        self.network_client.service(eventtime)

        deadline = self.network_deadline
        if self.network_operation is not None and deadline and eventtime >= deadline:
            if not self.network_probe_pending:
                if self.network_client.get_state():
                    self.network_probe_pending = True
                    self.network_deadline = eventtime + NETWORK_WATCHDOG_PROBE
                    return
            logging.warning("[feather_screen] netd went silent during %s",
                            self.network_operation)
            self._finish_network_operation(
                "Network service stopped responding", eventtime)
            self.network_client.mark_unresponsive(eventtime)

    def _stop_network_client(self):
        """Stop IPC observation; never translate socket close into CANCEL."""
        if self.network_client is not None:
            self.network_client.stop()
        self.network_operation = None
        self.network_deadline = 0.0
        self.network_probe_pending = False
        self.network_cancel_pending = False

    def _repaint_network(self, eventtime):
        if self.page == ScreenPage.NETWORK_HOME:
            self._render_network_home()
        elif self.page == ScreenPage.NETWORK_PROGRESS:
            self._render_network_progress()
        elif self.page == ScreenPage.IDLE_HOME:
            self._update_dashboard(eventtime)

    def _on_network_event(self, kind, value, eventtime):
        """Translate one semantic client event into application/UI state."""
        if kind == "unavailable":
            self.network_cancel_pending = False
            if self.network_operation is not None:
                self._finish_network_operation(
                    "Network service is unavailable", eventtime)
            elif value:
                self._repaint_network(eventtime)
            return

        if self.network_operation is not None:
            interval = (NETWORK_WATCHDOG_PROBE if self.network_probe_pending
                        else NETWORK_WATCHDOG)
            self.network_deadline = eventtime + interval

        if kind == "snapshot":
            if value:
                if (self.page == ScreenPage.NETWORK_HOME
                        and self.network_status.get("state") == "CONNECTING"):
                    self._show_page(ScreenPage.NETWORK_PROGRESS)
                    return
                if (self.page == ScreenPage.NETWORK_PROGRESS
                        and self.network_operation is None
                        and not self.network_cancel_pending
                        and self.network_status.get("state") != "CONNECTING"):
                    self._show_page(ScreenPage.NETWORK_HOME)
                    return
                self._repaint_network(eventtime)
            return

        if kind == "scan":
            # Feather currently has only a PSK password flow. netd reports all
            # scan rows; this presentation layer chooses what the product offers.
            if "PSK" in value["security"]:
                self.networks.append(value)
            return

        if kind == "ok":
            if self.network_probe_pending:
                self.network_probe_pending = False
                self.network_deadline = eventtime + NETWORK_WATCHDOG
                return
            cancelled_daemon_operation = (
                self.network_cancel_pending
                and self.network_operation is None)
            self.network_cancel_pending = False
            if cancelled_daemon_operation:
                self._show_page(ScreenPage.NETWORK_HOME)
                return
            self._finish_network_operation(None, eventtime)
            return

        if kind == "error":
            cancelled_daemon_operation = (
                self.network_cancel_pending
                and self.network_operation is None)
            self.network_cancel_pending = False
            if cancelled_daemon_operation:
                self._show_message(
                    NETWORK_ERRORS.get(value, "Network operation failed"),
                    ScreenPage.NETWORK_PROGRESS)
                return
            self._finish_network_operation(
                NETWORK_ERRORS.get(value, "Network operation failed"),
                eventtime, value)

    def _finish_network_operation(self, error, eventtime, reason=None):
        operation = self.network_operation
        if operation is None:
            return
        self.network_operation = None
        self.network_deadline = 0.0
        self.network_probe_pending = False
        self.network_cancel_pending = False
        return_page = self.network_return_page

        if error is not None:
            if (reason == "WRONG_KEY"
                    and operation in ("wifi", "wifi-saved")
                    and self.selected_network is not None
                    and self.selected_network.get("saved")):
                self._show_message(error, return_page, (
                    ("message.ok", "CANCEL", "enabled"),
                    ("net.reset.saved", "RESET PASSWORD", "warning"),
                ))
                return

            self._show_message(error, return_page)
            return
        if operation == "scan":
            self.networks.sort(key=lambda entry: -entry["signal"])
            self.network_page = 0
            self._show_page(ScreenPage.WIFI_SCAN)
            return

        self._show_page(ScreenPage.NETWORK_HOME)
        if self.network_status.get("state") == "CONNECTED":
            self._toast("Network connected")

    def _cancel_network_operation(self):
        """Explicit product cancellation; transport loss never calls this."""
        if self.network_cancel_pending:
            return True
        if not self._network_available():
            self.network_operation = None
            self.network_deadline = 0.0
            self.network_probe_pending = False
            self.network_cancel_pending = False
            return False
        if not self.network_client.cancel():
            return False
        self.network_cancel_pending = True
        return True

    @staticmethod
    def parse_network_status(text):
        return protocol.parse_status(text)

    def _render_network_progress(self):
        commands = self.renderer.begin_page("Network")
        if self.network_cancel_pending:
            label = NETWORK_PHASES["CANCELLING"]
        elif self.network_operation == "scan":
            label = "Scanning Wi-Fi..."
        else:
            label = NETWORK_PHASES.get(
                self.network_status.get("progress", ""), "Connecting...")
        commands.append(self.renderer.text(
            400, 230, label, ThemeColor.PRIMARY, "Roboto Bold 16pt",
            "center", "middle"))
        attempt = self.network_status.get("attempt", "")
        attempt_parts = attempt.split("/", 1)
        if (len(attempt_parts) == 2
                and all(part.isdigit() for part in attempt_parts)):
            commands.append(self.renderer.text(
                400, 280, "ATTEMPT %s OF %s" % tuple(attempt_parts),
                ThemeColor.DIM, "JetBrainsMono 8pt", "center", "middle"))

        if self.network_operation is None:
            commands += self.renderer.button(
                "net.keep", 80, 340, 290, 70, "KEEP WAITING")
            commands += self.renderer.button(
                "net.cancel", 430, 340, 290, 70, "CANCEL",
                state="disabled" if self.network_cancel_pending else "danger")
        else:
            commands += self.renderer.button(
                "net.cancel", 270, 340, 260, 70,
                "CANCELLING..." if self.network_cancel_pending else "CANCEL",
                state="disabled" if self.network_cancel_pending else "danger")
        self.renderer.send(commands)

    def _render_wifi_scan(self):
        pagination = Pagination(
            self.networks, self.network_page, NETWORK_ROWS)
        self.network_page = pagination.page
        commands = self.renderer.begin_page("Select Wi-Fi", back=True)
        rows = pagination.visible
        for index, network in enumerate(rows):
            y = 62 + index * 65
            frequency = network.get("frequency", 0)
            band = ("5 GHz" if frequency >= 5000 else
                    "2.4 GHz" if frequency >= 2400 else "Wi-Fi")
            commands += self.renderer.button(
                "net.item%d" % index, 30, y, 740, 56, "")
            side_y = y + (18 if network.get("saved") else 28)
            commands.append(self.renderer.text(
                55, side_y, band, ThemeColor.DIM, "JetBrainsMono 8pt",
                "left", "middle", max_width=145, truncate=True))
            if network.get("saved"):
                commands.append(self.renderer.text(
                    55, y + 39, "SAVED", ThemeColor.DIM,
                    "JetBrainsMono 7pt", "left", "middle",
                    max_width=145, truncate=True))
            commands.append(self.renderer.text(
                400, y + 28, network["ssid"], ThemeColor.TEXT,
                "JetBrainsMono 12pt", "center", "middle",
                max_width=420, truncate=True))
            commands.append(self.renderer.text(
                745, y + 28, "%d dBm" % network["signal"],
                ThemeColor.DIM, "JetBrainsMono 8pt", "right", "middle",
                max_width=135, truncate=True))
        commands += self.renderer.button(
            "net.prev", 25, 390, 190, 50, "< PAGE",
            active=pagination.has_previous)
        commands += self.renderer.button(
            "net.rescan", 305, 390, 190, 50, "RESCAN")
        commands += self.renderer.button(
            "net.next", 585, 390, 190, 50, "PAGE >",
            active=pagination.has_next)
        if not rows:
            commands.append(self.renderer.text(
                400, 230, "No supported networks", ThemeColor.DIM,
                "Roboto 16pt", "center", "middle"))
        self.renderer.send(commands)

    def _render_keyboard(self):
        ssid = self.selected_network["ssid"]
        commands = self.renderer.begin_page(ssid, back=True)
        commands += [
            self.renderer.text(
                25, 73, "WI-FI PASSWORD", ThemeColor.PRIMARY,
                "JetBrainsMono Bold 12pt"),
            self.renderer.text(
                25, 98, "8-63 ASCII CHARACTERS OR 64 HEX DIGITS",
                ThemeColor.TEXT, "JetBrainsMono 8pt", max_width=740,
                truncate=True),
            self.renderer.fill(25, 120, 750, 53, ThemeColor.PANEL),
            self.renderer.stroke(25, 120, 750, 53, ThemeColor.PRIMARY, 2),
        ]
        commands += TEXT_KEYBOARD.render_value(
            self.renderer, self.password, self.password_cursor,
            42, 147, 575, ThemeColor.PRIMARY,
            masked=not self.password_visible)
        commands += self.renderer.button(
            "net.password.toggle", 645, 128, 120, 37,
            "HIDE" if self.password_visible else "SHOW",
            font="JetBrainsMono 8pt")
        commands += TEXT_KEYBOARD.render(
            self.renderer, self.keyboard_symbols, self.keyboard_shift)
        valid = self._valid_password(self.password)
        commands += self.renderer.button(
            "net.connect", 25, 383, 750, 54, "CONNECT", active=valid,
            font="JetBrainsMono Bold 8pt")
        self.renderer.send(commands)

    @staticmethod
    def _valid_password(password):
        if len(password) == 64:
            return all(ch in "0123456789abcdefABCDEF" for ch in password)
        return (8 <= len(password) <= 63
                and all(32 <= ord(ch) <= 126 for ch in password))

    def _connect_wifi(self):
        if not self._valid_password(self.password):
            raise RuntimeError(
                "Password must be 8-63 ASCII characters or 64 hex digits")
        self._require_network_ready()
        password = self.password
        self.password = ""
        self.password_cursor = 0
        if not self.network_client.connect_wifi(
                self.selected_network["ssid"], password):
            raise RuntimeError("Network service is unavailable")
        self._begin_network_operation("wifi", ScreenPage.WIFI_SCAN)

    def _connect_saved_wifi(self):
        self._require_network_ready()
        if not self.network_client.connect_wifi(
                self.selected_network["ssid"]):
            raise RuntimeError("Network service is unavailable")
        self._begin_network_operation("wifi-saved", ScreenPage.WIFI_SCAN)
