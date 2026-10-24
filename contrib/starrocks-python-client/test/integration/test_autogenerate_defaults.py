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

"""Server default comparisons in autogenerate against tables reflected from StarRocks."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Column, MetaData, Table, inspect, testing
from sqlalchemy.testing import fixtures

import starrocks.alembic  # noqa: F401
from starrocks.datatype import BITMAP, HLL, INTEGER, PERCENTILE


class _DefaultsTestBase(fixtures.TestBase):
    __backend__ = True
    __only_on__ = "starrocks"

    @staticmethod
    def _diff_ops(connection, metadata: MetaData, table_prefix: str) -> list:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_server_default": True,
                "include_name": lambda name, type_, parents: type_ != "table" or name.startswith(table_prefix),
            },
        )
        return compare_metadata(context, metadata)


class SketchColumnDefaultTest(_DefaultsTestBase):
    """HLL and BITMAP columns report a NUL placeholder default that the model never declared."""

    @testing.requires.sqlalchemy_2
    def test_sketch_columns_have_no_reflected_default(self, connection, metadata):
        Table(
            "sketch_defaults",
            metadata,
            Column("id", INTEGER),
            Column("h", HLL, starrocks_agg_type="HLL_UNION"),
            Column("b", BITMAP, starrocks_agg_type="BITMAP_UNION"),
            Column("p", PERCENTILE, starrocks_agg_type="PERCENTILE_UNION"),
            starrocks_aggregate_key="id",
            starrocks_distributed_by="HASH(id) BUCKETS 1",
            starrocks_properties={"replication_num": "1"},
        )
        metadata.create_all(connection)

        reflected = Table("sketch_defaults", MetaData(), autoload_with=connection)

        assert [c.name for c in reflected.columns if c.server_default is not None] == []
        assert inspect(connection).get_columns("sketch_defaults")[1]["default"] is None
        assert self._diff_ops(connection, metadata, "sketch_defaults") == []
