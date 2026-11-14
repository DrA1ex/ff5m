## On-printer memory attribution: smaps grouping, the report and its UI test command.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import gc
import importlib
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / ".py/klipper/plugins"))

from feather_ui_test import memory
from feather_ui_test.feature import UITestFeature
from ff5m_ui.print_state import PrintState
from feather.features.manager import LazyFeatureManager
import feather_screen

SMAPS = """\
00010000-00400000 r-xp 00000000 b3:02 100 /opt/Python-3.7.11/bin/python3.7
Size:               3648 kB
Rss:                1200 kB
Anonymous:             0 kB
VmFlags: rd ex mr mw me dw
00400000-00800000 rw-p 00000000 00:00 0                                  [heap]
Size:               4096 kB
Rss:                3000 kB
Anonymous:          3000 kB
VmFlags: rd wr mr mw me ac
b6100000-b6200000 rw-p 00000000 00:00 0
Size:               1024 kB
Rss:                 900 kB
Anonymous:           900 kB
b6200000-b6240000 rw-p 00000000 00:00 0
Size:                256 kB
Rss:                 256 kB
Anonymous:           256 kB
b6300000-b6400000 rw-p 00000000 00:00 0
Size:               1024 kB
Rss:                  40 kB
Anonymous:            40 kB
bef00000-bf000000 rw-p 00000000 00:00 0                                  [stack]
Size:                136 kB
Rss:                  20 kB
Anonymous:            20 kB
bf100000-bf101000 r-xp 00000000 00:00 0                                  [vectors]
Size:                  4 kB
Rss:                   4 kB
Anonymous:             0 kB
"""

STATUS = "Name:\tklippy\nVmHWM:\t   30000 kB\nVmRSS:\t   28000 kB\nRssAnon:\t   20000 kB\nRssFile:\t    8000 kB\nVmSwap:\t       0 kB\n"


def command(values=None):
    responses = []
    return SimpleNamespace(get=(values or {}).get, get_int=lambda name, default=None: int((values or {}).get(name, default)),
                           error=RuntimeError, respond_info=responses.append, responses=responses)


class SmapsGroupsTest(unittest.TestCase):
    def test_mappings_are_grouped_by_what_they_are(self):
        groups = memory.summarize_smaps(SMAPS)
        self.assertEqual(groups["heap"], {"size": 4096, "rss": 3000, "anonymous": 3000})
        self.assertEqual(groups["file"], {"size": 3648, "rss": 1200, "anonymous": 0})
        self.assertEqual(groups["stack"], {"size": 136, "rss": 20, "anonymous": 20})
        self.assertEqual(groups["special"], {"size": 4, "rss": 4, "anonymous": 0})
        anonymous = groups["anonymous"]
        self.assertEqual((anonymous["size"], anonymous["rss"], anonymous["anonymous"], anonymous["count"]),
                         (2304, 1196, 1196, 3))

    def test_the_largest_unnamed_mappings_come_first_so_arenas_can_be_told_apart(self):
        largest = memory.summarize_smaps(SMAPS)["anonymous"]["largest"]
        self.assertEqual([mapping["rss"] for mapping in largest], [900, 256, 40])
        self.assertEqual(largest[0]["size"], 1024)

    def test_an_empty_listing_has_empty_groups(self):
        groups = memory.summarize_smaps("")
        self.assertEqual(groups["anonymous"]["count"], 0)
        self.assertEqual(groups["heap"]["rss"], 0)


class ObjectCountsTest(unittest.TestCase):
    def test_objects_are_counted_by_type_with_the_most_numerous_first(self):
        class Node:
            pass

        counts = memory.count_types([Node() for _ in range(3)] + [{}, {}, set()] + [[]] * 5)
        self.assertEqual(counts["total"], 11)
        self.assertEqual(list(counts["by_type"].items())[:2], [("list", 5), ("Node", 3)])


class Engine:
    """glibc's answers, which the host cannot give: a trim returns what was free but held."""

    def __init__(self, state):
        from ui.engine import feather_engine
        self.NodeBase, self.census, self.state, self.trims = feather_engine.NodeBase, feather_engine.census, state, 0

    def malloc_state(self, trim=False):
        if self.state is None:
            return None
        if trim:
            self.trims += 1
            return dict(self.state, free_held=0, trimmed=True)
        return dict(self.state)


