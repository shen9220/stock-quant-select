# -*- coding: utf-8 -*-
"""GitHub Pages 交易看板：PBKDF2 + AES-GCM（与浏览器 WebCrypto 对齐）。"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

DEFAULT_ITER = 100_000


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        return kdf.derive(password.encode("utf-8"))
    except Exception:
        from Crypto.Hash import SHA256
        from Crypto.Protocol.KDF import PBKDF2

        return PBKDF2(
            password,
            salt,
            dkLen=32,
            count=iterations,
            hmac_hash_module=SHA256,
        )


def _aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        blob = AESGCM(key).encrypt(iv, plaintext, None)
        return blob[:-16], blob[-16:]
    except Exception:
        from Crypto.Cipher import AES

        cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
        ct, tag = cipher.encrypt_and_digest(plaintext)
        return ct, tag


def _aes_gcm_decrypt(key: bytes, iv: bytes, ct: bytes, tag: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(key).decrypt(iv, ct + tag, None)
    except Exception:
        from Crypto.Cipher import AES

        cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
        return cipher.decrypt_and_verify(ct, tag)


def encrypt_payload(obj: Any, password: str, *, iterations: int = DEFAULT_ITER) -> dict:
    """返回与质量看板相同字段：salt/iv/tag/ct（均为 base64）+ iter。"""
    if not password:
        raise ValueError("password required")
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = _derive_key(password, salt, iterations)
    plain = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    ct, tag = _aes_gcm_encrypt(key, iv, plain)
    return {
        "v": 1,
        "iter": iterations,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
        "ct": base64.b64encode(ct).decode("ascii"),
    }


def decrypt_payload(payload: dict, password: str) -> Any:
    """本地自检用。"""
    salt = base64.b64decode(payload["salt"])
    iv = base64.b64decode(payload["iv"])
    tag = base64.b64decode(payload["tag"])
    ct = base64.b64decode(payload["ct"])
    iterations = int(payload.get("iter") or DEFAULT_ITER)
    key = _derive_key(password, salt, iterations)
    plain = _aes_gcm_decrypt(key, iv, ct, tag)
    return json.loads(plain.decode("utf-8"))
