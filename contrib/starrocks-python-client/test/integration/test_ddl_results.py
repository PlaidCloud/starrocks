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

"""Each statement a DDL construct compiles to raises its own error, through PyMySQL and through asyncmy."""

import asyncio

from sqlalchemy import Table, exc, testing
from sqlalchemy.testing import config, fixtures
from sqlalchemy.testing.assertions import expect_raises_message
from sqlalchemy.testing.schema import Column

from starrocks import INTEGER
from starrocks.sql.ddl import AlterTableProperties


def _properties_the_third_of_which_fails():
    """Properties StarRocks takes one at a time, so they compile to three ALTER TABLE SET statements."""
    return {"replication_num": "1", "in_memory": "false", "primary_index_cache_expire_sec": "abc"}


class DdlResultsTest(fixtures.TablesTest):
    __backend__ = True
    __only_on__ = "starrocks"

    @classmethod
    def define_tables(cls, metadata):
        Table("ddl_results", metadata, Column("id", INTEGER, primary_key=True, autoincrement=False))

    def test_the_error_of_a_third_statement_raises(self, connection):
        with expect_raises_message(exc.DBAPIError, "primary_index_cache_expire_sec"):
            connection.execute(AlterTableProperties("ddl_results", _properties_the_third_of_which_fails()))

    @testing.requires.sqlalchemy_2
    def test_the_error_of_a_third_statement_raises_through_asyncmy(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(config.db.url.set(drivername="starrocks+asyncmy"))

        async def alter():
            try:
                async with engine.begin() as conn:
                    await conn.execute(AlterTableProperties("ddl_results", _properties_the_third_of_which_fails()))
            finally:
                await engine.dispose()

        with expect_raises_message(exc.DBAPIError, "primary_index_cache_expire_sec"):
            asyncio.run(alter())