class ReportTest(unittest.TestCase):
    def read(self, path):
        return {"/proc/self/status": STATUS, "/proc/self/smaps": SMAPS}[path]

    def test_a_c_library_without_mallinfo_still_gives_process_and_python_state(self):
        result = memory.report(True, self.read, Engine(None))
        self.assertIsNone(result["before"]["malloc"])
        self.assertNotIn("after_trim", result)
        self.assertEqual(result["before"]["process"]["RssAnon"], 20000)
        self.assertEqual(result["before"]["smaps"]["heap"]["rss"], 3000)
        self.assertGreater(result["python"]["empty_dict"], 0)

    def test_unreachable_cycles_are_collected_before_counting(self):
        engine = Engine(None)

        class Cycle(engine.NodeBase):
            _layout_defaults = (None, None, 1, None, None, "stretch", "stretch", 0, 0, False)

        gc.collect()
        gc.disable()
        try:
            nodes = engine.census()["nodes"]
            for _ in range(3000):
                item = Cycle()
                item.loop = item
            del item
            result = memory.report(False, self.read, engine)
        finally:
            gc.enable()
        self.assertNotIn("Cycle", result["python"]["objects"]["by_type"])
        self.assertGreaterEqual(result["python"]["collected"], 3000)
        # The collector's own figures are read before that collect: the cycles are still pending in generation 0.
        self.assertGreaterEqual(result["python"]["gc"]["count"][0], 3000)
        self.assertEqual(len(result["python"]["gc"]["stats"]), 3)
        self.assertEqual(result["census"]["nodes"], nodes)

    def test_the_report_works_without_ctypes_as_on_the_printer(self):
        with mock.patch.dict(sys.modules, {"ctypes": None, "_ctypes": None}):
            importlib.reload(memory)
            try:
                result = memory.report(True, self.read)
            finally:
                importlib.reload(memory)
        self.assertEqual(result["before"]["process"]["RssAnon"], 20000)

    def test_the_trim_is_taken_only_when_asked(self):
        engine = Engine({"in_use": 1, "free_held": 2, "mapped": 3, "arena": 4, "trimmed": False})
        kept = memory.report(False, self.read, engine)
        trimmed = memory.report(True, self.read, engine)
        self.assertEqual(engine.trims, 1)
        self.assertNotIn("after_trim", kept)
        self.assertEqual(kept["before"]["malloc"]["free_held"], 2)
        self.assertTrue(trimmed["trimmed"])
        self.assertEqual(trimmed["after_trim"]["malloc"]["free_held"], 0)

    def test_the_engine_counts_the_nodes_that_exist_and_their_proxies(self):
        from ui import Text

        def report():
            while gc.collect():  # earlier tests' garbage may take several passes; the census counts all nodes
                pass
            return memory.report(False, self.read)

        # On the printer the engine is the module shipped in the ui package, not an installed feather_engine.
        with mock.patch.dict(sys.modules):
            sys.modules.pop("feather_engine", None)
            before = report()
            nodes = [Text("one"), Text("two"), Text("three")]
            after = report()
        self.assertEqual(after["census"]["nodes"] - before["census"]["nodes"], len(nodes))
        proxies = after["python"]["node_proxies"], before["python"]["node_proxies"]
        self.assertEqual(proxies[0]["total"] - proxies[1]["total"], len(nodes))
        self.assertEqual(proxies[0]["by_type"]["Text"] - proxies[1]["by_type"].get("Text", 0), len(nodes))

    def test_the_report_is_saved_as_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = memory.save({"time": 12.5, "census": None}, directory)
            self.assertEqual(json.loads(pathlib.Path(path).read_text()), {"time": 12.5, "census": None})


class FileAnonymousPagesTest(unittest.TestCase):
    def test_anonymous_pages_of_files_are_summed_per_file_largest_first(self):
        smaps = "".join(
            "%08x-%08x r-xp 00000000 b3:02 1 %s\nSize: 100 kB\nRss: 90 kB\nAnonymous: %d kB\n" % (
                index * 0x1000, index * 0x1000 + 0x1000, name, anon)
            for index, (name, anon) in enumerate((
                ("/opt/ui/feather_engine.abi3.so", 120), ("/usr/lib/libstdc++.so.6", 40),
                ("/opt/ui/feather_engine.abi3.so", 8), ("/usr/lib/libc.so.6", 0))))
        files = memory.summarize_smaps(smaps)["file_anonymous"]
        self.assertEqual(list(files.items()), [("/opt/ui/feather_engine.abi3.so", 128), ("/usr/lib/libstdc++.so.6", 40)])


