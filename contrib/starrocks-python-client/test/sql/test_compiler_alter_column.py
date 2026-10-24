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

"""How the dialect compiles Alembic's MySQL column-alter elements: MODIFY COLUMN, CHANGE COLUMN and ALTER DEFAULT."""

from alembic import op
from alembic.ddl.mysql import MySQLAlterDefault, MySQLChangeColumn, MySQLModifyColumn
from alembic.testing.fixtures import op_fixture
from sqlalchemy import exc, testing, text
from sqlalchemy.testing import expect_raises_message, fixtures
from sqlalchemy.testing.assertions import AssertsCompiledSQL

import starrocks.alembic  # noqa: F401
from starrocks.datatype import INTEGER, STRING
from starrocks.dialect import StarRocksDialect


class AlterColumnCompileTest(fixtures.TestBase, AssertsCompiledSQL):
    __dialect__ = StarRocksDialect()

    def test_modify_type_and_nullable(self):
        self.assert_compile(
            MySQLModifyColumn(
                "t", "c", schema=None, type_=INTEGER(), nullable=False, default=False, autoincrement=None, comment=False
            ),
            "ALTER TABLE t MODIFY COLUMN c INTEGER NOT NULL",
        )

    def test_modify_type_nullable_default_comment(self):
        self.assert_compile(
            MySQLModifyColumn(
                "t",
                "c",
                schema=None,
                type_=STRING,
                nullable=True,
                default="abc",
                autoincrement=None,
                comment="greeting",
            ),
            "ALTER TABLE t MODIFY COLUMN c STRING NULL DEFAULT 'abc' COMMENT 'greeting'",
        )

    def test_modify_schema_qualified(self):
        self.assert_compile(
            MySQLModifyColumn(
                "t", "c", schema="s", type_=INTEGER(), nullable=True, default=False, autoincrement=None, comment=False
            ),
            "ALTER TABLE s.t MODIFY COLUMN c INTEGER NULL",
        )

    def test_modify_quotes_reserved_and_spaced_names(self):
        self.assert_compile(
            MySQLModifyColumn(
                "order",
                "select",
                schema="my schema",
                type_=INTEGER(),
                nullable=False,
                default=False,
                autoincrement=None,
                comment=False,
            ),
            "ALTER TABLE `my schema`.`order` MODIFY COLUMN `select` INTEGER NOT NULL",
        )

    def test_modify_autoincrement(self):
        self.assert_compile(
            MySQLModifyColumn(
                "t", "c", schema=None, type_=INTEGER(), nullable=False, default=False, autoincrement=True, comment=False
            ),
            "ALTER TABLE t MODIFY COLUMN c INTEGER NOT NULL AUTO_INCREMENT",
        )

    def test_change_rename_only(self):
        """Alembic always passes the column type to CHANGE COLUMN, so a rename also emits a MODIFY COLUMN.

        StarRocks rejects MODIFY and RENAME in one statement; ``test_suite_alembic.BackendAlterColumnTest``
        marks the resulting failure, and this expectation changes when the dialect is fixed.
        """
        self.assert_compile(
            MySQLChangeColumn(
                "t",
                "old",
                schema=None,
                newname="new",
                type_=INTEGER(),
                nullable=True,
                default=False,
                autoincrement=None,
                comment=False,
            ),
            "ALTER TABLE t MODIFY COLUMN old INTEGER NULL, RENAME COLUMN old TO new",
        )

    def test_change_rename_and_modify(self):
        self.assert_compile(
            MySQLChangeColumn(
                "t",
                "old",
                schema=None,
                newname="new",
                type_=INTEGER(),
                nullable=False,
                default="0",
                autoincrement=None,
                comment="changed",
            ),
            "ALTER TABLE t MODIFY COLUMN old INTEGER NOT NULL DEFAULT '0' COMMENT 'changed', RENAME COLUMN old TO new",
        )

    @testing.combinations(
        ("literal", "abc"),
        ("expression", text("CURRENT_TIMESTAMP")),
        argnames="default",
        id_="ia",
    )
    def test_alter_default_is_refused(self, default):
        """StarRocks cannot change a default, and the MySQL statement lacks the column type it asks for."""
        with expect_raises_message(exc.CompileError, "cannot change a column's default value"):
            MySQLAlterDefault("t", "c", default=default, schema=None).compile(dialect=self.__dialect__)


class AlterColumnDefaultOperationTest(fixtures.TestBase):
    """``op.alter_column(server_default=...)`` refuses a change of default and passes on an unchanged one."""

    def test_new_default_alone_is_refused(self):
        op_fixture("starrocks")
        with expect_raises_message(exc.CompileError, "cannot change a column's default value"):
            op.alter_column("t", "c", server_default="x", existing_type=INTEGER())

    def test_new_default_with_a_type_change_is_refused(self):
        op_fixture("starrocks")
        with expect_raises_message(exc.CompileError, "'c' of table 't'"):
            op.alter_column("t", "c", type_=INTEGER(), server_default="x", existing_server_default="y")

    def test_dropped_default_is_refused(self):
        op_fixture("starrocks")
        with expect_raises_message(exc.CompileError, "cannot change a column's default value"):
            op.alter_column("t", "c", server_default=None, existing_server_default="y", existing_type=INTEGER())

    def test_unchanged_default_stays_in_the_modify(self):
        context = op_fixture("starrocks")
        op.alter_column("t", "c", type_=INTEGER(), server_default="y", existing_server_default="y", nullable=True)
        context.assert_("ALTER TABLE t MODIFY COLUMN c INTEGER NULL DEFAULT 'y'")

    def test_no_default_argument_is_not_refused(self):
        context = op_fixture("starrocks")
        op.alter_column("t", "c", type_=INTEGER(), existing_server_default="y", nullable=True)
        context.assert_("ALTER TABLE t MODIFY COLUMN c INTEGER NULL DEFAULT 'y'")
