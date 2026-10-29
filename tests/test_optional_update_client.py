"""Exercise UpdateManager startup without starting any real services."""
import ast
import asyncio
import __future__
import logging
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock

SOURCE = Path(__file__).resolve().parents[1] / '.root/moonraker/components/update_manager/update_manager.py'


class Config:
    def __init__(self, name, values):
        self.name = name
        self.values = values

    def get_name(self):
        return self.name

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getboolean(self, key, default=False):
        return self.values.get(key, default)


class Base:
    def __init__(self, config, helper):
        self.config = config

    @staticmethod
    def parse_name(config):
        return config.get_name().split()[-1]


class OptionalClientTest(unittest.TestCase):
    def initialize(self, directory, optional):
        tree = ast.parse(SOURCE.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'UpdateManager')
        client = Config('update_manager phobos', {'type': 'zip', 'path': str(directory),
                                                 'enable_if_path_exists': optional})
        config = MagicMock()
        config.getboolean.side_effect = lambda key, default=False: False
        config.getintlist.return_value = [0, 5]
        config.get.return_value = None
        config.get_prefix_sections.return_value = ['update_manager phobos']
        config.__getitem__.return_value = client
        base = Config('base', {'path': '/nonexistent-forgex-test', 'env': '/nonexistent-forgex-test'})
        factory = MagicMock(side_effect=Base)
        namespace = {'asyncio': asyncio, 'pathlib': __import__('pathlib'), 'os': os, 'logging': logging,
                     'InstanceTracker': MagicMock(), 'CommandHelper': MagicMock(), 'BaseDeploy': Base,
                     'get_base_configuration': lambda cfg: {'moonraker': base, 'klipper': base},
                     'get_deploy_class': lambda kind, default: factory if kind == 'zip' else default,
                     'RequestType': types.SimpleNamespace(GET='GET', POST='POST')}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec',
                     flags=__future__.annotations.compiler_flag), namespace)
        manager = namespace['UpdateManager'](config)
        return manager, factory, config.get_server.return_value

    def test_missing_optional_installation_is_not_registered_or_warned(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, factory, server = self.initialize(Path(tmp) / 'missing', True)
            self.assertNotIn('phobos', manager.updaters)
            factory.assert_not_called()
            server.add_warning.assert_not_called()

    def test_installed_optional_package_is_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, factory, server = self.initialize(Path(tmp), True)
            self.assertIn('phobos', manager.updaters)
            factory.assert_called_once()
            server.add_warning.assert_not_called()

    def test_regular_clients_keep_existing_startup_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, factory, _ = self.initialize(Path(tmp) / 'missing', False)
            self.assertIn('phobos', manager.updaters)
            factory.assert_called_once()


if __name__ == '__main__':
    unittest.main()