class OwnerTest(unittest.TestCase):
    def test_modules_belong_to_feather_parts_klipper_or_python_by_name(self):
        for name, expected in (("ui.layout", memory.FEATHER_FRAMEWORK), ("ui", memory.FEATHER_FRAMEWORK),
                               ("extras.feather_screen", memory.FEATHER_PRODUCT),
                               ("ff5m_ui.home.page", memory.FEATHER_PRODUCT),
                               ("feather.features.settings", memory.FEATHER_PRODUCT),
                               ("feather_ui_test.memory", memory.FEATHER_UI_TEST),
                               ("feather_engine", memory.FEATHER_FRAMEWORK),
                               ("extras.forge_shaper", memory.KLIPPER), ("json", memory.PYTHON),
                               ("sys", memory.PYTHON)):
            self.assertEqual(memory.owner_of(name), expected, name)


class AttributionTest(unittest.TestCase):
    """The Python heap by owner: what each owner's modules and instances hold, each object counted once."""

    def setUp(self):
        import types
        from ui import Text

        self.product, self.klipper = types.ModuleType("pkg_product"), types.ModuleType("pkg_klipper")
        exec("class Holder:\n    pass\n\n"
             "def work():\n    return %r\n" % ("code constant " * 400), self.product.__dict__)
        exec("class Device:\n    pass\n", self.klipper.__dict__)
        self.product.rows = ["product row %d" % index for index in range(300)]
        self.device = self.klipper.Device()
        self.device.readings = ["klipper reading %d" % index for index in range(200)]
        self.product.rows.append(self.device)  # reached from the product, owned by Klipper
        self.node = Text("attributed")
        self.node.payload = ["node payload %d" % index for index in range(250)]
        self.product.node = self.node  # reached from the product, held by a node
        self.modules = {"pkg_product": self.product, "pkg_klipper": self.klipper}
        owners = {"pkg_product": memory.FEATHER_PRODUCT, "pkg_klipper": memory.KLIPPER}
        self.result = memory.attribution(modules=self.modules,
                                         owner=lambda name: owners.get(name, memory.PYTHON))

    @staticmethod
    def size(values):
        return sys.getsizeof(values) + sum(sys.getsizeof(value) for value in values)

    def test_an_owners_module_values_are_its_data(self):
        product = self.result[memory.FEATHER_PRODUCT]["data"][1]
        rows = sys.getsizeof(self.product.rows) + sum(sys.getsizeof(row) for row in self.product.rows[:-1])
        self.assertGreaterEqual(product, rows)
        self.assertLess(product, rows + self.size(self.device.readings))  # the device is not the product's

    def test_an_instance_is_its_classs_owners_wherever_it_is_reached_from(self):
        self.assertGreaterEqual(self.result[memory.KLIPPER]["data"][1],
                                sys.getsizeof(self.device) + self.size(self.device.readings))

    def test_what_a_node_holds_is_the_nodes(self):
        self.assertGreaterEqual(self.result["nodes"][1], self.size(self.node.payload))
        product = self.result[memory.FEATHER_PRODUCT]["data"][1]
        self.assertLess(product, sys.getsizeof(self.product.rows) * 2 + self.size(self.product.rows[:-1]))

    def test_the_engine_objects_a_node_holds_are_the_nodes(self):
        # A node's LayoutOptions is of an engine type: counted as Python's, the nodes would lose at least its size.
        proxies = [item for item in gc.get_objects() if isinstance(item, memory._engine().NodeBase)]
        layout_options = sum(sys.getsizeof(node.layout_options) for node in proxies)
        engine_as_python = lambda name: (memory.PYTHON if (name or "").split(".")[0] == "feather_engine"
                                         else memory.owner_of(name))
        nodes = memory.attribution(modules={})["nodes"][1]
        without = memory.attribution(modules={}, owner=engine_as_python)["nodes"][1]
        self.assertGreaterEqual(nodes - without, layout_options)

    def test_classes_and_functions_are_their_owners_code(self):
        code = self.result[memory.FEATHER_PRODUCT]["code"][1]
        self.assertGreaterEqual(code, sys.getsizeof("code constant " * 400) + sys.getsizeof(self.product.work))
        self.assertLess(self.result[memory.KLIPPER]["code"][1], code)

    def test_the_report_carries_the_attribution_only_when_asked(self):
        read = ReportTest.read.__get__(ReportTest())
        self.assertNotIn("attribution", memory.report(False, read))
        self.assertIn(memory.FEATHER_FRAMEWORK, memory.report(False, read, attribute=True)["attribution"])


