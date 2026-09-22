"""Authenticated remote build envelopes; plaintext fallback is forbidden."""
import base64
import io
import os
import re
import tarfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"ESPHOME-AESGCM-1\n"

def context(device, request_id, kind):
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", x) for x in (device, request_id)):
        raise ValueError("Invalid build identifier")
    return f"esphome-build-v1:{device}:{request_id}:{kind}".encode()

def key():
    value = os.environ.get("ESPHOME_BUILD_KEY")
    if not value:
        value = Path(os.environ.get("ESPHOME_BUILD_KEY_FILE", "/var/lib/esphome-builder/build-encryption.key")).read_text().strip()
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32:
        raise ValueError("Build key must be 32 bytes")
    return raw

def encrypt(data, device, request_id, kind):
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key()).encrypt(nonce, data, context(device, request_id, kind))

def decrypt(data, device, request_id, kind):
    if not data.startswith(MAGIC):
        raise ValueError("Unencrypted or unknown build envelope refused")
    blob = data[len(MAGIC):]
    return AESGCM(key()).decrypt(blob[:12], blob[12:], context(device, request_id, kind))

def extract_bundle(data, destination):
    root = Path(destination).resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if sum(m.size for m in members) > 32 * 1024 * 1024:
            raise ValueError("Configuration bundle too large")
        for member in members:
            target = (root / member.name).resolve()
            if not target.is_relative_to(root) or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe configuration archive")
        archive.extractall(root, members=members, filter="data")
