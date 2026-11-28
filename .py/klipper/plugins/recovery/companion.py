## Infrequently changing recovery data. Each published file is immutable.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json, os, re, uuid

from .state import RecoveryParseError
from .snapshot import _number, _vector


def _prefix(checkpoint_path):
    return os.path.splitext(os.path.basename(checkpoint_path))[0] + ".objects."


def path_for(checkpoint_path, name):
    if (not isinstance(name, str) or not re.fullmatch(
            re.escape(_prefix(checkpoint_path)) + r"[0-9a-f]{32}\.json", name)):
        raise RecoveryParseError("Invalid recovery objects reference")
    return os.path.join(os.path.dirname(checkpoint_path), name)


def sync_directory(path):
    fd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(checkpoint_path, data):
    name = _prefix(checkpoint_path) + uuid.uuid4().hex + ".json"
    path = path_for(checkpoint_path, name)
    try:
        with open(path + ".tmp", "x", encoding="utf-8") as stream:
            json.dump(data, stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path + ".tmp", path)
        # The reference may become durable immediately after the next rename.
        # Publish the companion's directory entry before writing that reference.
        sync_directory(path)
    except Exception:
        try:
            os.unlink(path + ".tmp")
        except FileNotFoundError:
            pass
        raise
    return name


def read(checkpoint_path, name):
    try:
        with open(path_for(checkpoint_path, name), "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError) as error:
        raise RecoveryParseError("Unable to read recovery objects: %s" % error)
    if not isinstance(data, dict):
        raise RecoveryParseError("Invalid recovery objects")
    return data


def capture(printer, bed_mesh, previous):
    source = printer.lookup_object("exclude_object", None)
    objects = source.objects if source is not None else None
    mesh = bed_mesh.get_mesh()
    offsets = tuple(mesh.mesh_offsets) if mesh is not None else ()
    correction = printer.lookup_object("skew_correction", None)
    skew = ((correction.xy_factor, correction.xz_factor, correction.yz_factor)
            if correction is not None else None)
    # In Klipper 0.11 definitions and calibrated meshes are replaced, not
    # mutated; BED_MESH_OFFSET mutates offsets, so compare those separately.
    key = (objects, mesh, offsets, skew)
    if (previous is not None and previous[0] is objects
            and previous[1] is mesh and previous[2:] == key[2:]):
        return key, None
    data = {"mesh": {"active": mesh is not None}}
    if objects:
        data["objects"] = [
            {field: item[field] for field in ("name", "center", "polygon") if field in item}
            for item in objects]
    if skew is not None:
        data["skew"] = list(skew)
    if mesh is not None:
        # Use full precision; get_probed_matrix() rounds to six decimals.
        data["mesh"].update(points=mesh.probed_matrix,
                            params=dict(mesh.get_mesh_params()), offsets=list(offsets))
    return key, data


def build_mesh(data, factory=None):
    """Validate and construct the actual saved mesh before any motion."""
    if not isinstance(data, dict) or type(data.get("active")) is not bool:
        raise RecoveryParseError("Invalid saved bed mesh")
    if not data["active"]:
        return None
    params = data.get("params")
    if not isinstance(params, dict):
        raise RecoveryParseError("Invalid saved bed mesh parameters")
    keys = ("min_x", "max_x", "min_y", "max_y", "x_count", "y_count",
            "mesh_x_pps", "mesh_y_pps", "algo", "tension")
    if set(params) != set(keys):
        raise RecoveryParseError("Invalid saved bed mesh parameters")
    for axis in ("x", "y"):
        count = params[axis + "_count"]
        pps = params["mesh_" + axis + "_pps"]
        if (type(count) is not int or count < 2 or type(pps) is not int
                or pps < 0 or (count - 1) * pps + count > 512):
            raise RecoveryParseError("Invalid saved bed mesh dimensions")
        low = _number(params["min_" + axis], "mesh minimum")
        high = _number(params["max_" + axis], "mesh maximum")
        if high <= low:
            raise RecoveryParseError("Invalid saved bed mesh bounds")
    algo = params["algo"]
    counts = (params["x_count"], params["y_count"])
    pps = (params["mesh_x_pps"], params["mesh_y_pps"])
    if (algo not in ("lagrange", "bicubic", "direct")
            or (algo == "direct" and any(pps))
            or (algo == "lagrange" and max(counts) > 6)
            or (algo == "bicubic" and min(counts) < 4)):
        raise RecoveryParseError("Invalid saved bed mesh interpolation")
    _number(params["tension"], "mesh tension", 0.)
    points = data.get("points")
    if not isinstance(points, list) or len(points) != params["y_count"]:
        raise RecoveryParseError("Invalid saved bed mesh points")
    points = [_vector(row, "mesh row", params["x_count"]) for row in points]
    offsets = _vector(data.get("offsets"), "mesh offsets", 2)
    if factory is None:
        from ..bed_mesh import ZMesh
        factory = ZMesh
    mesh = factory(params)
    mesh.build_mesh(points)
    mesh.set_mesh_offsets(offsets)
    return mesh


def restore(bed_mesh, mesh, name):
    # No SAVE_CONFIG and no overwrite of a persisted profile.
    bed_mesh.pmgr.current_profile = name if mesh is not None else ""
    bed_mesh.set_mesh(mesh)


def prune(checkpoint_path, keep=None):
    # Never remove a companion while an old checkpoint could still survive
    # an uncommitted directory update.
    directory = os.path.dirname(checkpoint_path) or "."
    pattern = re.compile(re.escape(_prefix(checkpoint_path)) + r"[0-9a-f]{32}\.json(?:\.tmp)?")
    sync_directory(checkpoint_path)
    for name in os.listdir(directory):
        if name != keep and pattern.fullmatch(name):
            os.unlink(os.path.join(directory, name))
