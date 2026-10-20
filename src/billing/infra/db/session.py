from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from billing.config import get_settings

# Created once per process; connections are opened lazily on the first query.
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
