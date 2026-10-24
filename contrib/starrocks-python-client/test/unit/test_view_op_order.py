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

"""Where autogenerate puts view and materialized view operations, in the upgrade and in its reversed downgrade."""

from types import SimpleNamespace

from alembic.operations import ops
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.testing import fixtures
from sqlalchemy.testing.assertions import eq_

from starrocks import INTEGER, VARCHAR
from starrocks.alembic.compare import order_view_and_mv_ops
from starrocks.alembic.ops import (
    AlterMaterializedViewOp,
    AlterViewOp,
    CreateMaterializedViewOp,
    CreateViewOp,
    DropMaterializedViewOp,
    DropViewOp,
)


def _ordered(*operations):
    upgrade_ops = ops.UpgradeOps(ops=list(operations))
    context = SimpleNamespace(dialect=SimpleNamespace(name="starrocks", default_schema_name="db"))
    order_view_and_mv_ops(context, upgrade_ops, {None})
    return upgrade_ops


def _labels(operations):
    labels = []
    for op in operations.ops:
        name = getattr(op, "view_name", None) or op.table_name
        schema = getattr(op, "schema", None)
        labels.append(f"{type(op).__name__}:{schema + '.' if schema else ''}{name}")
    return labels


def _table(name, *columns, schema=None):
    return Table(name, MetaData(), Column("id", INTEGER), *columns, schema=schema)


class ViewOperationOrderTest(fixtures.TestBase):
    def test_a_removed_view_is_dropped_before_its_table_and_created_after_it(self):
        upgrade = _ordered(
            ops.DropTableOp.from_table(_table("t1")),
            DropViewOp("v1", existing_definition="SELECT id FROM t1"),
            DropMaterializedViewOp("mv1", existing_definition="SELECT id FROM t1"),
        )
        eq_(_labels(upgrade), ["DropMaterializedViewOp:mv1", "DropViewOp:v1", "DropTableOp:t1"])
        eq_(_labels(upgrade.reverse()), ["CreateTableOp:t1", "CreateViewOp:v1", "CreateMaterializedViewOp:mv1"])

    def test_an_added_view_is_created_after_its_table(self):
        upgrade = _ordered(
            CreateViewOp("a_view", "SELECT id FROM z_table"),
            ops.CreateTableOp.from_table(_table("z_table")),
        )
        eq_(_labels(upgrade), ["CreateTableOp:z_table", "CreateViewOp:a_view"])

    def test_an_alter_reading_a_column_the_revision_drops_becomes_a_drop_and_a_create(self):
        drop_name = ops.ModifyTableOps(
            "t1", [ops.DropColumnOp.from_column_and_tablename(None, "t1", Column("name", VARCHAR(20)))]
        )
        upgrade = _ordered(
            drop_name,
            AlterViewOp(
                "v1",
                definition="SELECT id FROM t1",
                existing_definition="SELECT id, name FROM t1",
                existing_security="INVOKER",
            ),
        )
        eq_(_labels(upgrade), ["DropViewOp:v1", "ModifyTableOps:t1", "CreateViewOp:v1"])
        eq_(upgrade.ops[0].kwargs, {"starrocks_security": "INVOKER"})
        eq_((upgrade.ops[2].definition, upgrade.ops[2].kwargs), ("SELECT id FROM t1", {"starrocks_security": "INVOKER"}))
        downgrade = upgrade.reverse()
        eq_(_labels(downgrade), ["DropViewOp:v1", "ModifyTableOps:t1", "CreateViewOp:v1"])
        eq_(downgrade.ops[2].definition, "SELECT id, name FROM t1")

    def test_an_alter_of_a_view_over_unchanged_tables_stays_an_alter_after_the_tables(self):
        add_name = ops.ModifyTableOps("t1", [ops.AddColumnOp.from_column_and_tablename(None, "t1", Column("name", VARCHAR(20)))])
        upgrade = _ordered(
            AlterViewOp("v1", definition="SELECT id, name FROM t1", existing_definition="SELECT id FROM t1"),
            add_name,
            AlterMaterializedViewOp("mv1", refresh="MANUAL", existing_refresh="ASYNC"),
        )
        eq_(_labels(upgrade), ["ModifyTableOps:t1", "AlterMaterializedViewOp:mv1", "AlterViewOp:v1"])

    def test_views_and_mvs_reading_each_other_follow_what_they_read(self):
        upgrade = _ordered(
            CreateViewOp("a_view", "SELECT id FROM m_view"),
            CreateViewOp("m_view", "SELECT id FROM `z_mv`"),
            CreateMaterializedViewOp("z_mv", "SELECT id FROM db.t1"),
            ops.CreateTableOp.from_table(_table("t1")),
        )
        eq_(
            _labels(upgrade),
            ["CreateTableOp:t1", "CreateMaterializedViewOp:z_mv", "CreateViewOp:m_view", "CreateViewOp:a_view"],
        )
        eq_(
            _labels(upgrade.reverse()),
            ["DropViewOp:a_view", "DropViewOp:m_view", "DropMaterializedViewOp:z_mv", "DropTableOp:t1"],
        )

    def test_views_named_alike_in_two_schemas_are_ordered_by_name(self):
        upgrade = _ordered(
            CreateViewOp("orders", "SELECT id FROM staging.orders", schema="reporting"),
            CreateViewOp("orders", "SELECT id FROM staging.orders", schema="archive"),
        )
        eq_(_labels(upgrade), ["CreateViewOp:archive.orders", "CreateViewOp:reporting.orders"])

    def test_a_cycle_keeps_name_order(self):
        upgrade = _ordered(CreateViewOp("b", "SELECT id FROM a"), CreateViewOp("a", "SELECT id FROM b"))
        eq_(_labels(upgrade), ["CreateViewOp:a", "CreateViewOp:b"])

    def test_without_views_the_operations_are_left_alone(self):
        tables = [ops.CreateTableOp.from_table(_table("z")), ops.CreateTableOp.from_table(_table("a"))]
        eq_(_labels(_ordered(*tables)), ["CreateTableOp:z", "CreateTableOp:a"])
