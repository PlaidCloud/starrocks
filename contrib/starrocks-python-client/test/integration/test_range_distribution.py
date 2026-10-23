# Copyright 2021-present StarRocks, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Reflection, autogenerate comparison and CREATE TABLE for RANGE distribution.

With range distribution enabled (opt-in on StarRocks 4.1; the shared-data default in
newer releases), a table that declares a key type or ORDER BY but no DISTRIBUTED BY
clause gets RANGE distribution. There is no DISTRIBUTED BY RANGE
syntax, so a model that leaves DISTRIBUTED BY unset (or sets it to 'RANGE') matches such
a table, and CREATE TABLE omits the clause.

Each connection opts in with the ``enable_range_distribution`` session variable, which
applies in any run mode, so these tests run on a StarRocks 4.1+ cluster and skip on
older ones.
"""

from __future__ import annotations

import logging
from unittest.mock import Mock

from alembic.autogenerate.api import AutogenContext
from alembic.operations.ops import UpgradeOps
from sqlalchemy import Column, Integer, MetaData, Table, event, inspect, testing
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DatabaseError
from sqlalchemy.schema import CreateTable
from sqlalchemy.testing import config, engines, expect_raises_message, fixtures

from starrocks.alembic.compare import compare_starrocks_table
from starrocks.common.params import TableInfoKeyWithPrefix


logger = logging.getLogger(__name__)


def _opt_in_to_range_distribution(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("SET enable_range_distribution = true")
    except Exception as exc:  # not a StarRocks 4.1+ cluster; the probe below skips
        logger.info("enable_range_distribution not settable: %s", exc)
    finally:
        cursor.close()


def _range_distribution_available(engine: Engine, schema: str) -> bool:
    """Whether a table created without DISTRIBUTED BY gets RANGE distribution here."""
    with engine.begin() as conn:
        conn.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS `{schema}`")
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS `{schema}`.`probe_range`")
        try:
            conn.exec_driver_sql(
                f"CREATE TABLE `{schema}`.`probe_range` (k1 INT, v INT) DUPLICATE KEY(k1)"
            )
        except DatabaseError as exc:
            logger.info("range probe table not creatable: %s", exc)
            return False
    return _distribute_type(engine, schema, "probe_range") == "RANGE"


def _distribute_type(engine: Engine, schema: str, table: str) -> str:
    with engine.connect() as conn:
        return str(conn.exec_driver_sql(
            "SELECT DISTRIBUTE_TYPE FROM information_schema.tables_config "
            f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}'"
        ).scalar()).upper()


class RangeDistributionTest(fixtures.TestBase):
    """Runs in a database of its own, named after the worker's, through an engine that opts in to RANGE."""

    __backend__ = True
    __only_on__ = "starrocks"

    @classmethod
    def setup_test_class(cls) -> None:
        cls.schema = f"{config.db.url.database}_range_dist"
        cls.engine = engines.testing_engine(options={"scope": "class"})
        event.listen(cls.engine, "connect", _opt_in_to_range_distribution)
        cls.engine.dispose()  # drop pooled connections opened before the listener
        if not _range_distribution_available(cls.engine, cls.schema):
            with cls.engine.begin() as conn:
                conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{cls.schema}`")
            cls.engine.dispose()
            config.skip_test("cluster does not support RANGE distribution (needs StarRocks 4.1+)")

    @classmethod
    def teardown_test_class(cls) -> None:
        with cls.engine.begin() as conn:
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{cls.schema}`")
        cls.engine.dispose()

    def _create_range_table(self, name: str) -> Table:
        with self.engine.begin() as conn:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS `{self.schema}`.`{name}`")
            conn.exec_driver_sql(
                f"CREATE TABLE `{self.schema}`.`{name}` (k1 INT, v INT) DUPLICATE KEY(k1)"
            )
        return Table(name, MetaData(), autoload_with=self.engine, schema=self.schema)

    def _target(self, reflected: Table, distributed_by=None) -> Table:
        """The reflected table's definition, with DISTRIBUTED BY replaced."""
        kwargs = {
            k: v for k, v in reflected.dialect_kwargs.items()
            if k.lower() != TableInfoKeyWithPrefix.DISTRIBUTED_BY
        }
        if distributed_by is not None:
            kwargs[TableInfoKeyWithPrefix.DISTRIBUTED_BY] = distributed_by
        return Table(
            reflected.name, MetaData(), Column("k1", Integer), Column("v", Integer),
            schema=self.schema, **kwargs,
        )

    def _compare(self, reflected: Table, target: Table) -> list:
        autogen_context = Mock(spec=AutogenContext)
        autogen_context.dialect = self.engine.dialect
        autogen_context.inspector = inspect(self.engine)
        upgrade_ops = UpgradeOps()
        compare_starrocks_table(autogen_context, upgrade_ops, self.schema, reflected.name, reflected, target)
        return upgrade_ops.ops

    def test_reflects_range(self):
        reflected = self._create_range_table("t_reflect")
        assert reflected.dialect_options["starrocks"]["distributed_by"] == "RANGE"

    @testing.combinations(("unset", None), ("range", "RANGE"), argnames="distributed_by", id_="ia")
    def test_autogenerate_no_change(self, distributed_by):
        reflected = self._create_range_table("t_no_change")
        assert self._compare(reflected, self._target(reflected, distributed_by)) == []

    def test_autogenerate_range_to_hash_raises(self):
        reflected = self._create_range_table("t_to_hash")
        with expect_raises_message(NotImplementedError, "RANGE-distributed table"):
            self._compare(reflected, self._target(reflected, "HASH(k1) BUCKETS 4"))

    def test_create_table_with_range_marker(self):
        """A model marked RANGE compiles without DISTRIBUTED BY and yields a RANGE table."""
        name = "t_create"
        table = Table(
            name, MetaData(), Column("k1", Integer), Column("v", Integer),
            schema=self.schema,
            starrocks_duplicate_key="k1",
            starrocks_distributed_by="RANGE",
        )
        with self.engine.begin() as conn:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS `{self.schema}`.`{name}`")
            ddl = str(CreateTable(table).compile(dialect=self.engine.dialect))
            assert "DISTRIBUTED BY" not in ddl
            conn.execute(CreateTable(table))
        assert _distribute_type(self.engine, self.schema, name) == "RANGE"
