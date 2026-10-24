# SQLAlchemy Usage Guide

This guide provides a comprehensive overview of how to use the StarRocks dialect with SQLAlchemy, from basic connections to defining complex tables and executing queries.

## Connecting to StarRocks

You can connect to StarRocks using a standard SQLAlchemy connection string.

```python
from sqlalchemy import create_engine, text

# Format: starrocks://<User>:<Password>@<Host>:<Port>/<Database>
engine = create_engine('starrocks://root@localhost:9030/mydatabase')

with engine.connect() as connection:
    rows = connection.execute(text("SELECT 1")).fetchall()
    print("Connection successful!")
    print(rows)
```

## Transactions

StarRocks commits each statement as it runs, unless a transaction has been opened with `BEGIN WORK`. StarRocks has transactions from version 3.5. The dialect offers two isolation levels:

- **`AUTOCOMMIT`**, the default. Every statement is committed when it runs. `commit()` and `rollback()` send nothing to StarRocks, so a rollback undoes nothing.
- **`READ COMMITTED`**. The first INSERT, UPDATE or DELETE of a SQLAlchemy transaction opens a StarRocks transaction, and `commit()` or `rollback()` ends it. A transaction that only reads opens none.

Set the level on the engine, or on one connection:

```python
engine = create_engine("starrocks://root@localhost:9030/mydatabase", isolation_level="READ COMMITTED")

with engine.begin() as connection:
    connection.execute(orders.insert(), {"id": 1, "total": 10})
    connection.execute(order_lines.insert(), {"order_id": 1, "item": "pen"})
    # both inserts commit together, or neither does

with engine.connect() as connection:
    connection.execution_options(isolation_level="READ COMMITTED")
    with connection.begin():
        ...
```

Other isolation levels, such as `SERIALIZABLE`, raise an `ArgumentError`. Before StarRocks 3.5, `BEGIN WORK` is accepted and does nothing, so the dialect raises an `InvalidRequestError` at the first INSERT, UPDATE or DELETE on a `READ COMMITTED` connection.

StarRocks restricts what a transaction can do, and refuses the statement when the transaction oversteps:

- The tables a transaction changes must all be in one database.
- A transaction can't read a table it has changed, so an ORM `Session` fails when a query reads a table its flush has written. Use `AUTOCOMMIT` with the ORM, or commit before reading back.
- On shared-nothing clusters, a transaction can write each table once. A large `executemany` that the driver splits into several INSERT statements counts as several writes.
- UPDATE and DELETE inside a transaction are refused by StarRocks 3.5. StarRocks documents them for shared-data clusters from 4.0, and 4.1 accepts them on shared-nothing clusters as well.
- `INSERT OVERWRITE` can't run inside a transaction.

The dialect adds two rules of its own:

- **DDL first commits the open transaction**, as MySQL does, because StarRocks refuses DDL inside one. What ran before the DDL is committed, and the next INSERT, UPDATE or DELETE opens a new transaction.
- **A DELETE on a table without a primary key raises an `InvalidRequestError`.** StarRocks applies such a DELETE at once, outside the transaction, and ROLLBACK doesn't undo it ([StarRocks issue 81471](https://github.com/StarRocks/starrocks/issues/81471)). Run those deletes on an `AUTOCOMMIT` connection.

`BEGIN WORK`, `COMMIT` and `ROLLBACK` sent as text are tracked too, so a transaction opened by hand on an `AUTOCOMMIT` connection is ended by the connection's `commit()` or `rollback()`, or when the connection goes back to the pool.

With Alembic, migrations behave as before under `AUTOCOMMIT`. Under `READ COMMITTED`, a migration's data changes, such as `op.bulk_insert`, are transactional until the next DDL operation commits them.

## Defining Schema: Tables, Views, and Materialized Views

You can define your StarRocks schema using SQLAlchemy's Core or ORM approaches.

