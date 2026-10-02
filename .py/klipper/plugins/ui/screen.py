## Shared screen composition and layer lifecycle.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from .bindings import page_state_keys
from .layout import Dirty, Node, Overlay, PageTree, Rect


class ScreenLayer:
    """One independently owned visual instance, retained while suspended."""

    def __init__(self, node, retain_background=False):
        if not isinstance(node, Node):
            raise TypeError("screen layer content must be a Node")
        self.node = node
        # Opt in only for painters that restore their own foreground pixels.
        self.retain_background = bool(retain_background)
        self.suspended = False


class ScreenRoot:
    """Own output admission and compose the page and transient layers.

    Declarative updates use the existing page tree's damage and modal rules.
    A modal layer defers background-only paints. Retained foreground painters
    can open and update over existing pixels; resized panels, page replacement
    and output recovery restore the composition. Closing a layer uses current
    data without keeping a second queue of background invalidations.
    An imperative page may submit a delta through the renderer. Such a delta
    is accepted only when the content explicitly allows deltas and there are
    no active layers; otherwise it requests a tree repaint subject to the
    same modal admission rule.
    This adapter lets products migrate their page bodies independently.
    """

    def __init__(self, renderer, content, bounds, page_id, title="", chrome=True):
        self.renderer = renderer
        self.content = content
        self.bounds = bounds if isinstance(bounds, Rect) else Rect(*bounds)
        self.page_id = page_id
        self.title = title
        self.chrome = bool(chrome)
        self.layers = []
        self.visible_layers = ()
        self._visible_content = None
        self._repaint_pending = False
        self._draining_repaint = False
        self.painting = False
        self._rebuild = True
        self._tree = None
        self._values = {}
        renderer.configure_frame_owner(self)

    def open(self, layer, replace=None, paint=True, *, before=None):
        if not isinstance(layer, ScreenLayer):
            raise TypeError("screen layer must be a ScreenLayer instance")
        if replace is not None:
            self.close(replace, paint=False)
        if layer not in self.layers:
            if before is None:
                self.layers.append(layer)
            else:
                self.layers.insert(self.layers.index(before), layer)
        self._rebuild = True
        return self.paint() if paint else True

    def close(self, layer, paint=True):
        if layer not in self.layers:
            return False
        self.layers.remove(layer)
        self._rebuild = True
        if paint:
            self.paint()
        return True

    def suspend(self, layer, suspended=True, paint=True):
        if layer not in self.layers or layer.suspended == bool(suspended):
            return False
        layer.suspended = bool(suspended)
        self._rebuild = True
        if paint:
            self.paint()
        return True

    def replace_content(self, content):
        if not isinstance(content, Node):
            raise TypeError("screen content must be a Node")
        self.content = content
        self._rebuild = True

    def update(self, values=None):
        if values is not None:
            self._values.update(values)
        return self.paint()

    def invalidate(self, node=None):
        (self.content if node is None else node).invalidate(Dirty.PAINT)

    def paint(self, receipt=None, force=False):
        if self.painting or not self.renderer.render_allowed:
            self._repaint_pending = self._repaint_pending or force
            return False
        force = force or self._repaint_pending or self.renderer.needs_redraw
        self._repaint_pending = False
        self.painting = True
        try:
            result = self._paint_frame(receipt, force)
        finally:
            self.painting = False
        return self._drain_repaint() or result

    def _drain_repaint(self):
        if (not self._repaint_pending or self._draining_repaint
                or not self.renderer.render_allowed):
            return False
        # One recovery follows the current send. If even that surface cannot
        # enter the queue, retain the request for the next caller instead of
        # recursively retrying while critical output still occupies it.
        self._draining_repaint = True
        try:
            return self.paint(force=True)
        finally:
            self._draining_repaint = False

    def _paint_frame(self, receipt, force):
        layers = tuple(layer for layer in self.layers if not layer.suspended)
        same_surface = self.content is self._visible_content and layers == self.visible_layers
        restore = force or self.renderer.header_action_changed
        mode = "full" if self._rebuild or restore else "delta"
        foreground = None
        try:
            if mode == "full":
                previous_schema = () if self._tree is None else self._tree.state_schema
                self._tree = PageTree(
                    Overlay(self.content, *(layer.node for layer in layers)),
                    self.bounds, page_id=self.page_id,
                    state_schema=tuple(key for layer in self.layers
                                       for key in page_state_keys(layer.node)))
                # Suspended instances retain their values; closed instances release them.
                for key in previous_schema:
                    if key not in self._tree.state_schema:
                        self._values.pop(key, None)
            self._tree.state.update(self._values)
            for layer in layers:
                layer.node.update(self._tree.state)
            modal_index = next((index for index in reversed(range(len(layers)))
                                if layers[index].node.input_blocked), None)
            if mode == "delta" and modal_index is not None:
                if all(layer.node.is_clean for layer in layers[modal_index:]):
                    self.renderer.discard_surface()
                    return False
                if all(layer.retain_background for layer in layers[modal_index:]):
                    mode = "update_foreground"
                    foreground = tuple(layer.node for layer in layers[modal_index:])
            elif (mode == "full" and not restore
                  and self.content is self._visible_content and not self.visible_layers
                  and len(layers) == 1 and modal_index == 0 and layers[0].retain_background):
                mode = "open_foreground"
                foreground = (layers[0].node,)

            with self.renderer.compose(preserve_input=same_surface,
                                       retain_background=mode == "update_foreground") as frame:
                commands = []
                if mode in ("full", "open_foreground"):
                    if self.chrome and mode == "full":
                        commands += self.renderer.begin_page(self.title)
                    else:
                        self.renderer.invalidate_input_generation()
                if mode == "delta":
                    commands += self._tree.update(self.renderer, self._values)
                else:
                    commands += self._tree.draw(
                        self.renderer, self._values,
                        reuse_layout=mode == "update_foreground",
                        reuse_styles=mode == "update_foreground", render_from=foreground)
                if commands:
                    self.renderer.send(
                        commands, receipt=receipt,
                        kind="surface" if mode == "full" else (
                            None if mode == "delta" else "state"),
                        key="foreground" if mode == "update_foreground" else None)
            if frame["background_changed"]:
                # Unknown or changed panel bounds require restoring exposed pixels.
                return self._paint_frame(receipt, True)
            accepted = frame["accepted"]
            if accepted:
                self.visible_layers = layers
                self._visible_content = self.content
            self._rebuild = bool(commands) and not accepted
            return accepted or not commands
        except BaseException:
            self.renderer.discard_surface()
            self._rebuild = True
            raise

    def submit(self, commands, **metadata):
        """Admit a legacy page delta or repaint its surrounding composition."""
        if (self._rebuild or self.visible_layers
                or any(not layer.suspended for layer in self.layers)
                or not getattr(self.content, "accepts_deltas", False)):
            self.renderer.discard_surface()
            self.invalidate()
            return self.paint(receipt=metadata.get("receipt"))
        self.painting = True
        try:
            result = self.renderer.send(commands, **metadata)
        finally:
            self.painting = False
        return self._drain_repaint() or result
