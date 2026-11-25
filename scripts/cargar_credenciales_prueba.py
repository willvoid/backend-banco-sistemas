"""SOLO DESARROLLO: da login y PIN a los datos de prueba de schema_banco_familiar.sql.

Uso (desde banco_api/):  python -m scripts.cargar_credenciales_prueba

Valores de prueba (no usar fuera del entorno local):
  - contraseña de los 3 titulares de ejemplo: PASSWORD_PRUEBA
  - PIN de todas las cuentas de ejemplo:      PIN_PRUEBA
"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Cuenta, Titular, Usuario
from app.passwords import hash_secreto

PASSWORD_PRUEBA = "clave1234"
PIN_PRUEBA = "1234"


def main() -> None:
    with SessionLocal() as db:
        h_pass, h_pin = hash_secreto(PASSWORD_PRUEBA), hash_secreto(PIN_PRUEBA)
        for titular in db.scalars(select(Titular)):
            usuario = db.scalar(select(Usuario).where(Usuario.titular_id == titular.id))
            if usuario is None:
                db.add(Usuario(titular_id=titular.id, password_hash=h_pass))
            else:
                usuario.password_hash = h_pass
        for cuenta in db.scalars(select(Cuenta)):
            cuenta.pin_hash = h_pin
        db.commit()
    print("Credenciales de prueba cargadas (ver constantes en este script).")


if __name__ == "__main__":
    main()
