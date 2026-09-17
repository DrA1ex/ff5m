## Optional source provenance provider contract.
##
## The product runtime owns only these lightweight hooks and neutral metadata.
## External tooling may install a provider while importing a project. The
## framework never imports a parser, a Designer package, or project-specific
## preview code.

_LOCAL = None
_PROVIDERS = {}


def _local():
    global _LOCAL
    if _LOCAL is None:
        import threading
        _LOCAL = threading.local()
    return _LOCAL


def _token_for(provider):
    name = getattr(provider, "__name__", None)
    if name:
        return "module:%s" % name
    return "provider:%x" % id(provider)


def _provider():
    if _LOCAL is None:
        return None
    token = getattr(_LOCAL, "provider_token", None)
    return _PROVIDERS.get(token)


class _SourceCapture:
    __slots__ = ("provider", "token", "previous", "local")

    def __init__(self, provider):
        if provider is None:
            raise TypeError("source provenance provider is required")
        self.provider = provider
        self.token = _token_for(provider)
        self.previous = None
        self.local = None

    def __enter__(self):
        _PROVIDERS[self.token] = self.provider
        self.local = _local()
        self.previous = getattr(self.local, "provider_token", None)
        self.local.provider_token = self.token

    def __exit__(self, exc_type, exc_value, traceback):
        self.local.provider_token = self.previous
        return False


def source_capture(provider):
    """Install an external provenance provider for one import operation."""
    return _SourceCapture(provider)



def capture_enabled():
    return _provider() is not None


def _node_provider(value):
    trace = getattr(value, "_source", None)
    if not isinstance(trace, dict):
        return None
    return _PROVIDERS.get(trace.get("_provider_token"))


def capture_construction(instance, names=()):
    """Delegate construction tracing to the active external provider."""
    provider = _provider()
    if provider is None:
        return None
    metadata = provider.capture_construction(instance, names=names)
    if metadata is None:
        return None
    metadata = dict(metadata)
    metadata["_provider_token"] = _token_for(provider)
    return metadata


def capture_binding(binding, names=()):
    """Delegate binding tracing to the active external provider."""
    provider = _provider()
    if provider is None:
        return None
    hook = getattr(provider, "capture_binding", None)
    if hook is None:
        return None
    metadata = hook(binding, names=names)
    if metadata is None:
        return None
    metadata = dict(metadata)
    metadata["_provider_token"] = _token_for(provider)
    return metadata


def capture_modifier(node, method, properties):
    """Delegate fluent layout tracing without importing source tooling."""
    provider = _provider() or _node_provider(node)
    if provider is None:
        return
    provider.capture_modifier(node, method, properties)


def construction_metadata(value):
    provider = _node_provider(value)
    if provider is None:
        trace = getattr(value, "_source", None)
        if trace is None:
            return None
        return {
            "callable": trace.get("callable"),
            "class": trace.get("class"),
            "anchor": trace.get("anchor"),
            "chain": list(trace.get("chain", ())),
        }
    return provider.construction_metadata(value)


def _unavailable(reason="Source capture is unavailable"):
    return {
        "status": "read_only",
        "rewrite": None,
        "reason": reason,
        "origin": None,
        "scopes": [],
        "transformations": [],
    }


def property_provenance(node, property_name, spec=None, value=None):
    instance = getattr(node, "_component_template_instance", None)
    if instance and instance.get("root"):
        parameter = component_property_parameter(node, property_name)
        if parameter is None:
            return _unavailable("Property belongs to the component definition and is not an exposed parameter")
        return component_parameter_provenance(node, parameter.name, value)
    provider = _node_provider(node)
    if provider is None:
        return _unavailable()
    return provider.property_provenance(
        node, property_name, spec=spec, value=value)


def component_property_parameter(node, property_name):
    """Map only explicit root ParamRef fields; never infer constructor aliases."""
    instance = getattr(node, "_component_template_instance", None)
    template = getattr(node, "_component_template", None)
    if template is None or not instance or not instance.get("root"):
        return None
    from .layout import ParamRef
    spec = next((value for value in template.root.property_schema
                 if value.name == property_name), None)
    value = None if spec is None else spec.value_from(template.root)
    if isinstance(value, ParamRef):
        return next((parameter for parameter in template.parameters
                     if parameter.name == value.parameter.name and parameter.field is not None), None)
    return None


