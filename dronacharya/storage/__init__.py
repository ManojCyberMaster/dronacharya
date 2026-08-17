from .base import Repository  # noqa: F401


def get_repo(config):
    """Backend selection: sqlite (standalone/client) or postgres (server role)."""
    if config.storage.backend == "postgres":
        from .postgres import PostgresRepo

        if not config.storage.postgres_dsn:
            raise RuntimeError("storage.backend is 'postgres' but postgres_dsn is empty")
        return PostgresRepo(config.storage.postgres_dsn)
    from ..config import db_path
    from .sqlite import SqliteRepo

    return SqliteRepo(db_path())
