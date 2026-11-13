import base64
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DJY_JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789")
os.environ.setdefault("DJY_AES_KEY_B64", base64.b64encode(b"k" * 32).decode())
os.environ.setdefault("DJY_PEPPER_DISPOSITIVO", "test-pepper-0123456789abcdef0123456789")
os.environ.setdefault("DJY_NTP_HABILITADO", "false")
os.environ.setdefault("DJY_DATABASE_URL", "sqlite://")

from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, configurar_engine  # noqa: E402
from app.legal import TZ_BOGOTA, a_utc  # noqa: E402
from app.tiempo import Instante, RelojLegal  # noqa: E402


class RelojFalso(RelojLegal):
    def __init__(self) -> None:
        super().__init__()
        self.actual = a_utc(datetime(2026, 11, 13, 7, 0, tzinfo=TZ_BOGOTA))

    def fijar(self, dt: datetime) -> None:
        self.actual = a_utc(dt if dt.tzinfo else dt.replace(tzinfo=TZ_BOGOTA))

    def ahora(self) -> Instante:
        return Instante(utc=self.actual, ntp_sincronizado=True, desfase_ms=0)


@pytest.fixture()
def entorno():
    engine = configurar_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    from fastapi.testclient import TestClient

    from app import main
    from app.tiempo import get_reloj

    reloj = RelojFalso()
    main.app.dependency_overrides[get_reloj] = lambda: reloj
    main.limitador._eventos.clear()
    client = TestClient(main.app)
    yield client, reloj
    main.app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)