# sys._debugmallocstats as Python 3.7 prints it, shortened: a size class table, then the totals.
PYMALLOC_STATS = """\
Small block threshold = 512, in 64 size classes.

class   size   num pools   blocks in use  avail blocks
-----   ----   ---------   -------------  ------------
    0      8           2             449            57
    6     56          30            2104            56

# times object malloc called       =          2,105,937
# arenas allocated total           =                 82
# arenas reclaimed                 =                 41
# arenas highwater mark            =                 42
# arenas allocated current         =                 41
41 arenas * 262144 bytes/arena     =         10,747,904

# bytes in allocated blocks        =         10,448,976
# bytes in available blocks        =             70,256
36 unused pools * 4096 bytes       =            147,456
Total                              =         10,913,040
   80 free PyDictObjects * 48 bytes each =              3,840
"""


class PymallocTest(unittest.TestCase):
    def test_the_totals_and_size_classes_are_read(self):
        state = memory.parse_pymalloc_stats(PYMALLOC_STATS)
        self.assertEqual(state["classes"], [[8, 2, 449, 57], [56, 30, 2104, 56]])
        totals = state["totals"]
        self.assertEqual((totals["arenas allocated current"], totals["arenas"]), (41, 10747904))
        self.assertEqual((totals["bytes in allocated blocks"], totals["bytes in available blocks"]), (10448976, 70256))
        self.assertEqual((totals["unused pools"], totals["Total"], totals["free PyDictObjects"]), (147456, 10913040, 3840))

    def test_this_interpreters_arenas_are_reported_and_stderr_is_put_back(self):
        with tempfile.TemporaryFile() as stderr:
            saved = os.dup(2)
            os.dup2(stderr.fileno(), 2)
            try:
                state = memory.pymalloc_state()
                os.write(2, b"after the statistics")
            finally:
                os.dup2(saved, 2)
                os.close(saved)
            stderr.seek(0)
            self.assertEqual(stderr.read(), b"after the statistics")
        if state is None:
            self.skipTest("this interpreter does not use pymalloc")
        self.assertGreater(state["totals"]["arenas allocated current"], 0)
        self.assertGreater(state["totals"]["bytes in allocated blocks"], 0)
        self.assertTrue(state["classes"])

    def test_the_report_carries_the_arenas(self):
        result = memory.report(False, ReportTest.read.__get__(ReportTest()))
        self.assertIn("pymalloc", result)
        self.assertEqual(result["pymalloc"] is None, memory.pymalloc_state() is None)


class PythonForFeatherTest(unittest.TestCase):
    """Python's modules that only Feather reaches are counted apart: Klipper would not hold them without Feather."""

    def setUp(self):
        import types

        names = ("pkg_ui", "pkg_klippy", "pkg_lib_only", "pkg_lib_shared", "pkg_lib_deep", "pkg_lib_deep.leaf",
                 "pkg_lib_lazy")
        self.modules = {name: types.ModuleType(name) for name in names}
        ui, klippy, only, shared = (self.modules[name] for name in names[:4])
        exec("class Table:\n    pass\n", only.__dict__)
        only.rows = bytes(200000)
        shared.rows = bytes(100000)
        ui.Table, ui.shared, ui.leaf = only.Table, shared, self.modules["pkg_lib_deep.leaf"]
        klippy.shared = shared
        owners = {"pkg_ui": memory.FEATHER_FRAMEWORK, "pkg_klippy": memory.KLIPPER}
        self.result = memory.attribution(modules=self.modules, owner=lambda name: owners.get(name, memory.PYTHON))

    def test_modules_only_feather_reaches_are_listed_with_their_packages(self):
        reach = self.result["python modules"]
        self.assertEqual(reach["feather only"], ["pkg_lib_deep", "pkg_lib_deep.leaf", "pkg_lib_only"])
        self.assertEqual(reach["unreached"], ["pkg_lib_lazy"])

    def test_their_data_is_python_for_feather_and_a_shared_modules_stays_pythons(self):
        for_feather = self.result[memory.PYTHON_FOR_FEATHER]["data"][1]
        self.assertGreaterEqual(for_feather, 200000)
        self.assertLess(for_feather, 300000)
        self.assertGreaterEqual(self.result[memory.PYTHON]["data"][1], 100000)


