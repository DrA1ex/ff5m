_EXPORTS = {
    "PAGE": "PAGE", "PAGE_ID": "PAGE_ID", "SummaryRef": "SummaryRef",
    "render": "render",
}


def __getattr__(name):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    from . import page
    value = getattr(page, target)
    globals()[name] = value
    return value
