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

"""Autogenerate leaves out the unique and foreign key constraints StarRocks tables cannot hold."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Column, ForeignKey, Table, UniqueConstraint, testing
from sqlalchemy.testing import fixtures

import starrocks.alembic  # noqa: F401
from starrocks.datatype import INTEGER


class UnholdableConstraintsTest(fixtures.TestBase):
    __backend__ = True
    __only_on__ = "starrocks"

    @testing.requires.sqlalchemy_2
    def test_no_constraint_ops_for_a_matching_table(self, connection, metadata):
        """The model's constraints were dropped by CREATE TABLE, so a matching model has nothing to add."""
        Table(
            "constraint_parent",
            metadata,
            Column("id", INTEGER, nullable=False),
            starrocks_primary_key="id",
            starrocks_distributed_by="HASH(id) BUCKETS 1",
        )
        Table(
            "constraint_child",
            metadata,
            Column("id", INTEGER, nullable=False),
            Column("parent_id", INTEGER, ForeignKey("constraint_parent.id")),
            Column("code", INTEGER, unique=True),
            Column("other_code", INTEGER),
            UniqueConstraint("other_code", name="uq_constraint_child_other_code"),
            starrocks_primary_key="id",
            starrocks_distributed_by="HASH(id) BUCKETS 1",
        )
        metadata.create_all(connection)

        context = MigrationContext.configure(
            connection,
            opts={"include_name": lambda name, type_, parents: type_ != "table" or name.startswith("constraint_")},
        )
        assert compare_metadata(context, metadata) == []