class FeatherModulesTest(unittest.TestCase):
    def test_only_feathers_own_modules_are_listed_in_order(self):
        loaded = {"ui", "ui.layout", "ff5m_ui.home.page", "feather.files", "feather_screen", "uiextra", "json",
                  "extras.feather_screen", "feather_ui_test.runner"}
        self.assertEqual(memory.feather_modules(loaded),
                         ["feather.files", "feather_screen", "ff5m_ui.home.page", "ui", "ui.layout"])

    def test_the_report_lists_the_modules_loaded_before_the_harness(self):
        import feather_ui_test
        result = memory.report(False, ReportTest.read.__get__(ReportTest()))
        listed = result["feather_modules"]
        self.assertLessEqual(set(listed), feather_ui_test.MODULES_BEFORE)  # nothing the harness loaded later
        self.assertEqual(listed, sorted(listed))
        self.assertTrue(all(name.split(".")[0] in ("ui", "ff5m_ui", "feather", "feather_screen") for name in listed))

    def test_the_report_also_lists_the_feather_modules_loaded_by_now(self):
        import feather_ui_test
        import ui.reflection  # noqa: F401  a Feather module that only the editor tools load
        result = memory.report(False, ReportTest.read.__get__(ReportTest()))
        now = result["feather_modules_now"]
        self.assertIn("ui.reflection", now)
        self.assertLessEqual(set(result["feather_modules"]), set(now))  # what was loaded at start stays loaded
        self.assertNotIn("feather_ui_test", {name.split(".")[0] for name in now})
        self.assertEqual(now, sorted(now))


class TracedMemoryTest(unittest.TestCase):
    """Under tracemalloc, live allocations by file and by the owner that asked for them."""

    def setUp(self):
        import tracemalloc
        import types

        self.tracing = tracemalloc.is_tracing()
        if not self.tracing:
            tracemalloc.start(16)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # A module the probe imports: its code is made by the import machinery below the probe's import line.
        with open(os.path.join(self.directory.name, "pkg_traced_imported.py"), "w") as source:
            source.write("".join("def function_%d(value):\n    return value + %d\n" % (index, index)
                                 for index in range(300)))
        # A Feather module whose own code makes a table while it loads.
        with open(os.path.join(self.directory.name, "pkg_ui_traced_loaded.py"), "w") as source:
            source.write("table = [bytes(700) for _ in range(200)]\n")
        sys.path.insert(0, self.directory.name)
        self.addCleanup(sys.path.remove, self.directory.name)
        self.addCleanup(sys.modules.pop, "pkg_traced_imported", None)
        self.addCleanup(sys.modules.pop, "pkg_ui_traced_loaded", None)
        self.path = os.path.join(tempfile.gettempdir(), "pkg_ui_traced_probe.py")
        self.module = types.ModuleType("pkg_ui_traced_probe")
        self.module.__file__ = self.path
        source = ("import json\n"
                  "rows = [bytes(1000) for _ in range(300)]\n"
                  "parsed = json.loads('[' + ','.join(['\"%06d\"' % index for index in range(4000)]) + ']')\n"
                  "import pkg_traced_imported\n"
                  "import pkg_ui_traced_loaded\n")
        exec(compile(source, self.path, "exec"), self.module.__dict__)
        owners = {"pkg_ui_traced_probe": memory.FEATHER_FRAMEWORK, "pkg_ui_traced_loaded": memory.FEATHER_FRAMEWORK}
        self.loaded_path = sys.modules["pkg_ui_traced_loaded"].__file__
        self.result = memory.traced_memory({"pkg_ui_traced_probe": self.module, "json": sys.modules["json"],
                                            "pkg_ui_traced_loaded": sys.modules["pkg_ui_traced_loaded"]},
                                           lambda name: owners.get(name, memory.PYTHON))

    def tearDown(self):
        import tracemalloc
        if not self.tracing:
            tracemalloc.stop()

    def test_allocations_are_counted_for_the_file_that_made_them(self):
        self.assertGreaterEqual(self.result["by_file"][self.path], 300 * 1000)

    def test_the_stdlib_work_a_module_asks_for_is_its_owners(self):
        # The parsed strings are made inside json; the module that called it owns them.
        strings = sum(sys.getsizeof(value) for value in self.module.parsed)
        self.assertGreaterEqual(self.result["by_owner"][memory.FEATHER_FRAMEWORK], 300 * 1000 + strings)
        self.assertLess(self.result["by_file"].get(self.path, 0), 300 * 1000 + strings)

    def test_feathers_lines_are_split_into_what_they_import_and_what_they_run(self):
        lines = dict(((kind, line), size) for kind, line, size in self.result["feather_lines"])
        self.assertGreaterEqual(lines[("run", self.path + ":2")], 300 * 1000)
        imported = sys.modules["pkg_traced_imported"]
        code = sum(sys.getsizeof(getattr(imported, "function_%d" % index).__code__) for index in range(300))
        self.assertGreaterEqual(lines[("import", self.path + ":4")], code)
        self.assertNotIn(("import", self.path + ":2"), lines)
        kinds = self.result["feather_kinds"]
        self.assertEqual(kinds["run"] + kinds["import"], self.result["by_owner"][memory.FEATHER_FRAMEWORK])

    def test_the_largest_lines_keep_the_stacks_below_them(self):
        stacks = dict(((kind, line), rows) for kind, line, rows in self.result["feather_stacks"])
        rows = stacks[("import", self.path + ":4")]
        lines = dict(((kind, line), size) for kind, line, size in self.result["feather_lines"])
        self.assertLessEqual(sum(size for size, _, _ in rows), lines[("import", self.path + ":4")])
        size, count, frames = rows[0]
        self.assertGreater(count, 0)
        self.assertEqual(frames[-1], self.path + ":4")  # stacks end at the line they explain
        self.assertTrue(any(frame.startswith("<frozen importlib") for frame in frames))
        self.assertGreaterEqual(sum(size for size, _, _ in stacks[("run", self.path + ":2")]), 300 * 1000)

    def test_what_feather_runs_is_summed_per_file_split_by_whether_a_module_was_loading(self):
        files = dict(((name, phase), size) for name, phase, size in self.result["feather_run_files"])
        self.assertGreaterEqual(files[(self.loaded_path, "load")], 200 * 700)  # the table, made while it loaded
        self.assertNotIn((self.loaded_path, "later"), files)
        self.assertGreaterEqual(files[(self.path, "later")], 300 * 1000)  # the probe ran outside any import
        self.assertNotIn((self.path, "load"), files)
        kinds = self.result["feather_kinds"]
        self.assertEqual(sum(files.values()), kinds["run"])

    def test_blocks_python_takes_from_malloc_are_counted_by_owner(self):
        large = self.result["by_owner_large"]
        self.assertGreaterEqual(large[memory.FEATHER_FRAMEWORK], 300 * 1000 + 200 * 700)  # the rows and the table
        self.assertLess(large[memory.FEATHER_FRAMEWORK], self.result["by_owner"][memory.FEATHER_FRAMEWORK])

    def test_without_tracing_there_is_nothing(self):
        import tracemalloc
        tracemalloc.stop()
        try:
            self.assertIsNone(memory.traced_memory())
            self.assertIsNone(memory.report(False, ReportTest.read.__get__(ReportTest()))["tracemalloc"])
        finally:
            tracemalloc.start(16)


