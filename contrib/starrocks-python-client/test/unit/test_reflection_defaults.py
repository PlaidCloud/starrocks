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
from starrocks.reflection import StarRocksTableDefinitionParser


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
