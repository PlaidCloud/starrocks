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

"""UPDATE, DELETE and INSERT compilation."""

from sqlalchemy import Column, Integer, MetaData, Table, delete, exc, testing, update
from sqlalchemy.testing import expect_raises_message, fixtures
from sqlalchemy.testing.assertions import AssertsCompiledSQL

from starrocks.dialect import StarRocksDialect


METADATA = MetaData()
A = Table("a", METADATA, Column("id", Integer), Column("v", Integer))
B = Table("b", METADATA, Column("id", Integer), Column("w", Integer))
C = Table("c", METADATA, Column("id", Integer), Column("x", Integer))


class MultiTableDmlCompileTest(fixtures.TestBase, AssertsCompiledSQL):
    __dialect__ = StarRocksDialect()

    def test_update_from_one_table(self):
        self.assert_compile(
            update(A).values(v=B.c.w).where(A.c.id == B.c.id),
            "UPDATE a SET v=b.w FROM b WHERE a.id = b.id",
        )

    def test_update_from_two_tables(self):
        self.assert_compile(
            update(A).values(v=B.c.w + C.c.x).where(A.c.id == B.c.id).where(B.c.id == C.c.id),
            "UPDATE a SET v=(b.w + c.x) FROM b, c WHERE a.id = b.id AND b.id = c.id",
        )

    def test_update_single_table_has_no_from(self):
        self.assert_compile(update(A).values(v=1).where(A.c.id == 2), "UPDATE a SET v=%s WHERE a.id = %s")

    def test_delete_using_one_table(self):
        self.assert_compile(
            delete(A).where(A.c.id == B.c.id).where(B.c.w == 3),
            "DELETE FROM a USING b WHERE a.id = b.id AND b.w = %s",
        )

    def test_delete_using_two_tables(self):
        self.assert_compile(
            delete(A).where(A.c.id == B.c.id).where(B.c.id == C.c.id),
            "DELETE FROM a USING b, c WHERE a.id = b.id AND b.id = c.id",
        )


class UnsupportedLimitTest(fixtures.TestBase):
    """StarRocks has no ``UPDATE ... LIMIT`` or ``DELETE ... LIMIT``, so the dialect must not accept one."""

    @testing.combinations(
        (lambda: update(A).values(v=1), "Update"),
        (lambda: delete(A), "Delete"),
        argnames="statement_factory, statement_class",
    )
    def test_starrocks_limit_is_rejected(self, statement_factory, statement_class):
        with expect_raises_message(
            exc.ArgumentError,
            f"Argument 'starrocks_limit' is not accepted by dialect 'starrocks' on behalf of .*{statement_class}",
        ):
            statement_factory().with_dialect_options(starrocks_limit=3)
