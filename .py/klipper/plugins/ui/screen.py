## Shared screen composition and layer lifecycle.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from contextlib import contextmanager

from .bindings import StateStore, page_state_keys, resolve
from .engine import NATIVE_METHODS, BinaryCommand, CommandBatch, ScreenFrames
from .footer import FOOTER_BOUNDS
from .header import Header, header_action_page
from .identity import serialize_key
from .layout import DeclarativePage, Dirty, LayoutResult, Node, Overlay, PageTree, Rect


class ScreenLayer:
    """One independently owned visual instance, retained while suspended: a
    node, or a declarative page whose nodes the screen draws as the content's."""

    def __init__(self, node, retain_background=False, backdrop=False):
        if not isinstance(node, (Node, DeclarativePage)):
            raise TypeError("screen layer content must be a Node or a DeclarativePage")
        self.node = node
        # Opt in only for painters that restore their own foreground pixels.
        self.retain_background = bool(retain_background)
        # A modal layer dims the screen beneath it: the engine draws the backdrop when it draws what lies beneath
        # the layer or opens it, not when it redraws the layer over its kept pixels.
        self.backdrop = bool(backdrop)
        self.suspended = False


class ImperativeSurface(Node):
    """A node an imperative painter draws: its render runs the painter and
    ignores the layout. The screen root records the painter's output when
    the frame places it."""

    paints_pixels = True
    covers_bounds = True


class _Recorded(Node):
    """An imperative surface's place in the screen's frames: the output its
    painter last recorded, which the engine draws there."""

    paints_pixels = True
    covers_bounds = True
    record_fields = ("commands",)
    draw = NATIVE_METHODS["recorded"]["draw"]

    def __init__(self):
        super().__init__()
        self.commands = None


class _Toast(ImperativeSurface):
    """The screen's toast (ScreenRoot.show_toast): the renderer's toast drawing of
    its message in its box."""

    def __init__(self, message, box):
        super().__init__()
        self.message = message
        self.box = box

    def render(self, renderer, state, layout):
        return renderer.toast_commands(self.message, self.box)


class _PageSlot(Overlay):
    """The place of a declarative page in the screen tree: its header (with a
    title) under the page's root, placed at the page's bounds.

    The page keeps its state, actions, styles and update pass; its root owns
    the page's tree flags while the screen holds it, and the screen draws the
    page's nodes with the page's state. The header (header.Header) is a tree
    of its own with the page's title, back button and header button (the
    page's header_button, if any); the screen keeps one for every page, so
    the slot takes it from the slot that showed it before.
    """

    def __init__(self, page, title, back, header):
        bounds = page.bounds
        self.header = header
        if header is not None and header.node.parent is not None:
            header.node.parent.release_header()
        self._frame = Overlay(page.root).offset(bounds.x, bounds.y).size(bounds.width, bounds.height)
        super().__init__(*self._nodes())
        self.page = page
        self.title = title
        self.back = back

    def _nodes(self):
        return (self.header.node, self._frame) if self.header is not None else (self._frame,)

    def release_header(self):
        """Another page's slot takes the header."""
        self.header.node.parent = None
        self.header = None
        self.children = self._nodes()
        self._adopt(*self.children)

    def apply_header(self, renderer):
        """Inside the screen's transaction: the header's values, and its new tree for a new header action."""
        if self.header is None or not self.header.apply(renderer):
            return
        for node in self.children:
            if node is not self._frame:
                node.parent = None
        self.children = self._nodes()
        self._adopt(*self.children)
        self.invalidate_layout()

    def drew(self, layout):
        """The screen drew the slot and published its layout."""
        self.page.screen_drew(layout)
        if self.header is not None:
            self.header.page.screen_drew(layout)


