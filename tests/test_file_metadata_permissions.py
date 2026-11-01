"""Metadata updates must retain the access fields used by Fluidd's file menu."""
import asyncio
import pathlib
import tempfile
import unittest
from unittest import mock

# Reuse the host-compatible Moonraker import setup (no filesystem watches).
from test_gcode_3mf_upload import fm, ServerError


class FileMetadataPermissionsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.filename = 'folder/current job.gcode'
        self.path = self.root / self.filename
        self.path.parent.mkdir()
        self.path.write_text('G1 X1\n')
        self.manager = fm.FileManager.__new__(fm.FileManager)
        self.manager.server = mock.Mock(error=ServerError)
        self.manager.file_paths = {'gcodes': str(self.root)}
        self.manager.full_access_roots = {'gcodes'}
        self.manager.reserved_paths = {}
        self.metadata = {'size': 6, 'modified': 123, 'estimated_time': 300}
        self.manager.gcode_metadata = mock.Mock()
        self.manager.gcode_metadata.get.return_value = self.metadata
        self.request = mock.Mock()
        self.request.get_str.return_value = self.filename

    async def test_metadata_response_keeps_file_menu_usable(self):
        result = await self.manager._handle_metadata_request(self.request)
        # Fluidd replaces its directory entry with this response, then uses
        # permissions.includes('r') when rendering the context menu.
        self.assertEqual(result['filename'], self.filename)
        self.assertIn('r', result['permissions'])
        self.assertEqual(result['permissions'], 'rw')
        self.assertEqual(result['estimated_time'], 300)
        self.assertNotIn('permissions', self.metadata)
        self.assertNotIn('filename', self.metadata)

    async def test_readonly_reserved_and_symlink_permissions_match_directory(self):
        for access in ('readonly', 'reserved', 'symlink'):
            with self.subTest(access=access):
                self.manager.full_access_roots = {'gcodes'}
                self.manager.reserved_paths = {}
                if access == 'readonly':
                    self.manager.full_access_roots = set()
                elif access == 'reserved':
                    self.manager.reserved_paths = {'private': (self.path.resolve(), False)}
                else:
                    target = self.root / 'target.gcode'
                    target.write_text('G1 X1\n')
                    self.path.unlink()
                    self.path.symlink_to(target)
                result = await self.manager._handle_metadata_request(self.request)
                expected = self.manager.get_path_info(self.path, 'gcodes')['permissions']
                self.assertEqual(result['permissions'], expected)
                self.assertNotEqual(result['permissions'], 'rw')

    async def test_cached_metadata_for_missing_file_has_no_permissions(self):
        self.path.unlink()
        result = await self.manager._handle_metadata_request(self.request)
        self.assertEqual(result['permissions'], '')

    async def test_missing_metadata_still_returns_404(self):
        self.manager.gcode_metadata.get.return_value = None
        with self.assertRaises(ServerError) as raised:
            await self.manager._handle_metadata_request(self.request)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_rescan_response_also_keeps_file_permissions(self):
        self.manager.sync_lock = asyncio.Lock()
        ready = asyncio.Event()
        ready.set()
        self.manager.gcode_metadata.parse_metadata.return_value = ready
        self.manager.gcode_metadata.remove_file_metadata.return_value = None
        result = await self.manager._handle_metascan_request(self.request)
        self.assertEqual(result['permissions'], 'rw')
        self.assertEqual(result['filename'], self.filename)
        self.assertNotIn('permissions', self.metadata)


if __name__ == '__main__':
    unittest.main()
