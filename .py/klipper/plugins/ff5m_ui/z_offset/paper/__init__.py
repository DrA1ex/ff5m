_EXPORTS = {
    "PAGE": "PAGE", "PAGE_ID": "PAGE_ID", "PaperRef": "PaperRef",
    "render": "render", "update_gauge": "update_gauge",
}


def __getattr__(name):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    from . import page
    value = getattr(page, target)
    globals()[name] = value
    return value
