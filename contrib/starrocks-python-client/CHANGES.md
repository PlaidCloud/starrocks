Version history
===============

**Unreleased**

- Run every test, with SQLAlchemy's and Alembic's dialect suites, in one `pytest` run under SQLAlchemy's testing plugin, on SQLAlchemy 2.0 and 1.4 and in parallel (by @rad-pat)
- Remove obsolete `setup.py` and `requirements-dev.txt`; consolidate dev dependencies into `pyproject.toml` `[project.optional-dependencies.dev]`
- Drop the stale `[aliases]` section from `setup.cfg`
- Depend on `asyncmy` instead of the deprecated `asyncmy2` fork. Both install the same `asyncmy` module, so uninstall `asyncmy2` before upgrading; if both are already installed, run `pip uninstall asyncmy2` and then `pip install --force-reinstall asyncmy` (by @HosseinNejatiJavaremi)
- Autogenerate no longer resets a table or materialized view property the model omits back to a hardcoded default; it is left as the database has it. Many defaults depend on the StarRocks version or FE config (`replication_num`, `fast_schema_evolution`, `bucket_size`), so the reset was often spurious. To manage a property, declare it in the model (by @chris-celerdata)
- Stop phantom property ALTERs when StarRocks does not report a value the model sets: add defaults for properties only reported when non-default (`write_quorum`, `compaction_strategy`, `enable_load_profile`, `mutable_bucket_num`, `primary_index_cache_expire_sec`, `storage_type`), compare enum and boolean values case-insensitively, and warn instead of generating an ALTER for a property the table cannot hold (`storage_format`, `bucket_size` on a non-RANDOM table, `enable_persistent_index` on a non-PRIMARY KEY table) (by @chris-celerdata)
- Stop autogenerate from resetting an unmanaged `bucket_size` on RANDOM-distributed tables to a hardcoded 4 GiB default; StarRocks 4.0+ defaults to 1 GiB (by @chris-celerdata)
- Reflect `TIME` and `VARIANT` columns instead of returning `NullType` (#77259 by @rad-pat)
- Add the `starrocks_temp_view_schema` Alembic option (`context.configure(...)`) to designate
  the schema in which the transient view used to canonicalize view/MV definitions is created.
  Lets a locked-down migration user be granted the required privileges on a single schema
  (e.g. the same one as `version_table_schema`) instead of on every schema that holds a view.
- Coalesce multiple `ADD`/`DROP COLUMN` autogenerate operations on a table into a single `ALTER TABLE` statement via the `combine_column_alters` rewriter, avoiding StarRocks in-progress schema-change failures (#78647 by @chris-celerdata)
- Add opt-in `starrocks_wait_for_schema_change` to block until a column schema-change job reaches a terminal state (#78647 by @chris-celerdata)
- Support RANGE distribution (StarRocks 4.1+, opt-in via `enable_range_distribution` on 4.1; the shared-data default from #76543): reflect it as `RANGE`, accept `starrocks_distributed_by="RANGE"` (compiled by omitting `DISTRIBUTED BY`), and stop autogenerate from raising on RANGE-distributed tables whose metadata leaves `DISTRIBUTED BY` unset (by @chris-celerdata)
- Create a PRIMARY KEY table from `primary_key=True` columns when no `starrocks_*_key` is given; StarRocks used to make it a DUPLICATE KEY table silently. The key columns must come first, in key order, or a `CompileError` is raised (by @rad-pat)
- Reflect a primary key only for PRIMARY, UNIQUE and AGGREGATE KEY tables; a DUPLICATE KEY table has none (by @rad-pat)
- Refuse what StarRocks cannot do instead of doing something else: a `MaterializedView` with neither `starrocks_refresh` nor `starrocks_distributed_by` (StarRocks would build a synchronous rollup the dialect cannot see), `CREATE OR REPLACE MATERIALIZED VIEW`, view security other than `NONE` and `INVOKER`, unique, foreign key and check constraint operations, and a column default change in `op.alter_column` (by @rad-pat)
- Refuse RANGE distribution and `VARIANT` columns before StarRocks 4.1, and view `starrocks_security` before 3.4.1; before 4.1 a RANGE table was created with the default distribution (by @rad-pat)
- Autogenerate no longer aborts on a change StarRocks cannot ALTER: a table's key type or partition method change is reported as a warning and the other changes are generated, a column default change is reported and skipped, and a materialized view whose change ALTER cannot express is dropped and created again, with a warning that it is refreshed in full. Unique and foreign key constraints are left out (by @rad-pat)
- Autogenerate stops reporting differences that are only spelling: `time_slice` partitions, function and quoted-literal server defaults, the NUL default of HLL and BITMAP columns, an MV refresh moment the model does not state, and the properties StarRocks adds on its own (by @rad-pat)
- Wait for the table's running schema change before each `ALTER TABLE`, `CREATE INDEX` and `DROP INDEX`, so `create_all` with several indexes on a table, and consecutive migration operations on one table, no longer fail with "A schema change operation is in progress"; `op.alter_table_properties` sends one `SET` per property (by @rad-pat)
- Transactions: connections report `AUTOCOMMIT`, which is how StarRocks runs their statements. `isolation_level="READ COMMITTED"` runs DML in StarRocks transactions (3.5 or later; refused before 3.5 when the connection is made): the first INSERT, UPDATE or DELETE opens one with `BEGIN WORK`, commit and rollback end it, and DDL commits it first. What StarRocks would run outside the transaction is refused: a DELETE on a table without a primary key, in text with any comment or hint and through a `schema_translate_map` too; a `delete()` without WHERE, which compiles to TRUNCATE; text holding several statements; and `INSERT INTO FILES` once the transaction is open. Other isolation levels, and a text BEGIN before 3.5, are refused (by @rad-pat)
- Secondary indexes: compile `CREATE INDEX` in StarRocks' grammar, with `starrocks_using`, `starrocks_properties` and `starrocks_comment`; reflect BITMAP, NGRAMBF, GIN and VECTOR indexes, so autogenerate stops creating them again on every run; skip a `drop_index` for an index StarRocks dropped with its column (by @rad-pat)
- Views: `create_all` and `drop_all` handle the views in a `MetaData`, creating each after the tables it reads; view comments are escaped; comment and security changes are applied (`CREATE OR REPLACE VIEW` and `ALTER VIEW ... SET SECURITY`, `op.alter_view(starrocks_security=...)`); a column-only change generates an `ALTER VIEW`; security is compared only when the model states it (by @rad-pat)
- Materialized views: reflect their columns, and emit `REFRESH ASYNC` for the `SCHEDULE` spelling, which StarRocks before 4.1.1 does not know (by @rad-pat)
- Bind ARRAY, MAP and STRUCT values element by element, so strings with quotes, decimals, dates and nested values round-trip exactly on StarRocks 3.3 to 4.1; ARRAY, MAP and STRUCT are cacheable and render as literals (by @rad-pat)
- JSON: render index and path access with `json_query` and the `get_json_*` functions (StarRocks has no `JSON_EXTRACT`), and round-trip JSON scalars as scalars (by @rad-pat)
- Types: compile `PERCENTILE`; accept `PERCENTILE_UNION` and agg-state aggregate types and drop `COUNT`, which StarRocks does not have; keep BINARY and VARBINARY lengths; compile `Text` as STRING and `sa.Enum` as VARCHAR; render `DATETIME(6)` and `TIMESTAMP` as `DATETIME`; quote STRUCT field names; stop dropping `CAST` to FLOAT and DOUBLE; return `float` from `starrocks.DOUBLE`; reflect `DECIMAL256` and the other type names of StarRocks' grammar; render DATETIME literals on SQLAlchemy 1.4 (by @rad-pat)
- DML: render a multi-table UPDATE as `UPDATE ... FROM` and a multi-table DELETE with `USING`; refuse `starrocks_limit` on UPDATE and DELETE; add `insert(...).with_dialect_options(starrocks_overwrite=True)` for `INSERT OVERWRITE` and `starrocks_label=` for `INSERT ... WITH LABEL`; compile `func.sysdate()` as `now()` (by @rad-pat)
- DDL: escape `PROPERTIES` values in every statement; accept a list for `starrocks_order_by`; `op.rename_table` emits `RENAME` without `TO` (by @rad-pat)
- Reflection: reflect each table once instead of twice, and read `information_schema` once per schema when reflecting many tables; inspect views and materialized views through an `Engine` as well as a `Connection` (by @rad-pat)
- Autogenerate orders view and materialized view operations by what each reads: drops before the table operations and creates after them, so a revision's downgrade runs as well as its upgrade. A view alter whose old definition reads a table or column the revision removes becomes a drop and a create. `create_all` reads a string definition's table references with their schema, and without CTE names, comments or join hints (by @rad-pat)
- Autogenerate on SQLAlchemy 1.4 compares tables and leaves the database's views and materialized views alone, with a warning, rather than failing (by @rad-pat)
- Materialized views in autogenerate: a recreated MV lists its columns in SELECT order, keeps the database's refresh and `session.*` properties when the model states none, and its downgrade renders the reflected refresh as clause text; the refresh is left alone when the model states none, and a refresh moment change recreates the MV; `session.*`, `query_rewrite_consistency`, `enable_query_rewrite` and `force_external_table_query_rewrite` are reflected from the MV's DDL; a START date and EVERY spacing compare as StarRocks writes them (by @rad-pat)
- Views and materialized views defined by a `select()` are compiled for StarRocks, so a mixed-case or reserved column name is backquoted instead of double-quoted, which StarRocks cannot parse (by @rad-pat)
- Columns in Alembic: `op.alter_column` renames a column in an ALTER TABLE of its own, repeats KEY or the aggregate type in MODIFY COLUMN whatever case the column name is written in, leaves an unchanged AUTO_INCREMENT out and refuses a changed one, and no longer refuses an autogenerated alter of an integer primary key column; `combine_column_alters` keeps adds before and drops after a sort key change, and drops an index before the ALTER that drops its column; `op.drop_index(if_exists=True)` drops an index that exists (by @rad-pat)
- Properties and defaults in autogenerate: the downgrade of a `colocate_with` or `bloom_filter_columns` change sets `""` instead of "None"; `bloom_filter_columns` compares as a set of columns; an empty-string default is compared, and an expression default as the text StarRocks reflects, `%` included; string defaults are reflected as SHOW CREATE TABLE spells them, and AUTO_INCREMENT and defaults are read from a column's definition, not its comment (by @rad-pat)
- DDL: double `%` in the SQL text spliced into DDL, with SQLAlchemy's `post_process_text`, so a `str2date(s, '%Y-%m-%d')` partition or a `date_format(d, '%Y')` view definition no longer raises; quote the names StarRocks reserves and MySQL does not, such as `files`, `text` and `json`; put a partition by a list of columns in parentheses, which StarRocks 3.3 needs; raise the error of every statement a DDL construct compiles to, through asyncmy too; refuse a multi-column `CREATE INDEX`, and `DROP INDEX IF EXISTS` offline; leave StarRocks 3.5's generated partition column out of reflection (by @rad-pat)
- Queries: give an OFFSET without LIMIT the LIMIT StarRocks needs; keep the precision of `func.sysdate(n)`; cast to the integer type asked for, with `sa.Integer` as BIGINT so a 64-bit value is not turned into NULL, unsigned MySQL types widened to a signed type that holds them, and MEDIUMINT as INT; refuse an executemany of INSERT OVERWRITE or INSERT WITH LABEL, as a construct or as text (by @rad-pat)
- Types: compile NCHAR, `mysql.NCHAR` and a collated CHAR as CHAR; size an Enum's VARCHAR to its length; adapt the generic `sa.ARRAY`, with its `dimensions` as nested ARRAYs and inside other collections; quote STRUCT field names with the identifier preparer, so a name StarRocks reserves, such as `key`, is backquoted and a `%` is doubled; read inf, -inf and nan inside collections (by @rad-pat)
- ARRAY, MAP and STRUCT elements bind and read through their type's own processing, as a plain column's value does: TypeDecorators, Enum, PickleType, JSON with the engine's `json_serializer`, and None. Numbers are written exactly, and text that is not a number is refused rather than stored as NULL. VARBINARY elements read as each server version writes them, and an ARRAY of them is selected as hex on 3.5 and earlier. Alembic's offline `bulk_insert` and `inline_literal` of these types render inside their bind expression. `func.array_agg(...)` now returns a list rather than the server's text (by @rad-pat)
- JSON: a chain of accesses reads in one path, a tuple path followed by an index included; keys a StarRocks path cannot name (empty, `$`, or holding `"` or `[`) are refused (by @rad-pat)
- `starrocks+asyncmy` binds binary values as `X'...'` hex literals, which StarRocks accepts (by @rad-pat)
- Run more of SQLAlchemy's suite: window functions, INTERSECT and EXCEPT, NULLS ordering, VALUES, regexp, bitwise operators, unbounded VARCHAR, historic dates and primary key reflection (by @rad-pat)

**1.3.4**

- Add `to_diff_tuple` for Alembic alter operations (#70146 by @arvindKandpal-ksolves)
- Fix parsing of reflected nested `STRUCT` / `ARRAY` / `MAP` column types when inline field comments are present (#69817)
- Deserialize complex types to matching Python list, dict types (#70480 by @chris-celerdata)
- Add `__hash__` to reflected dataclasses to fix unhashable type errors (#70734 by @aholowko)
- Fix spurious Alembic regeneration for syntax and property changes using SQLGlot (#75984 by @chris-celerdata)
- Add `SCHEDULE` keyword to the Python parser (#76762 by @chris-celerdata)

**1.3.3**

- Add back support for SQLAlchemy 1.4 (#65976 by @rad-pat)
- Enable select from a FilesSource and support for Python 3.14 (#66797 by @rad-pat)
- Add support for async SQLAlchemy connection via asyncmy driver and
  ignore errors from querying tables_config on non-default catalog (#67479 by @rad-pat)

**1.3.2**
- Fix DEFAULT compilation, type comparison, and column ordering (#66125 by @jaogoy)

**1.3.1**
- Enable SQLAlchemy Test Suite (#65025 by @rad-pat)
- Supports Views and Materialized Views and enhance documentation (#65808 by @jaogoy)

**1.3.0**
- Support more attributes for comparing StarRocks tables by using Alembic (#64161 by jaogoy)

**1.2.3**
Older