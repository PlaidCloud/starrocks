# Copyright 2021-present StarRocks, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https:#www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test bootstrap: the SQLAlchemy testing plugin drives every test, including the Alembic suite.

The database comes from ``--dburi``/``--db``, a git-ignored ``test.cfg`` ``[db]`` section, or
``setup.cfg``'s ``[db] default``; ``STARROCKS_URL`` is honoured when no ``--dburi`` is given.
"""

import inspect
import os

import pytest
from sqlalchemy.dialects import registry


registry.register("starrocks+pymysql", "starrocks.dialect", "StarRocksDialect")
registry.register("starrocks", "starrocks.dialect", "StarRocksDialect")

# It's just used to suppress a spurious warning from pytest.
pytest.register_assert_rewrite("sqlalchemy.testing.assertions")

from sqlalchemy.testing.plugin import pytestplugin  # noqa: E402
from sqlalchemy.testing.plugin.pytestplugin import *  # noqa: E402,F403,I001

import starrocks.alembic  # noqa: E402,F401


def pytest_configure(config: pytest.Config) -> None:
    _use_starrocks_url_when_no_dburi(config)
    pytestplugin.pytest_configure(config)


def pytest_pycollect_makeitem(collector: pytest.Collector, name: str, obj: object) -> object:
    _refuse_tests_the_plugin_would_drop(collector, name, obj)
    return pytestplugin.pytest_pycollect_makeitem(collector, name, obj)


def _use_starrocks_url_when_no_dburi(config: pytest.Config) -> None:
    """Map the ``STARROCKS_URL`` environment variable onto ``--dburi`` unless a URL was given."""
    starrocks_url = os.environ.get("STARROCKS_URL")
    if starrocks_url and not config.option.dburi and not config.option.db:
        config.option.dburi = [starrocks_url]


def _refuse_tests_the_plugin_would_drop(collector: pytest.Collector, name: str, obj: object) -> None:
    """Fail collection for test functions or classes that are not ``fixtures.TestBase`` subclasses.

    The SQLAlchemy plugin collects only ``fixtures.TestBase`` subclasses and skips everything else
    without a message, so a plain pytest test would otherwise vanish from the run unnoticed.
    ``fixtures`` is imported here because it can only be imported once the plugin is configured.
    """
    from sqlalchemy.testing import fixtures

    if not isinstance(collector, pytest.Module) or getattr(obj, "__module__", None) != collector.module.__name__:
        return
    if inspect.isfunction(obj) and collector.funcnamefilter(name):
        raise collector.CollectError(
            f"{name} is a module-level test function; move it into a fixtures.TestBase subclass"
        )
    if (
        inspect.isclass(obj)
        and not name.startswith("_")
        and (collector.classnamefilter(name) or name.endswith("Test"))
        and not issubclass(obj, fixtures.TestBase)
    ):
        raise collector.CollectError(f"{name} does not derive from fixtures.TestBase, so it would never run")
