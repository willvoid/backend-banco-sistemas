"""Genera el par de claves Ed25519 de este banco en keys/. Uso: python -m scripts.generar_claves

La clave PRIVADA no se comparte con nadie. La PÚBLICA se entrega al BCP simulado.
"""

import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DESTINO = Path("keys")


def main() -> int:
    privada, publica = DESTINO / "privada.pem", DESTINO / "publica.pem"
    if privada.exists():
        print(f"Ya existe {privada}; no se sobrescribe. Borrala a mano si querés regenerarla.")
        return 1
    DESTINO.mkdir(exist_ok=True)
    clave = Ed25519PrivateKey.generate()
    privada.write_bytes(
        clave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    publica.write_bytes(
        clave.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    print(f"Clave privada: {privada}  (no compartir)")
    print(f"Clave pública: {publica}  (entregar al BCP simulado)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