def style_provenance(node):
    """Return source-edit provenance for one ``Node.style(...)`` modifier."""
    provider = _node_provider(node)
    if provider is None:
        return _unavailable("Style assignment source capture is unavailable")
    hook = getattr(provider, "style_provenance", None)
    if hook is not None:
        return hook(node, value=getattr(node, "_style_id", None))
    return _unavailable(
        "Source provider does not support style assignment editing")


def style_property_provenance(style, property_name, value=None):
    """Return source-edit provenance for one Style keyword/property."""
    provider = _node_provider(style)
    if provider is None:
        return _unavailable("Style property source capture is unavailable")
    hook = getattr(provider, "style_property_provenance", None)
    if hook is None:
        return _unavailable(
            "Source provider does not support style property editing")
    return hook(style, property_name, value=value)


def component_parameter_provenance(node, parameter_name, value=None):
    """Return source-edit provenance for a ComponentTemplate instance argument."""
    provider = _node_provider(node)
    if provider is None:
        return _unavailable("Component parameter source capture is unavailable")
    hook = getattr(provider, "component_parameter_provenance", None)
    if hook is None:
        return _unavailable(
            "Source provider does not support component parameter editing")
    return hook(node, parameter_name, value=value)


def structure_provenance(node):
    """Return structural source-form provenance for bounded Designer adapters."""
    provider = _node_provider(node)
    if provider is None:
        return _unavailable("Structural source capture is unavailable")
    hook = getattr(provider, "structure_provenance", None)
    if hook is None:
        return _unavailable(
            "Source provider does not support structural rewriting")
    return hook(node)


def layout_provenance(node):
    provider = _node_provider(node)
    if provider is None:
        return dict((name, _unavailable()) for name in (
            "width", "height", "grow", "margin", "padding",
            "horizontal", "vertical", "offset", "allow_overflow",
        ))
    return provider.layout_provenance(node)




def _scope_id(scope):
    import hashlib
    anchor = scope.get("anchor") or {}
    value = anchor.get("range") or {}
    start = value.get("start") or {}
    end = value.get("end") or {}
    payload = "|".join(str(item) for item in (
        scope.get("kind"), anchor.get("relative_file") or anchor.get("file"),
        start.get("line"), start.get("column"),
        end.get("line"), end.get("column"),
        anchor.get("fingerprint"),
    ))
    return "scope:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

def _scope_key(scope, area=None, name=None):
    anchor = scope.get("anchor") or {}
    value = anchor.get("range") or {}
    start = value.get("start") or {}
    end = value.get("end") or {}
    base = (
        anchor.get("file"), start.get("line"), start.get("column"),
        end.get("line"), end.get("column"), scope.get("kind"),
    )
    if scope.get("kind") == "symbol":
        return base
    return base + (area, name)


def annotate_affected(tree):
    """Attach runtime properties affected by each neutral source anchor."""
    groups = {}

    def collect(node):
        template = (node.get("template_instance")
                    or node.get("template_definition") or {})
        target = {
            "id": node.get("id"), "ref": node.get("ref"),
            "type": node.get("type"),
            "template": template.get("template"),
            "item_key": template.get("item_key"),
        }
        for area in ("property_sources", "layout_sources", "action_sources"):
            for name, metadata in node.get(area, {}).items():
                for scope in metadata.get("scopes", ()):
                    key = _scope_key(scope, area, name)
                    groups.setdefault(key, []).append(dict(
                        target, property=name, area=area))
        for child in node.get("children", ()):
            collect(child)

    def apply(node):
        for area in ("property_sources", "layout_sources", "action_sources"):
            for name, metadata in node.get(area, {}).items():
                for scope in metadata.get("scopes", ()):
                    scope.setdefault("id", _scope_id(scope))
                    scope["affected"] = groups.get(
                        _scope_key(scope, area, name), [])
                    scope["shared"] = len(scope["affected"]) > 1
        for child in node.get("children", ()):
            apply(child)

    collect(tree)
    apply(tree)
    return tree


def _install_layout_hooks():
    from . import layout
    layout._install_source_hooks(capture_construction, capture_modifier)


def _install_binding_hook():
    from . import bindings
    bindings._install_source_hook(capture_binding)


_install_layout_hooks()
_install_binding_hook()
