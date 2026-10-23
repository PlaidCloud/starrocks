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

"""The conftest guard that fails collection for tests the SQLAlchemy plugin would drop without a word.

The objects it is given are built inside the combinations, so this module holds no such test itself.
"""

import enum
import unittest

import pytest
from sqlalchemy import testing
from sqlalchemy.testing import fixtures
from sqlalchemy.testing.assertions import expect_raises_message

from test.conftest import _refuse_tests_the_plugin_would_drop


def _a_test_method(self: object) -> None:
    pass


def _nested_class(outer_base: type, inner_base: type) -> type:
    """A class named Outer, deriving from ``outer_base``, with a class named Inner nested in it, deriving from
    ``inner_base`` and holding a test.
    """

    class Outer(outer_base):
        class Inner(inner_base):
            def test_x(self) -> None:
                pass

    return Outer


class CollectionGuardTest(fixtures.TestBase):
    @testing.combinations(
        ("class_named_like_a_test", "ReflectionTest", type("ReflectionTest", (), {"test_x": _a_test_method})),
        ("class_named_otherwise", "ReflectionChecks", type("ReflectionChecks", (), {"test_x": _a_test_method})),
        ("class_named_tests", "ReflectionTests", type("ReflectionTests", (), {"test_x": _a_test_method})),
        ("unittest_case", "ReflectionCase", type("ReflectionCase", (unittest.TestCase,), {"test_x": _a_test_method})),
        (
            "testbase_misnamed_method",
            "ReflectionTest",
            type("ReflectionTest", (fixtures.TestBase,), {"testx": _a_test_method}),
        ),
        ("module_level_function", "test_reflection", lambda: None),
        id_="iaa",
        argnames="name, obj",
    )
    def test_refuses_what_the_plugin_would_drop(self, request, name, obj):
        with expect_raises_message(pytest.Collector.CollectError, name):
            _refuse_tests_the_plugin_would_drop(request.node.getparent(pytest.Module), name, obj)

    @testing.combinations(
        (
            "testbase_classmethod",
            type("ReflectionTest", (fixtures.TestBase,), {"test_x": classmethod(_a_test_method)}),
            "ReflectionTest.test_x is a classmethod",
        ),
        (
            "testbase_staticmethod",
            type("ReflectionTest", (fixtures.TestBase,), {"test_x": staticmethod(_a_test_method)}),
            "ReflectionTest.test_x is a staticmethod",
        ),
        (
            "plain_class_with_a_classmethod_test",
            type("ReflectionChecks", (), {"test_x": classmethod(_a_test_method)}),
            "ReflectionChecks does not derive from fixtures.TestBase",
        ),
        (
            "plain_class_in_testbase",
            _nested_class(fixtures.TestBase, object),
            "Outer.Inner does not derive from fixtures.TestBase",
        ),
        (
            "testbase_in_plain_class",
            _nested_class(object, fixtures.TestBase),
            "Outer does not derive from fixtures.TestBase",
        ),
        id_="iaa",
        argnames="obj, message",
    )
    def test_refuses_classmethods_staticmethods_and_nested_classes_the_plugin_would_drop(self, request, obj, message):
        with expect_raises_message(pytest.Collector.CollectError, message):
            _refuse_tests_the_plugin_would_drop(request.node.getparent(pytest.Module), obj.__name__, obj)

    @testing.combinations(
        ("testbase", "ReflectionTest", type("ReflectionTest", (fixtures.TestBase,), {"test_x": _a_test_method})),
        ("underscore_mixin", "_ReflectionChecks", type("_ReflectionChecks", (), {"test_x": _a_test_method})),
        (
            "test_false",
            "ReflectionChecks",
            type("ReflectionChecks", (), {"test_x": _a_test_method, "__test__": False}),
        ),
        ("no_test_methods", "Colour", enum.Enum("Colour", "RED GREEN")),
        ("helper_function", "_reflect", lambda: None),
        ("testbase_in_testbase", "Outer", _nested_class(fixtures.TestBase, fixtures.TestBase)),
        (
            "class_attribute_naming_a_mixin",
            "ReflectionTest",
            type(
                "ReflectionTest",
                (fixtures.TestBase,),
                {"checks": type("_ReflectionChecks", (), {"test_x": _a_test_method})},
            ),
        ),
        id_="iaa",
        argnames="name, obj",
    )
    def test_leaves_alone_what_is_not_a_dropped_test(self, request, name, obj):
        _refuse_tests_the_plugin_would_drop(request.node.getparent(pytest.Module), name, obj)
