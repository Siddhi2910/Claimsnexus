from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
import structlog
from app.config import settings

log = structlog.get_logger()
DB_MODE = "uninitialized"


def _build_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite+aiosqlite"):
        return create_async_engine(url, echo=settings.app_debug, pool_pre_ping=True)
    return create_async_engine(
        url,
        echo=settings.app_debug,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


engine: AsyncEngine = _build_engine(settings.database_url)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


def db_mode() -> str:
    return DB_MODE


def db_available() -> bool:
    return DB_MODE != "memory"


def _switch_engine(url: str) -> None:
    global engine, AsyncSessionLocal
    engine = _build_engine(url)
    AsyncSessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


async def get_db() -> AsyncSession | None:
    if not db_available():
        yield None
        return
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception as exc:
            try:
                await session.rollback()
            except Exception as rb_exc:
                log.warning("db_rollback_failed", error=str(rb_exc))
            log.warning("db_session_error", error=str(exc))
        finally:
            try:
                await session.commit()
            except Exception as commit_exc:
                log.warning("DB FAILED -> USING MEMORY", error=str(commit_exc))
            try:
                await session.close()
            except Exception as close_exc:
                log.warning("db_session_close_failed", error=str(close_exc))


async def init_db() -> None:
    global DB_MODE
    # Ensure model metadata is loaded before create_all.
    from app.models import claim as _claim_model  # noqa: F401
    from app.models import decision as _decision_model  # noqa: F401

    configured_url = settings.database_url or ""
    is_sqlite_configured = configured_url.startswith("sqlite")
    is_postgres_configured = "asyncpg" in configured_url or "postgresql" in configured_url

    # Build candidate list — skip Postgres if the configured URL is already SQLite.
    # This prevents slow startup from connection-refused errors when Postgres isn't running.
    candidates: list[tuple[str, str]] = []

    if is_postgres_configured and not is_sqlite_configured:
        candidates.append(("postgres", configured_url))

    if is_sqlite_configured:
        # Configured URL is SQLite — use it first
        candidates.append(("sqlite", configured_url))

    # Always have SQLite file fallback, then in-memory as last resort
    candidates.append(("sqlite", "sqlite+aiosqlite:///./claimsnexus.db"))
    candidates.append(("sqlite", "sqlite+aiosqlite:///:memory:"))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_candidates: list[tuple[str, str]] = []
    for mode, url in candidates:
        if url not in seen:
            seen.add(url)
            unique_candidates.append((mode, url))

    for mode, url in unique_candidates:
        try:
            _switch_engine(url)
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
                await conn.run_sync(Base.metadata.create_all)
            DB_MODE = "sqlite" if mode == "sqlite" else "postgres"
            log.info("database_initialized", mode=mode, url=url)
            return
        except Exception as exc:
            log.warning("database_init_candidate_failed", mode=mode, url=url, error=str(exc)[:200])

    DB_MODE = "memory"
    log.warning("DB FAILED -> USING MEMORY")
