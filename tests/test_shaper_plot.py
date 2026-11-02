"""Geometric stroke contracts independent of the display rasterizer."""
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
import os
import pathlib
import shlex
import sys
import unittest
import struct
import subprocess
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / '.py/klipper/plugins'))
from ff5m_ui.shaper.plot import _stroke_commands, plot_commands
from ui import Rect, ThemeColor, FeatherRenderer
from ui.renderer import BinaryCommand
from feather.previews import decode_fxi1
from ff5m_ui.shaper import plot as plotting


def raster_pixels(commands):
    pixels = {}
    for command in commands:
        if not hasattr(command,'payload'): continue
        tokens=shlex.split(command);i=tokens.index('-p')+1
        x,y=map(int,tokens[i:i+2]);data=decode_fxi1(command.payload)
        for index in range(data['width']*data['height']):
            if data['packed'][index//8] & (0x80 >> (index%8)):
                py,px=divmod(index,data['width'])
                pixels[x+px,y+py]=data['palette'][1]
    return pixels


class Palette:
    def image(self, x, y, blob):
        return BinaryCommand('--batch image -p %d %d --format fxi1' % (x,y),blob)

    def color(self, token):
        return '000000' if token in (ThemeColor.BACKGROUND, ThemeColor.BORDER) else 'ffffff'


class StrokeTests(unittest.TestCase):
    def raster(self, xs, ys):
        commands = _stroke_commands(Palette(), Rect(0, 0, 64, 64),
                                    [(xs, ys, ThemeColor.PRIMARY)], 64., 64., 2)
        pixels = {}
        for command in commands:
            tokens = shlex.split(command)
            i = tokens.index('-p') + 1
            x, y = map(int, tokens[i:i+2])
            data = decode_fxi1(command.payload)
            self.assertLess(len(command.payload)+len(command)+32,8192)
            for index in range(data['width']*data['height']):
                if not (data['packed'][index//8] & (0x80 >> (index%8))):
                    continue
                py,px = divmod(index,data['width'])
                px,py = px+x,py+y
                self.assertTrue(0 <= px <= 64 and 0 <= py <= 64)
                pixels[px, py] = (data['palette'][1] & 255) / 255.
        return pixels

    def test_width_normal_to_diagonal_matches_horizontal_and_has_edge_coverage(self):
        horizontal = self.raster([10., 50.], [32., 32.])
        diagonal = self.raster([10., 50.], [10., 50.])
        for pixels, length in ((horizontal, 40.), (diagonal, math.sqrt(2.)*40.)):
            # Round caps add pi*r^2; the length term must retain a 2px width.
            self.assertAlmostEqual((sum(pixels.values())-math.pi)/length, 2., delta=.12)
            self.assertTrue(any(0. < a < 1. for a in pixels.values()))

    def test_shared_vertex_does_not_change_coverage_or_darken_a_straight_line(self):
        self.assertEqual(self.raster([10., 50.], [10., 50.]),
                         self.raster([10., 30., 50.], [10., 30., 50.]))

    def test_clipping_and_repeated_points_remain_bounded(self):
        pixels = self.raster([-10., 32., 32., 80.], [-10., 32., 32., 80.])
        self.assertTrue(pixels)

    def test_maximum_size_jagged_spectra_fit_transport_without_disappearing(self):
        xs = [i*200./511 for i in range(512)]
        first = [float(i%2) for i in range(512)]
        second = [float((i//2)%2)*.75 for i in range(512)]
        renderer = FeatherRenderer()
        commands = plot_commands(renderer, Rect(68,146,700,210),
            [(xs,first,ThemeColor.PRIMARY),(xs,second,ThemeColor.WARNING)], line_width=2)
        self.assertTrue(any(hasattr(c,'payload') for c in commands))
        self.assertLess(renderer._serialized_size(commands), 50*1024)
        self.assertTrue(renderer.send(commands))
        self.assertLessEqual(max(map(len,renderer._encode_frames(commands))),8192)


class NativeStrokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = os.environ.get('FORGE_SHAPER_HELPER')
        if not cls.helper:
            raise unittest.SkipTest('Set FORGE_SHAPER_HELPER to the host native executable')

    def test_native_renderer_matches_portable_pixels_with_joins_and_overlaps(self):
        renderer=FeatherRenderer();bounds=Rect(8,12,100,80)
        series=[([0.,25.,50.,75.,100.],[0.,.3,.8,.5,.2],ThemeColor.DIM),
                ([0.,20.,40.,80.,100.],[0.,.2,.9,.4,.1],ThemeColor.PRIMARY),
                ([0.,20.,40.,80.,100.],[0.,.1,.4,.2,.08],ThemeColor.WARNING)]
        with mock.patch.object(plotting,'_native_strokes',return_value=None):
            portable=plot_commands(renderer,bounds,series,x_max=100.,line_width=2)
        with mock.patch.dict(os.environ,{'FORGE_SHAPER_RENDERER':self.helper}):
            with mock.patch.object(plotting, '_stroke_commands', side_effect=AssertionError('Native renderer fell back')):
                native=plot_commands(renderer,bounds,series,x_max=100.,line_width=2)
        self.assertEqual(raster_pixels(native),raster_pixels(portable))

    def test_native_renderer_rejects_unbounded_and_truncated_requests(self):
        for data in (b'bad',struct.pack('<8s4H2I',b'FXPLOT1\n',65535,65535,2,2,0,0),
                     struct.pack('<8s4H2I',b'FXPLOT1\n',100,100,2,4,0,0)):
            result=subprocess.run([self.helper,'render'],input=data,capture_output=True,timeout=2)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(result.stdout,b'')
