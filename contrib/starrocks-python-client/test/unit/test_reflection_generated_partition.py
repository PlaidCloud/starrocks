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

"""The hidden column StarRocks 3.5 generates for an expression partition is left out of reflection."""

from sqlalchemy.testing import eq_, fixtures

from starrocks.dialect import StarRocksDialect


DDL_3_5 = """CREATE TABLE `gp_expr` (
  `dt` datetime NULL COMMENT "",
  `s` varchar(10) NULL COMMENT "",
  `__generated_partition_column_0` varchar(1048576) NULL AS substr(`s`, 1, 2) COMMENT "",
  `__generated_partition_column_1` datetime NULL AS time_slice(`dt`, INTERVAL 1 month, ceil) COMMENT ""
) ENGINE=OLAP
DUPLICATE KEY(`dt`)
PARTITION BY (`__generated_partition_column_0`, `__generated_partition_column_1`)
DISTRIBUTED BY HASH(`dt`) BUCKETS 1
"""


class GeneratedPartitionColumnTest(fixtures.TestBase):
    def test_generated_columns_and_their_expressions(self):
        eq_(
            StarRocksDialect._generated_partition_columns(DDL_3_5),
            {
                "__generated_partition_column_0": "substr(`s`, 1, 2)",
                "__generated_partition_column_1": "time_slice(`dt`, INTERVAL 1 month, ceil)",
            },
        )

    def test_a_table_without_one_has_none(self):
        eq_(StarRocksDialect._generated_partition_columns(DDL_3_5.replace("__generated_partition_column", "c")), {})
