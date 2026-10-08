## Visual finding regions and offline report contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import copy
import html.parser
import unittest

from tests.test_feather_ui_vision import FakeOpenAIEndpoint, verdict, _frame
from tests.visual_checks import html_report
from tests.visual_checks import openai_compatible as vision


def region(image="primary", **coordinates):
    return dict({"image": image, "x": 0.1, "y": 0.7,
                 "width": 0.8, "height": 0.2}, **coordinates)


class FindingRegionsTest(unittest.TestCase):
    def test_http_review_preserves_localized_finding_in_response_and_reasons(self):
        response = verdict("fail", check_id="text_legibility")
        finding = next(item for item in response["checks"] if item["status"] == "fail")
        finding["regions"] = [region()]
        original = copy.deepcopy(response)
        with FakeOpenAIEndpoint(("vision-a",), {"vision-a": response}) as endpoint:
            settings = vision.VisualCheckSettings(True, endpoint.base_url, "vision-a")
            result = endpoint.evaluator(settings).evaluate(b"image", "image/png", {})
        model = result["models"][0]
        self.assertEqual(model["status"], "completed")
        self.assertEqual(model["verdict"], "fail")
        self.assertEqual(model["reasons"][0]["regions"], [region()])
        returned = next(item for item in model["response"]["checks"] if item["status"] == "fail")
        self.assertEqual(returned["regions"], [region()])
        self.assertEqual(response, original)

    def test_old_responses_and_empty_regions_keep_the_same_verdict(self):
        for status in ("pass", "fail"):
            with self.subTest(status=status):
                old = verdict(status)
                current = copy.deepcopy(old)
                for check in current["checks"]:
                    check["regions"] = []
                self.assertEqual(vision.validate_verdict(old), vision.validate_verdict(current))

    def test_bad_regions_never_discard_a_valid_failure(self):
        invalid = [None, {}, "not a rectangle", [region(width=0)],
                   [region(x=-0.1)], [region(x=True)], [region(width="0.5")],
                   [region(x=0.9)], [region(y=0.99)], [region(image="third")],
                   [region(height=float("nan"))], [region(width=float("inf"))],
                   [region(x=10 ** 1000)], [region(extra="unsupported")]]
        expected = vision.validate_verdict(verdict("fail"))
        for value in invalid:
            with self.subTest(value=str(value)[:80]):
                response = verdict("fail")
                response["checks"][0]["regions"] = value
                self.assertEqual(vision.validate_verdict(response), expected)

    def test_regions_identify_only_supplied_images_and_problem_checks(self):
        response = verdict("fail")
        response["checks"][0]["regions"] = [region("comparison"), region()]
        response["checks"][1]["regions"] = [region()]
        single = vision.validate_verdict(response)
        paired = vision.validate_verdict(response, allow_design_mismatch=True)
        self.assertEqual(single["checks"][0]["regions"], [region()])
        self.assertEqual(paired["checks"][0]["regions"], [region("comparison"), region()])
        self.assertNotIn("regions", paired["checks"][1])

    def test_duplicate_and_excess_regions_are_bounded(self):
        boxes = [region(), region(), region(x=0, width=0.5), region(x=0.2, width=0.5)]
        self.assertEqual(vision.normalize_regions(boxes), [boxes[0], boxes[2]])

    def test_printer_spacing_audit_regions_reference_the_second_image(self):
        clear = {"defect": False, "subject": "footer", "gap_relation": "clear",
                 "reason": "Visible spacing", "regions": []}
        defect = {"defect": True, "subject": "pager", "gap_relation": "near_touching",
                  "reason": "Pager touches footer", "regions": [region()]}
        with FakeOpenAIEndpoint(("vision-a",), {"vision-a": verdict()},
                                {"vision-a": [clear, defect]}) as endpoint:
            settings = vision.VisualCheckSettings(True, endpoint.base_url, "vision-a")
            result = endpoint.evaluator(settings).evaluate(
                b"designer", "image/png", {"_comparison_image": (b"printer", "image/png")})
        model = result["models"][0]
        self.assertEqual(model["verdict"], "fail")
        self.assertEqual(model["reasons"][0]["check_id"], "spacing_and_clearance")
        self.assertEqual(model["reasons"][0]["regions"], [region("comparison")])


class CropCollector(html.parser.HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.crops = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "canvas" and "issue-crop" in attrs.get("class", "").split():
            self.crops.append(attrs)


class RegionReportTest(unittest.TestCase):
    def test_problems_and_gallery_details_use_the_correct_image_and_region(self):
        frame = _frame("Move", "fail", source="parity", comparison_artifact="printer/move.bmp")
        frame["case_result"]["reasons"][0]["regions"] = [region("comparison")]
        frame["models"][0]["response"]["checks"][0]["regions"] = [region("comparison")]
        pages = html_report.render({"screenshots": [frame]})
        for name in ("report-problems.html", "report-gallery.html"):
            with self.subTest(page=name):
                crops = CropCollector(pages[name]).crops
                self.assertEqual(len(crops), 1)
                self.assertEqual(crops[0]["data-src"], "printer/move.bmp")
                self.assertEqual(crops[0]["data-region"], "0.1 0.7 0.8 0.2")

    def test_invalid_or_unavailable_regions_do_not_create_a_crop(self):
        for artifact, boxes in (("source.png", [region(width=0)]),
                                ("/etc/passwd", [region()]),
                                ("../outside.png", [region()]),
                                ("source.png", [region("comparison")])):
            with self.subTest(artifact=artifact, boxes=boxes):
                frame = _frame("Move", "fail", artifact=artifact)
                frame["case_result"]["reasons"][0]["regions"] = boxes
                pages = html_report.render({"screenshots": [frame]})
                self.assertFalse(CropCollector(pages["report-problems.html"]).crops)
                self.assertIn("Move needs review.", pages["report-problems.html"])

    def test_old_reports_and_passing_findings_have_no_crop(self):
        for status in ("pass", "fail"):
            frame = _frame("Move", status)
            if status == "pass":
                frame["models"][0]["response"]["checks"][0]["regions"] = [region()]
            pages = html_report.render({"screenshots": [frame]})
            self.assertFalse(CropCollector(pages["report-gallery.html"]).crops)