class _LayerSlot(Overlay):
    """The place of a declarative page shown as a layer: the page's root at
    the page's bounds and, when the root keeps it (a Popup's
    preserve_header_action), the renderer's header action over it, as the
    renderer's popup and dialog keep it. header is that action's page
    (header.header_action_page), which the screen keeps for its layers."""

    def __init__(self, page, header_action, header):
        bounds = page.bounds
        self.page = page
        self.header_action = header_action
        self.header = header
        if header is not None and header.root.parent is not None:
            header.root.parent.release_header()
        self._frame = Overlay(page.root).offset(bounds.x, bounds.y).size(bounds.width, bounds.height)
        super().__init__(*self._nodes())

    def _nodes(self):
        return (self._frame,) if self.header is None else (self._frame, self.header.root)

    def release_header(self):
        """Another layer's slot, made later, takes the header action's page: the
        top one shows it, since a modal layer covers the input of those below."""
        self.header.root.parent = None
        self.header = None
        self.children = self._nodes()
        self._adopt(*self.children)

    def prepare_frame(self, full):
        """Inside the screen's transaction: the pages' Python work and update pass (DeclarativePage.prepare_frame)."""
        self.page.prepare_frame(full)
        if self.header is not None:
            self.header.prepare_frame(full)

    def drew(self, layout):
        """The screen drew the slot and published its layout."""
        self.page.screen_drew(layout)
        if self.header is not None:
            self.header.screen_drew(layout)


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

    The content is a node or a declarative page. A page keeps its values in
    its own state: the product writes them there and paints, and a delta
    draws the page's changes while no layer is visible. Under a modal layer
    the changes wait for the layer to close; under other layers the
    composition repaints. update() writes the values the content node and
    the layers read.

    select_content, when given, returns (content, title, back) for the
    product's current state; each paint shows what it returns.
    write_content, when given, writes the content's values; each paint runs
    it inside its transaction, before the page's pass. A frame loop may take
    the screen's tree between transactions on its own thread, so the tree and
    its state change only there: invalidate() waits for the next paint too.

    The root draws through its ScreenFrames (_paint_screen): the engine
    composes the content and the layers by its layer rule and says what the
    next frame draws. A page's nodes are the screen tree's, the content's and
    a layer's (its _LayerSlot); every other node is recorded: the frame places the output its source last drew, which an
    imperative surface's painter draws and any other node's own tree draws
    (_record), on the reactor until such nodes are native. The renderer's
    footer is a persisting layer of that tree, between the content and the
    layers, while it shows and its strip lies in the screen; the content is
    then sized above it. The renderer's toast is the tree's top layer
    (show_toast).
    """

    def __init__(self, renderer, content, bounds, page_id, select_content=None, write_content=None):
        self.renderer = renderer
        self.content = None
        self._content_node = None
        self._content_chrome = None
        self._header = None
        self._page = None
        self.bounds = bounds if isinstance(bounds, Rect) else Rect(*bounds)
        self.page_id = page_id
        self.layers = []
        self.visible_layers = ()
        self._visible_content = None
        # A paint asked for while one ran, which may have read its values already (and whether it was forced).
        self._paint_again = False
        self._force_again = False
        self.painting = False
        # Open transaction() groups, and whether the footer changed inside them with no paint asked.
        self._grouped = 0
        self._chrome_again = False
        self._rebuild = True
        self._values = {}
        self._select_content = select_content
        self._write_content = write_content
        # Nodes invalidate() asked to repaint (None: the content), applied inside the next paint.
        self._invalidated = []
        # Values write_layer() asked for, by layer page, written inside the next paint.
        self._layer_values = {}
        # The screen's frames, their tree, each recorded node (content by
        # (node, title, back), layers by layer), the nodes the tree holds, the
        # state the recorded sources read, the trees that draw sources other
        # than imperative surfaces, and whether the next frame must draw whole
        # (the panel shows something it did not draw).
        self._screen_tree = Overlay()
        self._screen = ScreenFrames(self._screen_tree, None, self.bounds)
        self._recorded = {}
        self._shown = ()
        self._screen_state = None
        self._screen_schema = ()
        self._trees = {}
        self._screen_redraw = True
        # The renderer's header action as the last accepted frame showed it. Imperative painters (the content's
        # begin_page, a dialog that keeps the header action) draw it only in a whole frame.
        self._header_action = None
        # The renderer's dialog bounds as the last accepted frame drew them: a retained painter keeps the pixels
        # around its dialogs only while they stay.
        self._dialog_bounds = ()
        # The footer's place in the screen tree: its root at the footer's bounds.
        self._footer_slot = None
        # (header action, its page) that page layers keep over them (_LayerSlot).
        self._layer_header = None
        # That slot while the last placement (_place_footer) left it in the tree and uncovered: until the next
        # one, the footer's values alone change it (chrome_changed).
        self._footer_shown = None
        # The toast (show_toast): the tree's top layer, over the product's layers but not one of them.
        self._toast = None
        self.replace_content(content)
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

    def show_toast(self, message, box):
        """Show message in box (x, y, width, height) as the screen's toast, in place
        of the one it shows: a layer over the content and every other layer, drawn
        by the renderer's toast_commands. It is none of the product's layers
        (layers, visible_layers), so it changes no dialog's place and takes no
        input but its own."""
        if self._toast is not None and (self._toast.node.message, self._toast.node.box) == (message, box):
            return True
        self._toast = ScreenLayer(_Toast(message, box))
        self._rebuild = True
        return self.paint()

    def hide_toast(self, paint=True):
        """Remove the toast; False when none shows."""
        if self._toast is None:
            return False
        self._toast = None
        self._rebuild = True
        if paint:
            self.paint()
        return True

    def replace_content(self, content, title=None, back=False):
        """Show content: a node, or a declarative page with its title and back.

        Showing the same content with the same title and back keeps the surface.
        """
        if content is self.content and self._content_chrome == (title, bool(back)):
            return
        if isinstance(content, DeclarativePage):
            # Made inside the screen's transaction (_content): it adopts the page's root.
            self._content_node = None
            self._page = content
        elif isinstance(content, Node):
            if title is not None:
                raise ValueError("only page content has a title")
            self._content_node = content
            self._page = None
        else:
            raise TypeError("screen content must be a Node or a DeclarativePage")
        self.content = content
        self._content_chrome = (title, bool(back))
        self._rebuild = True

    def update(self, values=None):
        if values is not None:
            self._values.update(values)
        return self.paint()

    def invalidate(self, node=None):
        """Repaint node (the content when None) at the next paint, which
        applies it inside its transaction. The content drawn anew lifts a
        block of the screen's input (block_input)."""
        self._invalidated.append(node)
        if node is None:
            self._screen.block_input(False)

    def block_input(self):
        """Block the screen's input until its content is drawn anew
        (invalidate), as the renderer's block_input blocks a page's: Typer
        holds only the wake region while the frames still paint. False for
        content that takes deltas, whose page the renderer blocks itself."""
        if getattr(self.content, "accepts_deltas", False):
            return False
        self._screen.block_input(True)
        self.paint()
        return True

    def write_layer(self, layer, values):
        """Write values of a declarative page's layer and paint: the next paint writes them inside its transaction."""
        self._layer_values.setdefault(layer.node, {}).update(values)
        return self.paint()

    def _content(self):
        """The content's node; a page's slot is made on first use, inside
        the screen's transaction, since it adopts the page's root, which the
        screen's tree may still hold."""
        if self._content_node is None:
            title, back = self._content_chrome
            button = getattr(self._page, "header_button", None)
            if title is None:
                header = None
            elif self._header is None:
                header = self._header = Header(title, back, button)
            else:
                header = self._header
                header.show(title, back, button)
            self._content_node = _PageSlot(self._page, title, back, header)
        return self._content_node

    def _write_inside(self):
        """Inside the paint (the screen's transaction): the
        content's values and the repaints invalidate() asked for."""
        if self._write_content is not None:
            self._write_content()
        written, self._layer_values = self._layer_values, {}
        for page, values in written.items():
            page.state.update(values)
        invalidated, self._invalidated = self._invalidated, []
        for node in invalidated:
            node = self._content() if node is None else node
            if isinstance(node, _PageSlot):
                # The slot does not cover its bounds (begin_page leaves the footer's strip alone), and a
                # repaint would fill them: laid out again, it is drawn as a whole frame draws it.
                node.invalidate_layout()
            else:
                node.invalidate(Dirty.PAINT)

    @property
    def frames(self):
        """The screen's ScreenFrames: the renderer attaches it to its frame loop,
        which then draws every frame of it."""
        return self._screen

    def shown_input(self):
        """The input Typer holds of the screen (ScreenFrames.shown_input)."""
        return self._screen.shown_input()

    def restarted(self):
        """Typer restarted and holds nothing of the screen: this transaction,
        the framework's, draws all of it at once. A freeze ignores the product's
        writes, not this one, so a frozen safety screen is drawn again with its
        buttons."""
        self.surface_cleared()
        frozen = self.renderer.output_frozen
        if frozen:
            self.renderer.thaw_output()
        try:
            self.paint()
        finally:
            if frozen:
                self.renderer.freeze_output()

    def surface_cleared(self):
        """The panel's pixels are not the composition's (the renderer's start,
        a Typer restart, another owner's drawing): the next paint draws the whole
        composition."""
        self._rebuild = True
        self._screen_redraw = True
        self.visible_layers = ()
        self._visible_content = None

    def paint(self, receipt=None, force=False, receipt_phase="presented"):
        """One transaction of the screen: the content selected and written, the
        layers' values, the invalidations, the painters of what the frame paints,
        then its frame. A paint asked for inside one (a painter, write_content, a
        header change) runs once that one ends. While output is frozen nothing is
        drawn: the frozen screen is the composition's, so the paint after the
        thaw draws what changed."""
        if self.painting or (self._grouped and receipt is None):
            self._paint_again = True
            self._force_again = self._force_again or force
            return not self.painting
        if not self.renderer.render_allowed:
            return False
        result = self._paint_once(receipt, force, receipt_phase)
        return self._paint_asked() or result

    @contextmanager
    def transaction(self):
        """Group a reactor pass's writes into one transaction of the screen: the
        paints (and footer changes) asked for inside run once, when the outermost
        group ends, so the pass shows in one frame. A paint that asks a receipt
        runs at once, since its receipt names its frame; it draws what was
        written so far."""
        self._grouped += 1
        try:
            yield self
        finally:
            self._grouped -= 1
            if not self._grouped:
                chrome, self._chrome_again = self._chrome_again, False
                if self._paint_again:
                    self._paint_asked()  # its frame takes the footer's values too
                elif chrome:
                    self.chrome_changed()

    def _paint_once(self, receipt, force, receipt_phase):
        self.painting = True
        try:
            if self._select_content is not None:
                content, title, back = self._select_content()
                self.replace_content(content, title=title, back=back)
            return self._paint_screen(receipt, force, receipt_phase)
        finally:
            self.painting = False

    def _paint_asked(self):
        """The paint asked for while one ran, drawn now; once, since a paint that
        asks for itself each time would not end."""
        if not self._paint_again or not self.renderer.render_allowed:
            return False
        force, self._paint_again, self._force_again = self._force_again, False, False
        return self._paint_once(None, force, "presented")

    def _screen_layers(self, layers):
        """Set the screen tree's nodes for the content and layers: a page's
        slot, or a recorded node for each surface, with the footer between
        them while it shows. Returns the content's and layers' nodes with their
        sources and whether they changed."""
        content = self._content()
        keys = [(content,) + self._content_chrome] + list(layers)
        slot = content if isinstance(content, _PageSlot) else None
        nodes = (slot or self._recorded.get(keys[0]) or _Recorded(),) + tuple(
            self._layer_node(key) for key in keys[1:])
        changed = nodes != self._shown
        if changed:
            for node in self._shown:
                if node not in nodes:
                    # Off the screen its dirt (a page written while hidden) reaches no frame.
                    node.parent = None
                    if isinstance(node, (_PageSlot, _LayerSlot)) and node.page.root.parent is node._frame:
                        # Its page leaves with nothing of the screen, which a product's kept page (a module's
                        # PAGE, shown again or copied) would hold on to. A newer slot may hold the root already.
                        node.page.root.parent = None
                        node.page.screen_released()
            self._recorded = dict(zip(keys, nodes))
            self._shown = nodes
            sources = ((content,) if slot is None else ()) + tuple(
                layer.node for layer in self.layers if isinstance(layer.node, Node))
            self._trees = {source: tree for source, tree in self._trees.items() if source in sources}
            schema = tuple(dict.fromkeys(key for source in sources for key in page_state_keys(source)))
            # Suspended instances retain their values; closed instances release them.
            for key in self._screen_schema:
                if key not in schema:
                    self._values.pop(key, None)
            self._screen_schema = schema
            self._screen_state = StateStore(schema)
        self._place_footer()
        sources = [(content, nodes[0], None)]
        sources += [(layer.node, node, layer) for layer, node in zip(layers, nodes[1:])]
        return sources, changed

    def _layer_node(self, layer):
        """A layer's node in the screen tree: a page's slot, made again when
        the header action it keeps changes (new input, as a header's), else
        a recorded node."""
        node = self._recorded.get(layer)
        page = layer.node
        if not isinstance(page, DeclarativePage):
            if node is None:
                node = _Recorded()
                if isinstance(page, _Toast):
                    # At its box, so that the content's repaints beneath draw it again only where they meet it.
                    x, y, width, height = page.box
                    node.offset(x - self.bounds.x, y - self.bounds.y).size(width, height)
            return node
        keeps = resolve(getattr(page.root, "preserve_header_action", False), page.state)
        header_action = getattr(self.renderer, "_header_action", None) if keeps else None
        if isinstance(node, _LayerSlot) and node.header_action == header_action:
            return node
        header = None
        if header_action is not None:
            if self._layer_header is None or self._layer_header[0] != header_action:
                self._layer_header = (header_action, header_action_page(header_action))
            header = self._layer_header[1]
        return _LayerSlot(page, header_action, header)

    def _paint_screen(self, receipt, force, receipt_phase="presented"):
        """Draw the content and the layers through the screen's frames, in the
        mode the engine's layer rule decides (planned())."""
        layers = tuple(layer for layer in self.layers if not layer.suspended)
        same_surface = self.content is self._visible_content and layers == self.visible_layers
        screen = self._screen
        screen.begin()
        try:
            self._write_inside()
            shown = self._shown
            sources, changed = self._screen_layers(layers + ((self._toast,) if self._toast is not None else ()))
            slot = sources[0][1] if isinstance(sources[0][1], _PageSlot) else None
            if slot is not None:
                slot.apply_header(self.renderer)
            header_action = getattr(self.renderer, "_header_action", None)
            # A page's slot alone takes a new header action as its own relayout (apply_header).
            redraw = (force or self._screen_redraw or (self._rebuild and not changed)
                      or (header_action != self._header_action and (slot is None or bool(layers))))
            if self._screen_redraw:
                # The panel's pixels are gone: every layer is drawn, the footer too.
                screen.redraw()
            elif redraw:
                # The composition draws whole; the footer's pixels survive it.
                screen.draw_whole()
            self._screen_redraw = False
            page = None if slot is None else slot.page
            if page is not None:
                # A frame that draws the page anew prepares it as its draw does.
                page.prepare_frame(redraw or shown[:1] != self._shown[:1])
                # A modal node in the page (a modal Dialog) makes the content modal, as a layer's root makes its slot.
                slot._blocks_input = page.root.input_blocked
            state = self._screen_state
            state.update(self._values)
            for source, node, layer in sources:
                if isinstance(node, _LayerSlot):
                    node.prepare_frame(redraw or node not in shown)
                    node._blocks_input = source.root.input_blocked
                    node._retains_background = layer.retain_background
                    node._backdrop = layer.backdrop
                    continue
                if source is node:
                    continue  # a page's slot: its nodes are the screen's
                source.update(state)
                node._blocks_input = source.input_blocked
                node._retains_background = layer is not None and layer.retain_background
                node._backdrop = layer is not None and layer.backdrop
                if not source.is_clean:
                    node.invalidate()
                    source.clear_dirty()
            planned = screen.planned()
        except BaseException:
            self._abandon_screen()
            raise
        if planned is None:
            screen.end(self.renderer)
            self.renderer.discard_surface()
            return not any(node._blocks_input for _source, node, _layer in sources[1:])
        # A frame loop draws an attached screen: end() hands it the frame, and the loop reports the receipt.
        attached = screen.attached
        try:
            mode, foreground = self._paint_nodes(planned, changed, same_surface, sources, slot, state)
            if attached and receipt is not None:
                screen.receipt(receipt, phase=receipt_phase)
            commands = list(screen.end(self.renderer) or ())
        except BaseException:
            self._abandon_screen()
            raise
        accepted = attached or (bool(commands) and self.renderer.submit_frame(
            commands, receipt=receipt, receipt_phase=receipt_phase))
        drew = [slot] if slot is not None and mode in ("whole", "relayout") else []
        drew += [node for _source, node, _layer in sources[1:] if isinstance(node, _LayerSlot)
                 and (mode in ("whole", "relayout") or (mode == "foreground" and node in foreground))]
        for node in drew:
            # The loop arranges an attached screen on its own thread and publishes no layout here.
            node.drew(LayoutResult() if attached else screen.layout)
        if accepted or not commands:
            # A frame with nothing to draw leaves the panel as the composition shows it.
            self.renderer.keep_surface()
            self.visible_layers = layers
            self._visible_content = self.content
            self._header_action = header_action
            self._dialog_bounds = self.renderer.dialog_bounds
        else:
            self.renderer.discard_surface()
        self._rebuild = bool(commands) and not accepted
        self._screen_redraw = self._rebuild
        return accepted or not commands

    def _paint_nodes(self, planned, changed, same_surface, sources, slot, state):
        """Inside the transaction, what its frame needs from the reactor before
        it ends: for another surface a new input generation and the page's start,
        then the output of the recorded nodes it paints. Returns the frame's plan
        (mode, foreground)."""
        renderer, (mode, foreground) = self.renderer, planned
        retained = mode == "foreground" and not changed
        with renderer.painters(retain_background=retained):
            if (mode == "whole" or (mode == "foreground" and changed)) and not same_surface:
                renderer.invalidate_input_generation()
                if mode == "whole" and slot is not None and slot.page.covers_footer:
                    renderer.cover_footer()
                elif mode == "whole" and slot is not None and slot.header is not None:
                    # The page starts on the panel, as its painter's begin_page starts it.
                    renderer.show_page(slot.title, back=slot.back)
            if slot is not None and mode in ("whole", "relayout"):
                renderer.set_semantic_page(slot.page.page_id)
            elif mode != "repaint":
                # As the composition's draws named their page.
                renderer.set_semantic_page(serialize_key(self.page_id))
            recorded = False
            for source, node, _layer in sources:
                whole = mode == "whole" or (mode == "foreground" and node in foreground)
                if not whole and mode == "foreground":
                    continue  # the frame does not paint it
                if not isinstance(node, _Recorded):
                    # A page's slot: its nodes are the screen's, but those that draw in Python.
                    node.page.commit_draws(renderer, whole)
                elif whole or not node.is_clean:
                    node.commands = CommandBatch(self._record(source, state), BinaryCommand)
                    recorded = True
        if mode == "whole":
            # The painters may have shown or hidden the footer (a page begun, a state
            # screen); after the plan only a frame that draws whole takes that.
            self._place_footer()
        bounds = renderer.dialog_bounds
        if retained and recorded and (not bounds or bounds != self._dialog_bounds):
            # The retained painters drew over pixels around dialogs of unknown or other bounds: the panel is
            # drawn in full, which restores the exposed background.
            self._screen.redraw()
            return self._paint_nodes(self._screen.planned(), changed, same_surface, sources, slot, state)
        return mode, foreground

    def _record(self, source, state):
        """What a recorded node holds: the output of its imperative surface's
        painter (with what it sends meanwhile), or of any other node drawn whole
        by a tree of its own with the screen's values (Python on the reactor,
        until such nodes are native)."""
        if isinstance(source, ImperativeSurface):
            with self.renderer.collect() as sent:
                output = source.render(self.renderer, state, None)
            return list(sent) + list(output)
        tree = self._trees.get(source)
        if tree is None:
            tree = self._trees[source] = PageTree(source, self.bounds, page_id=self.page_id)
        return tree.draw(self.renderer, {key: state[key] for key in tree.state_schema})

    def _place_footer(self):
        """Inside the screen's transaction: the content and layers with the
        footer the screen holds between them (_held_footer), with its latest
        values unless a modal layer covers it, and the content sized above it."""
        slot = self._held_footer()
        self._footer_shown = slot if slot is not None and not self._footer_covered() else None
        if self._footer_shown is not None:
            self.renderer.footer_chrome.apply()
        nodes = self._shown[:1] + ((slot,) if slot is not None else ()) + self._shown[1:]
        if nodes == tuple(self._screen_tree.children):
            return
        content = self._shown[0]
        if not isinstance(content, _PageSlot):
            # A recorded node covers its bounds. A page's slot is sized by its own nodes: the header draws
            # only above the footer, but its wake region covers the screen, as begin_page's does.
            height = FOOTER_BOUNDS.y - self.bounds.y if slot is not None else None
            if content.layout_options.height != height:
                content.layout_options.height = height
        for node in self._screen_tree.children:
            if node not in nodes:
                node.parent = None
        self._screen_tree.children = nodes
        self._screen_tree._adopt(*nodes)
        self._screen_tree.invalidate_layout()

    def _held_footer(self):
        """The footer's slot the screen holds now, or None: the renderer's footer
        while it shows with values and its strip lies in the screen. Under a modal
        layer, whose scrim covers it, a footer that is not in the screen stays out
        until the layer closes."""
        footer = getattr(self.renderer, "footer_chrome", None)
        node = None if footer is None else footer.node
        bounds, strip = self.bounds, FOOTER_BOUNDS
        holds = (bounds.x <= strip.x and strip.x + strip.width <= bounds.x + bounds.width
                 and bounds.y <= strip.y and strip.y + strip.height <= bounds.y + bounds.height)
        slot = self._footer_slot
        placed = slot is not None and slot in self._screen_tree.children
        if node is None or not holds or (self._footer_covered() and not placed):
            return None
        if slot is None or slot.children[0] is not node:
            slot = self._footer_slot = Overlay(node).offset(
                strip.x - bounds.x, strip.y - bounds.y).size(strip.width, strip.height)
            slot._persists = True
        return slot

    def _footer_joins(self):
        """Whether the footer the screen holds is not in its tree yet."""
        slot = self._held_footer() if self._screen_tree is not None else None
        return slot is not None and slot not in self._screen_tree.children

    def _footer_covered(self):
        """Whether a modal layer is over the footer (as its last update found it)."""
        return any(node._blocks_input for node in self._shown[1:])

    def header_changed(self):
        """The renderer's busy label changed: True when a page's slot draws the
        header, which this paint (or the one after the paint in progress) shows;
        False for content that draws its own header."""
        if self._page is None or self._content_chrome[0] is None:
            return False
        self.paint()
        return True

    def chrome_changed(self):
        """The footer's values changed: one transaction of the screen, whose frame
        is submitted; the screen's paint when the footer joins it. Inside a paint,
        or while the screen draws nothing (before its first frame, frozen
        output), the next paint draws it; under a modal layer, the paint that
        closes it."""
        screen = self._screen
        if self._grouped:
            self._chrome_again = True
            return True
        if self.painting or not self.renderer.render_allowed:
            return False
        if self._footer_shown is None:
            # Covered, out of the screen, or about to join it (its paint draws it whole).
            return not self._footer_covered() and self._held_footer() is not None and self.paint()
        screen.begin()
        try:
            self.renderer.footer_chrome.apply()
        finally:
            # A footer that needs Python fails the frame as any node of the screen does.
            frame = screen.end(self.renderer)
        return screen.attached or (bool(frame) and self.renderer.submit_frame(frame))

    def _abandon_screen(self):
        """A failed screen frame: whatever it drew is unknown, so the next
        frame draws whole."""
        self.renderer.discard_surface()
        self._rebuild = self._screen_redraw = True
        if self._screen.open:
            try:
                self._screen.end(self.renderer)
            except RuntimeError:
                pass

    def submit(self, commands, **metadata):
        """Admit a legacy page delta or repaint its surrounding composition (also
        when the footer joins the screen or has values to draw: a delta draws
        neither; and under the toast, which a delta would draw over)."""
        if (self._rebuild or self._paint_again or self.visible_layers or self._toast is not None
                or any(not layer.suspended for layer in self.layers)
                or not getattr(self.content, "accepts_deltas", False) or self._footer_joins()
                or (self._held_footer() is not None and self.renderer.footer_chrome.pending)):
            self.renderer.discard_surface()
            self.invalidate()
            return self.paint(receipt=metadata.get("receipt"),
                              receipt_phase=metadata.get("receipt_phase", "presented"))
        self.painting = True
        try:
            result = self.renderer.send(commands, **metadata)
            if result and not self._screen.attached:
                # The delta's input is the content's until a frame paints the content (an attached
                # screen's loop notes what it writes itself).
                self._screen.note_written(CommandBatch(commands, BinaryCommand))
        finally:
            self.painting = False
        return self._paint_asked() or result