### Example: Basic Table Definition (Core Style)

SQLAlchemy Core allows you to define tables using the `Table` construct. This approach is explicit and well-suited for projects that don't use the ORM.

```python
from sqlalchemy import Table, MetaData, Column
from starrocks import INTEGER, VARCHAR, DATETIME

metadata = MetaData()

my_table = Table(
    'my_table',
    metadata,
    Column('id', INTEGER, primary_key=True),
    Column('name', VARCHAR(50)),
    Column('timestamp', DATETIME),

    # StarRocks-specific arguments
    starrocks_primary_key='id',
    starrocks_distributed_by='HASH(id) BUCKETS 10',
    starrocks_properties={"replication_num": "1"}
)

# Create the table in the database
metadata.create_all(engine)
```

### Example: Advanced Table Operations (ORM Declarative Style)

For more complex table definitions, including `PRIMARY KEY`, `AGGREGATE KEY` tables, and various StarRocks-specific attributes and data types, you can use SQLAlchemy's ORM declarative style. This approach allows you to define your schema directly within Python classes.

```python
from sqlalchemy import Column, Integer, String, Date, text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from starrocks import BITMAP, INTEGER, DATE, STRING # Import StarRocks specific types
from datetime import date


# --- StarRocks Table declaration ---

Base = declarative_base()

class PageViewAggregates(Base):
    __tablename__ = 'page_view_aggregates'

    page_id = Column(INTEGER, primary_key=True, starrocks_is_agg_key=True)
    visit_date = Column(DATE, primary_key=True, starrocks_is_agg_key=True)
    total_views = Column(INTEGER, starrocks_agg_type='SUM')
    last_user = Column(STRING, starrocks_agg_type='REPLACE')
    distinct_users = Column(BITMAP, starrocks_agg_type='BITMAP_UNION')

    __table_args__ = {
        'starrocks_aggregate_key': 'page_id, visit_date',
        'starrocks_partition_by': 'date_trunc("day", visit_date)',
        'starrocks_distributed_by': 'HASH(page_id)',
        'starrocks_properties': {"replication_num": "1"}
    }

# --- Data Insertion and Query Examples ---

# Assuming `engine` is already created as shown in "Basic Operations"
engine = create_engine('starrocks://root@localhost:9030/mydatabase')

Base.metadata.create_all(engine) # Create the table in the database

Session = sessionmaker(bind=engine)
session = Session()

# Insert data
new_data = PageViewAggregates(
    page_id=1,
    visit_date=date(2023, 10, 26),
    total_views=100,
    last_user="user_A",
    distinct_users=None # BITMAP types might require specific functions for value insertion
)
session.add(new_data)
session.commit()

# Insert data using raw SQL (two records with same key for aggregation)
with engine.connect() as connection:
    connection.execute(
        text("""
            INSERT INTO page_view_aggregates
            (page_id, visit_date, total_views, last_user, distinct_users)
            VALUES
                (:page_id_1, :visit_date_1, :total_views_1, :last_user_1, NULL),
                (:page_id_2, :visit_date_2, :total_views_2, :last_user_2, NULL)"""),
            {
                "page_id_1": 2, "visit_date_1": "2023-10-27",
                "total_views_1": 200, "last_user_1": "user_B",
                "page_id_2": 2, "visit_date_2": "2023-10-27",
                "total_views_2": 150, "last_user_2": "user_C"
            })
    connection.commit()

# Query all data to verify aggregation
all_results = session.query(PageViewAggregates).order_by(PageViewAggregates.page_id).all()
print(f"Total unique rows after insertions: {len(all_results)}")
for row in all_results:
    print(f"  Page ID: {row.page_id}, Visit Date: {row.visit_date}, Total Views: {row.total_views}, Last User: {row.last_user}")

session.close()
```

For a detailed reference on all StarRocks-specific table attributes and data types, please see the **[Table Definition Reference](./tables.md)**.
