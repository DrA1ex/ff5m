## Pre-render validated scenario scenes through the isolated Designer host.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Pre-render validated scenario scenes through the isolated Designer host."""

import base64
import json
import pathlib
import pickle
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        raise SystemExit(
            "usage: designer_scenes.py DESIGNER_ROOT PROJECT_ROOT PLAN")
    designer_root, project_root, plan_path = map(pathlib.Path, argv)
    sys.path.insert(0, str(designer_root.resolve()))
    from feather_preview.host_client import ProjectHostClient

    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    client = ProjectHostClient(
        project_root.resolve(), theme="DEFAULT", width=800, height=480,
        data_root=plan_path.parent / ".designer-host-data")
    try:
        encoded = client.call("host.checkpoint")["checkpoint"]
        baseline = pickle.loads(base64.b64decode(encoded.encode("ascii")))
        for case in plan["cases"]:
            checkpoint = pickle.loads(pickle.dumps(
                baseline, protocol=pickle.HIGHEST_PROTOCOL))
            screen = case["semantic_page_id"]
            state = checkpoint["states"].get(screen)
            if state is None:
                raise ValueError("unknown scenario page: %s" % screen)
            state.update(case.get("state") or {})
            restored = base64.b64encode(pickle.dumps(
                checkpoint, protocol=pickle.HIGHEST_PROTOCOL)).decode("ascii")
            client.call("host.restore", {"checkpoint": restored})
            for action in case.get("actions") or ():
                client.call("action.dispatch", {
                    "screen": screen,
                    "action": action["wire_id"],
                    "event": action.get("event") or {},
                })
            case["scene"] = client.call("page.render", {
                "screen": screen,
                "theme": case["theme"],
                "viewport": {
                    "width": case["width"], "height": case["height"],
                },
            })
    finally:
        client.close()
    plan_path.write_text(
        json.dumps(plan, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
