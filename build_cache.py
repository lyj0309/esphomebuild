"""Content-addressed, authenticated cache for successful remote builds."""
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import zipfile
from build_crypto import decrypt, encrypt, key

def fingerprint(bundle, recipe, device):
    digest = hmac.new(key(), digestmod=hashlib.sha256)
    def add(value):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    add(b"esphome-build-cache-v1")
    add(device.encode())
    add(recipe.encode())
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
        for member in sorted(archive.getmembers(), key=lambda m: m.name):
            if not member.isfile():
                continue
            data = archive.extractfile(member).read()
            if member.name == "manifest.json":
                manifest = json.loads(data)
                manifest.pop("config_dir", None)
                data = json.dumps(manifest, sort_keys=True).encode()
            add(member.name.encode())
            add(data)
    return digest.hexdigest()

def validate_archive(data, device):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        result = json.loads(zf.read("result.json"))
        if result.get("success") is not True or result.get("device") != device:
            raise ValueError("Cache is not a successful build for this device")
        required = [f"artifact/idedata/{device}.json", f"artifact/storage/{device}.yaml.json"]
        if not all(n in zf.namelist() for n in required):
            raise ValueError("Incomplete cached artifact")
        if not any(n.startswith(f"artifact/build/{device}/") and n.endswith(".bin") for n in zf.namelist()):
            raise ValueError("Cached firmware missing")
        if zf.testzip() is not None:
            raise ValueError("Corrupted cached archive")

def load(path, device, digest):
    data = decrypt(Path(path).read_bytes(), device, digest, "cache")
    validate_archive(data, device)
    return data

def save(path, data, device, digest):
    validate_archive(data, device)
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    blob = encrypt(data, device, digest, "cache")
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(blob)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
