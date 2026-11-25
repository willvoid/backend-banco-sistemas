"""Crea o actualiza el usuario de login de un titular existente.

Uso (desde banco_api/):
  python -m scripts.crear_usuario 1000001            # pide la contraseña por teclado
  python -m scripts.crear_usuario 1000001 --password "..."
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Titular, Usuario
from app.passwords import hash_secreto


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("documento")
    ap.add_argument("--password")
    a = ap.parse_args()
    password = a.password or getpass.getpass("Contraseña (mín. 8 caracteres): ")
    if len(password) < 8:
        print("La contraseña debe tener al menos 8 caracteres")
        return 1

    with SessionLocal() as db:
        titular = db.scalar(select(Titular).where(Titular.documento == a.documento))
        if titular is None:
            print(f"No existe un titular con documento {a.documento}")
            return 1
        usuario = db.scalar(select(Usuario).where(Usuario.titular_id == titular.id))
        if usuario is None:
            db.add(Usuario(titular_id=titular.id, password_hash=hash_secreto(password)))
            print(f"Usuario creado para {titular.nombre}")
        else:
            usuario.password_hash = hash_secreto(password)
            usuario.intentos_fallidos = 0
            usuario.bloqueado_hasta = None
            print(f"Contraseña actualizada para {titular.nombre}")
        db.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