class TyperProcessTest(unittest.TestCase):
    def test_the_report_has_typers_own_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            child = pathlib.Path(directory, "700")
            child.mkdir()
            (child / "stat").write_text("700 (typer) S %d 1 1\n" % os.getpid())
            (child / "status").write_text("Name:\ttyper\nVmRSS:\t    2800 kB\nRssAnon:\t     900 kB\n")
            paths = {"/proc/self/status": STATUS, "/proc/self/smaps": SMAPS}
            result = memory.report(False, lambda path: paths.get(path) or pathlib.Path(path).read_text(),
                                   proc=directory)
        self.assertEqual(result["typer"], {"VmRSS": 2800, "RssAnon": 900})


class DescriptorCountsTest(unittest.TestCase):
    """Open descriptors of Klipper and of the Typer process it started, read from a /proc tree."""

    def proc(self, directory, pid, name, parent, descriptors):
        path = pathlib.Path(directory, str(pid))
        (path / "fd").mkdir(parents=True)
        (path / "stat").write_text("%d (%s) S %d 1 1 0 -1 4194560\n" % (pid, name, parent))
        for number in range(descriptors):
            (path / "fd" / str(number)).write_text("")

    def test_klipper_and_its_own_typer_child_are_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            self.proc(directory, 4242, "klippy", 1, 3)
            self.proc(directory, 200, "typer", 1, 9)  # another Typer, not started by this Klipper
            self.proc(directory, 300, "python3", 4242, 4)  # another child
            self.proc(directory, 500, "typer", 4242, 5)
            pathlib.Path(directory, "self").mkdir()
            self.assertEqual(memory.descriptor_counts(directory, 4242), {"klipper": 3, "typer": 5})

    def test_a_typer_that_this_klipper_did_not_start_is_not_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            self.proc(directory, 4242, "klippy", 1, 3)
            self.proc(directory, 200, "typer", 1, 9)
            self.assertEqual(memory.descriptor_counts(directory, 4242), {"klipper": 3, "typer": None})

    def test_a_process_name_holding_parentheses_is_not_misread(self):
        with tempfile.TemporaryDirectory() as directory:
            self.proc(directory, 4242, "klippy", 1, 2)
            self.proc(directory, 600, "typer) S 4242 (x", 4242, 7)
            self.assertEqual(memory.descriptor_counts(directory, 4242), {"klipper": 2, "typer": None})

    def test_without_proc_both_are_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(pathlib.Path(directory, "missing"))
            self.assertEqual(memory.descriptor_counts(missing, 4242), {"klipper": None, "typer": None})


