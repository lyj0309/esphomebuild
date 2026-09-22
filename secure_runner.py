"""No plaintext configuration, firmware, logs or build caches leave this runner."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile
from build_crypto import context, decrypt, encrypt, extract_bundle

def main():
    os.umask(0o077)
    inputs = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())["inputs"]
    device, request_id = inputs["device"], inputs["request_id"]
    context(device, request_id, "input")
    bundle = decrypt(base64.b64decode(inputs["bundle_encrypted"], validate=True), device, request_id, "input")
    output = Path(os.environ["RUNNER_TEMP"]) / "encrypted-output"
    output.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="esphome-private-", dir=os.environ["RUNNER_TEMP"]) as tmp:
        root = Path(tmp)
        extract_bundle(bundle, root)
        if not (root / f"{device}.yaml").is_file():
            raise ValueError("Missing device configuration")
        if not (root / "secrets.yaml").exists():
            secrets = os.environ.get("ESPHOME_SECRETS_YAML")
            if not secrets:
                raise ValueError("Missing actual build secrets; no placeholders allowed")
            (root / "secrets.yaml").write_text(secrets)
        log = root / "build.log"
        with log.open("wb") as stream:
            result = subprocess.run([
                "docker", "run", "--rm",
                "-v", f"{root}:/config", "ghcr.io/esphome/esphome:2026.8.2",
                "compile", f"/config/{device}.yaml",
            ], stdout=stream, stderr=subprocess.STDOUT)
        subprocess.run(["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(root)], check=True)
        archive = io.BytesIO()
        ok = result.returncode == 0
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(log, "build.log")
            if ok:
                build = root / ".esphome" / "build" / device
                bins = []
                for parent, dirs, files in os.walk(build, followlinks=True):
                    for name in files:
                        if name.endswith(".bin") or name in {"build_info.json", "platformio.ini", "sdkconfig." + device}:
                            p = Path(parent) / name
                            zf.write(p, "artifact/build/" + device + "/" + str(p.relative_to(build)))
                            if name.endswith(".bin"):
                                bins.append(p)
                for folder, filename in [("idedata", f"{device}.json"), ("storage", f"{device}.yaml.json")]:
                    p = root / ".esphome" / folder / filename
                    if not p.exists():
                        ok = False
                    else:
                        zf.write(p, f"artifact/{folder}/{filename}")
                ok = ok and bool(bins) and (build / "build_info.json").exists()
            zf.writestr("result.json", json.dumps({"success": ok, "device": device, "request_id": request_id}))
        (output / "build.enc").write_bytes(encrypt(archive.getvalue(), device, request_id, "output"))
        print("Build completed; encrypted output ready" if ok else "Build failed; diagnostics encrypted")
        return 0 if ok else 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Secure build failed before artifact completion; no plaintext diagnostics published")
        raise SystemExit(1)
