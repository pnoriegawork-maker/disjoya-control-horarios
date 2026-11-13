"""Motor SQLAlchemy y propagación del contexto RLS a PostgreSQL.

Cada transacción que abre la sesión ejecuta `set_config('app.empleado_id' / 'app.rol', ..., true)`
(alcance local a la transacción), de modo que las políticas Row Level Security de la base de
datos se evalúan con la identidad del usuario autenticado aun si la API tuviera un defecto."""
from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def configurar_engine(url: str | None = None, **kwargs) -> Engine:
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    _engine = create_engine(url, pool_pre_ping=True, future=True, **kwargs)
    if _engine.dialect.name == "postgresql":

        @event.listens_for(_engine, "connect")
        def _tz(dbapi_conn, _rec):  # noqa: ANN001
            with dbapi_conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'UTC'")
            dbapi_conn.commit()

    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configurar_engine()
    return _engine  # type: ignore[return-value]


def nueva_sesion(empleado_id: str | None = None, rol: str = "SISTEMA") -> Session:
    if _SessionLocal is None:
        configurar_engine()
    sesion: Session = _SessionLocal()  # type: ignore[misc]
    sesion.info["rls"] = {"empleado_id": empleado_id or "", "rol": rol}
    return sesion


def fijar_contexto(sesion: Session, empleado_id: str, rol: str) -> None:
    sesion.info["rls"] = {"empleado_id": empleado_id, "rol": rol}
    if sesion.in_transaction():
        _aplicar(sesion.connection(), sesion.info["rls"])


def _aplicar(conexion, ctx: dict | None = None) -> None:  # noqa: ANN001
    if conexion.dialect.name != "postgresql":
        return
    sesion_ctx = ctx or {}
    conexion.execute(
        text("SELECT set_config('app.empleado_id', :e, true), set_config('app.rol', :r, true)"),
        {"e": sesion_ctx.get("empleado_id", ""), "r": sesion_ctx.get("rol", "ANONIMO")},
    )


@event.listens_for(Session, "after_begin")
def _rls_after_begin(sesion: Session, _transaccion, conexion) -> None:  # noqa: ANN001
    _aplicar(conexion, sesion.info.get("rls"))


def get_db() -> Iterator[Session]:
    sesion = nueva_sesion(rol="ANONIMO")
    try:
        yield sesion
    finally:
        sesion.close()