class LeakCountersTest(unittest.TestCase):
    def counters(self, **values):
        result = dict.fromkeys(memory.LEAK_COUNTERS, 10)
        result["klipper_fds"] = 30
        result.update(values)
        return result

    def test_only_counters_that_grew_are_reported(self):
        before = self.counters(images=7, typer_fds=None)
        after = self.counters(nodes=12, images=6, typer_fds=40)
        self.assertEqual(memory.leak_growth(before, after), {"nodes": [10, 12]})

    def test_klipper_descriptors_are_recorded_but_not_compared(self):
        self.assertEqual(memory.leak_growth(self.counters(), self.counters(klipper_fds=31)), {})

    def test_equal_counters_report_nothing(self):
        self.assertEqual(memory.leak_growth(self.counters(), self.counters()), {})

    def test_counters_count_kept_nodes_their_proxies_and_new_authored_defaults(self):
        from ui import Text, ThemeColor

        warm = Text("warm")  # the first plain Text of the process would add its defaults too
        before = memory.leak_counters(proc="/nonexistent")
        kept = [Text("one"), Text("two", color=ThemeColor.SUCCESS, font="Leak counters 9pt")]
        after = memory.leak_counters(proc="/nonexistent")
        self.assertEqual(after["nodes"] - before["nodes"], len(kept))
        self.assertEqual(after["node_proxies"] - before["node_proxies"], len(kept))
        self.assertEqual(after["shared_defaults"] - before["shared_defaults"], 1)
        self.assertEqual((after["klipper_fds"], after["typer_fds"]), (None, None))

    def test_unreachable_nodes_are_collected_before_counting(self):
        from ui import Text

        gc.collect()
        gc.disable()
        try:
            before = memory.leak_counters(proc="/nonexistent")
            for index in range(50):
                item = Text("cycle %d" % index)
                item.loop = item
            del item
            after = memory.leak_counters(proc="/nonexistent")
        finally:
            gc.enable()
        self.assertEqual(memory.leak_growth(before, after), {})


class CallShapesTest(unittest.TestCase):
    def test_a_page_built_from_more_or_fewer_items_adds_no_call_shape(self):
        from ff5m_ui.filament.material.page import create_page

        def profiles(count):
            return [("PLA%d" % index, 200 + index) for index in range(count)]

        for count in (1, 7):  # one row of one button, then full rows and a partial one
            create_page(profiles(count))
        before = memory.leak_counters(proc="/nonexistent")["call_shapes"]
        for count in (2, 3, 4, 5, 6, 8, 9):  # the page holds up to three rows of three
            create_page(profiles(count))
        self.assertEqual(memory.leak_counters(proc="/nonexistent")["call_shapes"], before)


