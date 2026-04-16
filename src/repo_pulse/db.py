from sqlmodel import SQLModel, Session, create_engine


def build_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def init_db(engine) -> None:
    # Ensure model modules register their tables before creating them.
    from repo_pulse import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def session_factory(engine) -> Session:
    return Session(engine)
