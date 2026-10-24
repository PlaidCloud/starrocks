# Copyright 2021-present StarRocks, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test bootstrap: the SQLAlchemy testing plugin drives every test, including the Alembic suite.

The database URL comes from, in order of precedence: ``--dburi``/``--db``, the ``STARROCKS_URL`` environment
variable, a git-ignored ``test.cfg`` ``[db]`` section, then ``setup.cfg``'s ``[db] default``. Run pytest from
this client directory: Alembic stages its migration scripts under ``scratch`` in the working directory.
"""

import inspect
import os
from pathlib import Path
import unittest

import pytest
from sqlalchemy.dialects import registry


registry.register("starrocks.pymysql", "starrocks.dialect", "StarRocksDialect")
registry.register("starrocks.asyncmy", "starrocks.asyncmy", "StarRocksDialect_asyncmy")
registry.register("starrocks", "starrocks.dialect", "StarRocksDialect")

# It's just used to suppress a spurious warning from pytest.
pytest.register_assert_rewrite("sqlalchemy.testing.assertions")

from sqlalchemy.testing.plugin import plugin_base, pytestplugin  # noqa: E402
from sqlalchemy.testing.plugin.pytestplugin import *  # noqa: E402,F403,I001

import starrocks.alembic  # noqa: E402,F401


CLIENT_DIRECTORY = Path(__file__).resolve().parent.parent


def pytest_configure(config: pytest.Config) -> None:
    _read_config_files_from_client_directory()
    _use_starrocks_url_when_no_dburi(config)
    pytestplugin.pytest_configure(config)


def pytest_pycollect_makeitem(collector: pytest.Collector, name: str, obj: object) -> object:
    _refuse_tests_the_plugin_would_drop(collector, name, obj)
    return pytestplugin.pytest_pycollect_makeitem(collector, name, obj)


def _read_config_files_from_client_directory() -> None:
    """Read ``setup.cfg`` and ``test.cfg`` from this client directory, whatever the working directory.

    SQLAlchemy 1.4 reads them relative to the working directory when it adds its options, so a run started
    elsewhere found no ``[db]`` section. 2.0 reads them later, from pytest's rootdir, which is this directory,
    and has no file config yet at this point.
    """
    if plugin_base.file_config is not None:
        plugin_base.file_config.read([CLIENT_DIRECTORY / "setup.cfg", CLIENT_DIRECTORY / "test.cfg"])


def _use_starrocks_url_when_no_dburi(config: pytest.Config) -> None:
    """Map the ``STARROCKS_URL`` environment variable onto ``--dburi`` unless a URL was given."""
    starrocks_url = os.environ.get("STARROCKS_URL")
    if starrocks_url and not config.option.dburi and not config.option.db:
        config.option.dburi = [starrocks_url]


def _refuse_tests_the_plugin_would_drop(collector: pytest.Collector, name: str, obj: object) -> None:
    """Fail collection for tests that the SQLAlchemy plugin would skip without a message.

    The plugin runs only plain ``test_*`` methods of ``fixtures.TestBase`` subclasses, so these would vanish
    from the run:

    - a module-level test function;
    - a ``testfoo`` method of a ``TestBase`` subclass, or a test that is a classmethod or staticmethod;
    - a class with test methods that does not derive from ``TestBase``, a ``unittest.TestCase`` among them;
    - a class nested in another class: the plugin collects it only when both derive from ``TestBase``.

    That covers a class of any name: pytest's own class name filter does not apply, since the plugin collects a
    ``TestBase`` subclass whatever its name. Classes that are not meant to run are left alone: ``__test__ =
    False``, a ``_`` prefix, or no test methods at all, as with enums and named tuples.
    """
    if not isinstance(collector, pytest.Module) or getattr(obj, "__module__", None) != collector.module.__name__:
        return
    if inspect.isfunction(obj) and collector.funcnamefilter(name):
        raise collector.CollectError(
            f"{name} is a module-level test function; move it into a fixtures.TestBase subclass"
        )
    if inspect.isclass(obj):
        _refuse_tests_of_a_class_the_plugin_would_drop(collector, name, name, obj)


def _refuse_tests_of_a_class_the_plugin_would_drop(collector: pytest.Module, path: str, name: str, cls: type) -> None:
    """Fail collection for a class's tests that the plugin would drop, and for those of the classes nested in it.

    ``name`` is the attribute the class is found under, and ``path`` names it from the module, as ``Outer.Inner``.
    ``fixtures`` is imported here because it can only be imported once the plugin is configured.
    """
    from sqlalchemy.testing import fixtures

    if name.startswith("_") or not getattr(cls, "__test__", True):
        return
    if not issubclass(cls, fixtures.TestBase):
        if _holds_tests(collector, cls):
            raise collector.CollectError(
                f"{path} does not derive from fixtures.TestBase, so the SQLAlchemy plugin would never run its tests; "
                "derive from it, or give a mixin a name starting with _"
            )
        return
    for attr, method in _test_methods(collector, cls).items():
        if isinstance(method, (classmethod, staticmethod)):
            raise collector.CollectError(
                f"{path}.{attr} is a {type(method).__name__}, so the SQLAlchemy plugin would never run it"
            )
        if not attr.startswith("test_"):
            raise collector.CollectError(
                f"{path}.{attr} is not named test_*, so the SQLAlchemy plugin would never run it"
            )
    for nested_name, nested in _nested_classes(collector, cls).items():
        _refuse_tests_of_a_class_the_plugin_would_drop(collector, f"{path}.{nested_name}", nested_name, nested)


def _test_methods(collector: pytest.Module, cls: type) -> dict[str, object]:
    """The functions, classmethods and staticmethods of a class, inherited ones included, that pytest's name
    filter takes for tests, as the class holds them.
    """
    methods = {}
    for attr in dir(cls):
        if collector.funcnamefilter(attr):
            method = inspect.getattr_static(cls, attr)
            if inspect.isfunction(method) or isinstance(method, (classmethod, staticmethod)):
                methods[attr] = method
    return methods


def _nested_classes(collector: pytest.Module, cls: type) -> dict[str, type]:
    """The classes defined in the body of a class, leaving out a class attribute that names a class from
    elsewhere.
    """
    return {
        attr: value
        for attr, value in vars(cls).items()
        if inspect.isclass(value)
        and value.__module__ == collector.module.__name__
        and value.__qualname__ == f"{cls.__qualname__}.{attr}"
    }


def _holds_tests(collector: pytest.Module, cls: type) -> bool:
    """Whether a class is a ``unittest.TestCase``, has test methods, or has a nested class meant to run that
    holds tests.
    """
    if issubclass(cls, unittest.TestCase) or _test_methods(collector, cls):
        return True
    return any(
        not nested_name.startswith("_") and getattr(nested, "__test__", True) and _holds_tests(collector, nested)
        for nested_name, nested in _nested_classes(collector, cls).items()
    )
