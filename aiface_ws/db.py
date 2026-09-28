"""
Async engine and session factory.
"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel


def make_engine(url: str) -> AsyncEngine:
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_async_engine(url, **kwargs)

    if url.startswith("sqlite"):
        # pysqlite does not open the outer transaction by itself, so a
        # released SAVEPOINT is already committed and a later rollback
        # cannot undo it. Every punch is saved inside a savepoint, so this
        # matters. This is SQLAlchemy's documented fix.
        @event.listens_for(engine.sync_engine, "connect")
        def _no_autobegin(dbapi_connection, _record):
            dbapi_connection.isolation_level = None

        @event.listens_for(engine.sync_engine, "begin")
        def _explicit_begin(conn):
            conn.exec_driver_sql("BEGIN")

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # autoflush=False on purpose (it is the common production setting).
    # storage.upsert_employee has to look at `session.new` because of it;
    # see the note there.
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def create_tables(engine: AsyncEngine) -> None:
    from aiface_ws import models  # noqa: F401  (registers the tables)

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