class ReopenedPagesTest(unittest.TestCase):
    """Pages reopened with other data and other themes leave the UI holding no more once it is back where it was.
    What the UI holds depends on what it shows (a list keeps the items of its current page), so the same final
    state is compared."""

    def setUp(self):
        from tests.feather_typer_loop import TyperLoop
        from tests.test_feather_screen_loop import _Clock, home_controller
        from tests.test_mod_params import MOD_PARAMS
        from feather_ui_test import runner
        from feather_ui_test.scenarios import ScenarioCatalog

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        params = MOD_PARAMS.ModParamManagement.__new__(MOD_PARAMS.ModParamManagement)
        params.declaration, params.filename = str(ROOT / "mod_params.json"), str(pathlib.Path(directory.name, "v.cfg"))
        params.printer, params.gcode = SimpleNamespace(command_error=RuntimeError), SimpleNamespace(error=RuntimeError)
        params.reactor, params._variables_lock = mock.Mock(), threading.RLock()
        params._load_declaration()
        params._reload()
        self.controller = controller = home_controller()
        controller.reactor, controller.print_state, controller.params = _Clock(), PrintState.IDLE, params
        controller.feature_manager = LazyFeatureManager(controller, feather_screen.FEATURE_SPECS)
        self.typer = TyperLoop(controller.renderer)
        self.addCleanup(self.typer.close)
        self.typer.start()
        controller._render_screen()
        self.assertTrue(self.typer.drew("192.168.2.124"))
        controller.last_action_time = -100.0
        self.catalog = ScenarioCatalog(runner.UITestRun(controller))
        self.settings = controller.feature_manager.get("settings")
        self.receipts = 0

    def presented(self):
        """Waits until Typer presents everything drawn so far. The loop snapshots the screen only between write
        transactions, so a state replaced before its frame is never snapshotted: the item nodes a list keeps for
        reuse hold an image only if a frame showed them, which would make the count depend on the loop's timing."""
        from ui.render_receipts import RenderReceipt

        self.receipts += 1
        token = "presented-%d" % self.receipts
        self.assertTrue(self.controller.renderer.request_presented_receipt(token))
        events = self.typer.take(lambda events: any(
            isinstance(event, RenderReceipt) and event.token == token for event in events))
        self.assertTrue(any(isinstance(event, RenderReceipt) and event.token == token for event in events))

    def show_mod_page(self, index):
        self.settings.mod_page = index
        self.catalog._show(feather_screen.ScreenPage.MOD_SETTINGS)
        self.presented()

    def home(self):
        self.catalog._show(feather_screen.ScreenPage.IDLE_HOME)
        self.assertTrue(self.typer.drew("192.168.2.124"))

    def test_mod_pages_and_themes_reopened_add_nothing(self):
        from ui.layout import _SHARED_DEFAULTS_LIMIT

        self.show_mod_page(10 ** 6)  # past the end: the page clamps to the last one
        pages = range(self.settings.mod_page + 1)
        themes = self.controller.renderer.theme_names()
        self.assertGreater(len(pages), 2)
        self.assertGreater(len(themes), 2)

        def forward():
            # Every page and option row once: a list keeps the item nodes of each kind it has shown for reuse, so
            # the first showing of a kind (the disabled option row on the second page) adds nodes once.
            for index in pages:
                self.show_mod_page(index)
            self.catalog._render_parameter_options(disabled_page=True)
            self.presented()
            self.catalog._render_parameter_options()
            self.presented()
            self.home()

        forward()
        first = memory.leak_counters()
        for index in reversed(pages):  # each list page now shows the items of another
            self.show_mod_page(index)
        for theme in themes:
            self.controller.renderer.set_theme(theme)
            self.show_mod_page(0)
            self.home()
        self.controller.renderer.set_theme("DEFAULT")
        forward()
        second = memory.leak_counters()
        self.assertEqual(memory.leak_growth(first, second), {})
        self.assertLess(second["shared_defaults"], _SHARED_DEFAULTS_LIMIT)  # a full table would stop sharing


class MemoryCommandTest(unittest.TestCase):
    def setUp(self):
        self.host = SimpleNamespace(
            reactor=SimpleNamespace(monotonic=lambda: 100.0), print_state=PrintState.IDLE, command_depth=0,
            _safety_print_active=mock.Mock(return_value=False),
            _safety_heaters_active=mock.Mock(return_value=False),
            _safety_motion_active=mock.Mock(return_value=False),
            pause_resume=SimpleNamespace(get_status=lambda eventtime: {"is_paused": False}))
        self.feature = UITestFeature(self.host)
        self.addCleanup(self.feature.deactivate)

    def test_an_idle_printer_gets_a_saved_report(self):
        gcmd = command()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(memory, "ARTIFACT_ROOT", directory), \
                mock.patch.object(memory, "read_text", side_effect=lambda path: {
                    "/proc/self/status": STATUS, "/proc/self/smaps": SMAPS}[path]):
            self.feature.memory_report(gcmd, False)
            saved = list(pathlib.Path(directory).glob("memory-*.json"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(json.loads(saved[0].read_text())["before"]["process"]["RssAnon"], 20000)
        self.assertIn(str(saved[0]), gcmd.responses[0])

    def test_a_busy_printer_writes_nothing(self):
        self.host._safety_heaters_active.return_value = True
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(memory, "ARTIFACT_ROOT", directory):
            with self.assertRaisesRegex(RuntimeError, "idle"):
                self.feature.memory_report(command(), True)
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [])

    def test_the_command_routes_the_memory_action_with_its_trim_choice(self):
        controller = feather_screen.FeatherScreen.__new__(feather_screen.FeatherScreen)
        controller.feature_manager = LazyFeatureManager(controller, feather_screen.FEATURE_SPECS)
        calls = []
        with mock.patch.object(UITestFeature, "memory_report",
                               lambda self, gcmd, trim, attribute: calls.append((trim, attribute))):
            controller.cmd_FEATHER_UI_TEST(command({"ACTION": "MEMORY"}))
            controller.cmd_FEATHER_UI_TEST(command({"ACTION": "MEMORY", "TRIM": "0"}))
            controller.cmd_FEATHER_UI_TEST(command({"ACTION": "MEMORY", "ATTRIBUTE": "1"}))
        self.assertEqual(calls, [(True, False), (False, False), (True, True)])


if __name__ == "__main__":
    unittest.main()
