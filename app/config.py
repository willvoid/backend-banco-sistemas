from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    banco_id: str = "BCO1"
    private_key_path: Path = Path("keys/privada.pem")
    ventana_firma_seg: int = 300
    timeout_otros_bancos_seg: float = 5.0

    jwt_secret: str = ""
    jwt_minutos: int = 30  # tope absoluto de la sesión desde el login
    inactividad_seg: int = 300  # cierre de sesión tras este tiempo sin actividad
    max_intentos_login: int = 5
    bloqueo_login_min: int = 15

    # Orígenes permitidos para el frontend en el navegador, separados por coma. "*" = cualquiera.
    cors_origins: str = ""

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "banco_familiar"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "mysql+pymysql",
            username=self.db_user,
            password=self.db_password or None,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
