## Lazy feature loading and lifecycle routing for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib
import logging


class FeatureLoadError(RuntimeError):
    pass


class FeatureSpec:
    """Declarative description of one lazily-created product feature."""

    def __init__(self, name, module, factory, pages=()):
        self.name = str(name)
        self.module = str(module)
        self.factory = str(factory)
        self.pages = frozenset(pages)


class LazyFeatureManager:
    """Import and retain feature instances only after an explicit request."""

    def __init__(self, host, specs):
        self.host = host
        self._specs = {}
        self._owners = {}
        self._instances = {}
        self._loading = set()
        for spec in specs:
            if spec.name in self._specs:
                raise ValueError("Duplicate Feather feature: %s" % spec.name)
            self._specs[spec.name] = spec
            for page in spec.pages:
                if page in self._owners:
                    raise ValueError("Feather page has multiple feature owners")
                self._owners[page] = spec.name

    def owner_name(self, page):
        return self._owners.get(page)

    def peek(self, name):
        return self._instances.get(name)

    def loaded(self):
        return tuple(self._instances.values())

    def get_for_page(self, page):
        name = self.owner_name(page)
        return None if name is None else self.get(name)

    def get(self, name):
        instance = self._instances.get(name)
        if instance is not None:
            return instance
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError("Unknown Feather feature: %s" % name)
        if name in self._loading:
            raise FeatureLoadError(
                "Circular Feather feature load detected: %s" % name)
        self._loading.add(name)
        try:
            module = importlib.import_module(spec.module)
            factory = getattr(module, spec.factory)
            instance = factory(self.host)
            initialize = getattr(instance, "initialize", None)
            if initialize is not None:
                initialize()
        except Exception as exc:
            logging.exception(
                "[feather_screen] unable to load feature=%s module=%s",
                name, spec.module)
            raise FeatureLoadError(
                "Unable to load %s feature: %s" % (name, exc))
        finally:
            self._loading.discard(name)
        # Publish only a completely initialized object. A failed factory can
        # therefore be retried and can never leak a half-constructed instance.
        self._instances[name] = instance
        logging.info(
            "[feather_screen] loaded feature=%s module=%s",
            name, spec.module)
        return instance

    def resolve_semantic_action(self, page, wire_id):
        feature = self.get_for_page(page)
        if feature is None:
            return None, None
        return feature, feature.resolve_semantic_action(page, wire_id)

    def update(self, eventtime):
        for feature in self.loaded():
            feature.update(eventtime)

    def notify(self, hook, *args):
        for feature in self.loaded():
            callback = getattr(feature, hook, None)
            if callback is not None:
                callback(*args)

    def handle_immediate_action(self, page, action):
        for feature in self.loaded():
            if feature.handle_immediate_action(page, action):
                return True
        return False

    def blocks_action(self, action):
        for feature in self.loaded():
            callback = getattr(feature, "blocks_action", None)
            if callback is not None and callback(action):
                return True
        return False

    def safety_active_reasons(self, eventtime):
        reasons = []
        for feature in self.loaded():
            callback = getattr(feature, "safety_active_reasons", None)
            if callback is not None:
                reasons.extend(callback(eventtime) or ())
        return tuple(reasons)

    def safety_armed_reasons(self, page, eventtime):
        name = self.owner_name(page)
        feature = self._instances.get(name)
        if feature is None:
            return ()
        callback = getattr(feature, "safety_armed_reasons", None)
        return () if callback is None else tuple(
            callback(page, eventtime) or ())

    @property
    def input_blocked(self):
        return any(bool(getattr(feature, "input_blocked", False))
                   for feature in self.loaded())

    @property
    def theme_update_blocked(self):
        return any(bool(getattr(feature, "theme_update_blocked", False))
                   for feature in self.loaded())

    def deactivate(self):
        for feature in self.loaded():
            try:
                feature.deactivate()
            except Exception:
                logging.exception(
                    "[feather_screen] unable to deactivate feature=%s",
                    getattr(feature, "name", type(feature).__name__))


class FeatureHostProxy:
    """Expose controller services while keeping feature state local.

    Reads not implemented by a feature are delegated to the controller. The
    small set of controller fields that features may update is declared as
    explicit properties instead of being hidden in ``__setattr__`` routing.
    """

    def __init__(self, host):
        self._host = host

    @property
    def feature_manager(self):
        return self._host.feature_manager

    @property
    def page(self):
        return self._host.page

    @page.setter
    def page(self, value):
        self._host.page = value

    @property
    def previous_page(self):
        return self._host.previous_page

    @previous_page.setter
    def previous_page(self, value):
        self._host.previous_page = value

    def __getattr__(self, name):
        return getattr(self._host, name)

    def allows_action(self, page, action):
        return False

    def handle_action(self, page, action):
        return False

    def resolve_semantic_action(self, page, wire_id):
        return None

    def handle_semantic_action(self, page, action):
        return False

    def back(self, page):
        return False

    def update(self, eventtime):
        pass

    def on_gcode_output(self, message):
        pass

    def on_print_state_changed(self, old_state, new_state, stats_state):
        pass

    def handle_immediate_action(self, page, action):
        return False

    def safety_active_reasons(self, eventtime):
        return ()

    def safety_armed_reasons(self, page, eventtime):
        return ()

    def deactivate(self):
        pass

    @property
    def input_blocked(self):
        return False

    @property
    def theme_update_blocked(self):
        return False
