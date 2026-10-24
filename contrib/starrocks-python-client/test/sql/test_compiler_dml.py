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

from sqlalchemy import Column, Integer, MetaData, Table, update
from sqlalchemy.testing import fixtures
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
