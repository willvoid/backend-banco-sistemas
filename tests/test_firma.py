from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.firma import cadena_canonica, firmar, hash_solicitud, verificar


def _par():
    clave = Ed25519PrivateKey.generate()
    pem = clave.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return clave, pem.decode()


def test_cadena_canonica_tiene_el_formato_del_contrato():
    c = cadena_canonica("post", "/interbancario/creditos", "BCO1", "k1", "2026-10-26T14:32:10Z", '{"a":1}')
    assert c.split("\n") == [
        "POST", "/interbancario/creditos", "BCO1", "k1", "2026-10-26T14:32:10Z", '{"a":1}'
    ]


def test_firma_valida_y_alterada():
    clave, pem = _par()
    canonica = cadena_canonica("GET", "/cuentas/1001", "BCO1", "k1", "2026-10-26T14:32:10Z", "")
    firma = firmar(clave, canonica)
    assert verificar(pem, canonica, firma)
    assert not verificar(pem, canonica.replace("1001", "1002"), firma)


def test_firma_de_otro_banco_no_verifica():
    clave, _ = _par()
    _, pem_ajena = _par()
    canonica = cadena_canonica("GET", "/x", "BCO1", "k", "t", "")
    assert not verificar(pem_ajena, canonica, firmar(clave, canonica))


def test_firma_basura_no_lanza_excepcion():
    _, pem = _par()
    assert not verificar(pem, "x", "esto no es base64 !!")
    assert not verificar("no es un pem", "x", "AAAA")


def test_hash_solicitud_no_depende_del_orden_de_claves():
    assert hash_solicitud({"a": 1, "b": 2}) == hash_solicitud({"b": 2, "a": 1})
    assert hash_solicitud({"a": 1}) != hash_solicitud({"a": 2})
