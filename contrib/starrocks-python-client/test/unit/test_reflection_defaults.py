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

from types import SimpleNamespace

from sqlalchemy import testing
from sqlalchemy.testing import fixtures

from starrocks.common.defaults import ReflectionViewDefaults
from starrocks.common.types import ViewSecurityType
from starrocks.engine.interfaces import ReflectedViewState
from starrocks.reflection import StarRocksTableDefinitionParser, column_auto_increments, column_default_kinds


class ReflectionViewDefaultsTest(fixtures.TestBase):
    def test_reflection_view_defaults_apply_basic(self):
        info: ReflectedViewState = ReflectionViewDefaults.apply(
            name="v1",
            definition="SELECT 1",
            comment=None,
            security=None,
        )
        assert info.name == "v1"
        assert info.definition == "SELECT 1"
        assert info.comment is None or info.comment == ""
        assert info.security == ViewSecurityType.NONE or info.security is None

    def test_reflection_view_defaults_apply_upper_security_and_keep_comment(self):
        info: ReflectedViewState = ReflectionViewDefaults.apply(
            name="v2",
            definition="SELECT 2",
            comment="some comment",
            security="invoker",
        )
        assert info.comment == "some comment"
        assert info.security == ViewSecurityType.INVOKER

    def test_reflection_view_defaults_empty_strings(self):
        info: ReflectedViewState = ReflectionViewDefaults.apply(
            name="v3",
            definition="SELECT 3",
            comment="",
            security="",
        )
        assert info.comment is None or info.comment == ""
        assert info.security == ViewSecurityType.NONE or info.security is None


class ColumnDefaultValueRendererTest(fixtures.TestBase):
    """How a COLUMN_DEFAULT from information_schema.columns becomes a reflected server default."""

    @staticmethod
    def _rendered(column_type: str, default: str | None) -> str | None:
        column = SimpleNamespace(COLUMN_TYPE=column_type, COLUMN_DEFAULT=default)
        return StarRocksTableDefinitionParser._ColumnDefaultValueRenderer().build(column)

    @testing.combinations(
        ("hll", "hll", "\x00", None),
        ("bitmap", "bitmap", "\x00", None),
        ("percentile", "percentile", "\x00", None),
        ("varchar_keeps_nul", "varchar(10)", "\x00", "'\\0'"),
        ("no_default", "int", None, None),
        ("null_default", "int", "NULL", None),
        ("function", "varchar(36)", "uuid()", "(uuid())"),
        ("datetime_now", "datetime", "CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP"),
        argnames="column_type, default, expected",
        id_="iaaa",
    )
    def test_rendered_default(self, column_type, default, expected):
        assert self._rendered(column_type, default) == expected

    @testing.combinations(
        ("padded_literal", "  padded  ", "literal", "'  padded  '"),
        ("null_word_literal", "NULL", "literal", "'NULL'"),
        ("call_like_literal", "uuid()", "literal", "'uuid()'"),
        ("expression", "uuid()", "expression", "(uuid())"),
        ("keyword", "CURRENT_TIMESTAMP", "keyword", "CURRENT_TIMESTAMP"),
        ("no_default_clause", "x", "none", None),
        id_="iaaa",
        argnames="default, kind, expected",
    )
    def test_spelling_from_show_create_table_decides(self, default, kind, expected):
        """information_schema reports these alike; SHOW CREATE TABLE's spelling tells them apart."""
        column = SimpleNamespace(COLUMN_TYPE="varchar(40)", COLUMN_DEFAULT=default)
        assert StarRocksTableDefinitionParser._ColumnDefaultValueRenderer().build(column, kind) == expected


class ColumnDefaultKindsTest(fixtures.TestBase):
    def test_kinds_read_from_show_create_table(self):
        ddl = (
            "CREATE TABLE `t` (\n"
            '  `id` int(11) NULL COMMENT "",\n'
            '  `c_lit` varchar(20) NULL DEFAULT "uuid()" COMMENT "has DEFAULT (x) inside",\n'
            '  `c_fn` varchar(40) NULL DEFAULT (uuid()) COMMENT "",\n'
            '  `d_ts` datetime NULL DEFAULT CURRENT_TIMESTAMP COMMENT "",\n'
            '  `odd``name` int(11) NULL COMMENT " DEFAULT \\"x\\""\n'
            ") ENGINE=OLAP\nDUPLICATE KEY(`id`)"
        )
        assert column_default_kinds(ddl) == {
            "id": "none",
            "c_lit": "literal",
            "c_fn": "expression",
            "d_ts": "keyword",
            "odd`name": "none",
        }

    def test_engine_text_in_a_comment_or_default_does_not_end_the_columns(self):
        ddl = (
            "CREATE TABLE `t` (\n"
            '  `a` varchar(20) NULL DEFAULT "ENGINE=OLAP" COMMENT "",\n'
            '  `b` int(11) NULL COMMENT "multi\nline ENGINE= comment",\n'
            '  `c` varchar(40) NULL DEFAULT (uuid()) COMMENT "",\n'
            "  INDEX idx_a (`a`) USING BITMAP\n"
            ") ENGINE=OLAP\nDUPLICATE KEY(`a`)"
        )
        assert column_default_kinds(ddl) == {"a": "literal", "b": "none", "c": "expression"}


class ColumnAutoIncrementsTest(fixtures.TestBase):
    def test_only_the_column_definition_counts(self):
        ddl = (
            "CREATE TABLE `t` (\n"
            '  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT "",\n'
            '  `n` int(11) NULL COMMENT "unlike id, not AUTO_INCREMENT",\n'
            '  `d` varchar(20) NULL DEFAULT "AUTO_INCREMENT" COMMENT "",\n'
            '  `my id` bigint(20) NOT NULL auto_increment COMMENT ""\n'
            ") ENGINE=OLAP\nPRIMARY KEY(`id`)"
        )
        assert column_auto_increments(ddl) == {"id": True, "n": False, "d": False, "my id": True}

    def test_a_view_lists_no_columns(self):
        assert column_auto_increments("CREATE VIEW `v` AS SELECT 1") == {}
