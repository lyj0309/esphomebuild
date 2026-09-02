#!/usr/bin/env python3
"""ESPHome CLI shim: dispatch receiver compile jobs to GitHub Actions."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
import tempfile
import time
import tarfile
import io
import uuid
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

REAL = "/usr/local/bin/esphome-local"
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
WORKFLOW = os.environ.get("GITHUB_WORKFLOW", "build-one.yml")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LOG_TIMESTAMP_RE = re.compile(r"^\ufeff?\d{4}-\d{2}-\d{2}T\S+Z ")


def api(method: str, path: str, body: dict | None = None):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token or not REPO:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required")
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def safe_extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            if not str(target).startswith(str(destination.resolve()) + os.sep):
                raise RuntimeError(f"unsafe artifact path: {member.filename}")
        zf.extractall(destination)


def download_artifact(url: str, destination: Path) -> None:
    # GitHub API redirects to a signed blob URL. Do not forward the GitHub
    # bearer token to that different host, or Actions storage returns 401.
    req = Request(url)
    req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    req.add_header("Accept", "application/vnd.github+json")

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return None

    try:
        response = build_opener(NoRedirect).open(req, timeout=30)
    except HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise
        location = exc.headers.get("Location")
        if not location:
            raise RuntimeError("GitHub artifact redirect did not include Location") from exc
        response = urlopen(Request(location), timeout=120)
    with response:
        with destination.open("wb") as out:
            shutil.copyfileobj(response, out)


def emit_compile_log(job_id: int) -> bool:
    """Replay the ESPHome portion of a completed Actions job log."""
    with tempfile.NamedTemporaryFile(prefix="esphome-gh-log-", delete=False) as out:
        log_path = Path(out.name)
    try:
        download_artifact(f"{API}/repos/{REPO}/actions/jobs/{job_id}/logs", log_path)
        started = False
        emitted = False
        print("*** GitHub Actions ESPHome compile log ***", flush=True)
        for raw_line in log_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = LOG_TIMESTAMP_RE.sub("", raw_line)
            line = ANSI_ESCAPE_RE.sub("", line).rstrip()
            if not started:
                if "INFO ESPHome " not in line:
                    continue
                started = True
            if line.startswith(("##[group]", "##[endgroup]")):
                break
            if line.startswith("##["):
                continue
            print(line, flush=True)
            emitted = True
        if emitted:
            print("*** end GitHub Actions ESPHome compile log ***", flush=True)
        return emitted
    finally:
        log_path.unlink(missing_ok=True)


def install_artifacts(device: str, archive: Path, data_dir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="esphome-gh-artifact-") as tmp:
        extracted = Path(tmp)
        safe_extract(archive, extracted)
        artifact = extracted / "artifact"
        if not artifact.exists():
            artifact = extracted
        source_build = artifact / "build" / device
        if not source_build.exists():
            raise RuntimeError(f"artifact does not contain build/{device}")
        build_dir = data_dir / "build" / device
        if build_dir.exists():
            shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True, exist_ok=True)
        for src in source_build.rglob("*"):
            if src.is_file():
                dst = build_dir / src.relative_to(source_build)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        idedata_src = artifact / "idedata" / f"{device}.json"
        storage_src = artifact / "storage" / f"{device}.yaml.json"
        if not idedata_src.exists() or not storage_src.exists():
            raise RuntimeError("artifact is missing idedata or StorageJSON")
        idedata = json.loads(idedata_src.read_text())
        for image in idedata.get("extra", {}).get("flash_images", []) or []:
            old = image.get("path") if isinstance(image, dict) else None
            if not old:
                continue
            matches = list(build_dir.rglob(Path(old).name))
            image["path"] = str(matches[0] if matches else build_dir / Path(old).name)
        idedata_dir = data_dir / "idedata"
        idedata_dir.mkdir(parents=True, exist_ok=True)
        (idedata_dir / f"{device}.json").write_text(json.dumps(idedata, indent=2) + "\n")

        storage = json.loads(storage_src.read_text())
        original_build = Path(storage.get("build_path", ""))
        original_firmware_path = Path(storage.get("firmware_bin_path", ""))
        storage["build_path"] = str(build_dir)
        firmware = None
        if original_build and original_firmware_path:
            try:
                candidate = build_dir / original_firmware_path.relative_to(original_build)
            except ValueError:
                pass
            else:
                if candidate.is_file():
                    firmware = candidate
        original_firmware = original_firmware_path.name
        if firmware is None and original_firmware:
            firmware = next(build_dir.rglob(original_firmware), None)
        if firmware is None:
            firmware = next(build_dir.rglob("firmware.bin"), None)
        if firmware is None:
            firmware = next(build_dir.rglob("firmware.ota.bin"), None)
        if firmware is None:
            raise RuntimeError("artifact does not contain the OTA firmware image")
        storage["firmware_bin_path"] = str(firmware)
        storage_dir = data_dir / "storage"
        storage_dir.mkdir(parents=True, exist_ok=True)
        (storage_dir / f"{device}.yaml.json").write_text(json.dumps(storage, indent=2) + "\n")
        validated = artifact / "storage" / f"{device}.yaml.validated.yaml"
        if validated.exists():
            shutil.copy2(validated, storage_dir / validated.name)


def encode_config_bundle(config: Path) -> str:
    """Pack the receiver-extracted Remote Build tree, excluding secrets."""
    root = config.parent
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if any(part in {".git", ".esphome", "__pycache__"} for part in relative.parts):
                continue
            if relative.name in {"secrets.yaml", "secrets.yml"}:
                continue
            archive.add(path, arcname=relative, recursive=False)
    encoded = base64.b64encode(buffer.getvalue()).decode()
    # GitHub caps all workflow_dispatch inputs at 65,535 characters.
    if len(encoded) > 60_000:
        raise RuntimeError(
            "configuration bundle exceeds GitHub workflow_dispatch input limit; "
            "move large assets to a remote package or optional config repository"
        )
    return encoded


def remote_compile(config: Path) -> int:
    device = config.stem
    if not device or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in device):
        raise RuntimeError(f"invalid device name: {device!r}")
    request_id = f"remote-{device}-{uuid.uuid4().hex[:12]}"
    ref = os.environ.get("GITHUB_CONFIG_REF", "master")
    config_repo = os.environ.get("GITHUB_CONFIG_REPOSITORY", "")
    bundle_b64 = encode_config_bundle(config)
    print(f"INFO GitHub Actions build requested for {device} ({request_id})", flush=True)
    api("POST", f"/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches", {
        "ref": os.environ.get("GITHUB_WORKFLOW_REF", "master"),
        "inputs": {
            "device": device,
            "config_ref": ref,
            "config_repo": config_repo,
            "config_b64": "",
            "bundle_b64": bundle_b64,
            "request_id": request_id,
        },
    })
    deadline = time.monotonic() + float(os.environ.get("GITHUB_BUILD_TIMEOUT", "3600"))
    run = None
    last_run_state = None
    step_states: dict[int, str] = {}
    build_job_id = None
    compile_log_emitted = False
    while time.monotonic() < deadline:
        runs = api("GET", f"/repos/{REPO}/actions/runs?event=workflow_dispatch&per_page=20")
        for candidate in runs.get("workflow_runs", []):
            if request_id in (candidate.get("display_title") or candidate.get("name") or ""):
                run = candidate
                break
        if run:
            status = run.get("status")
            run_state = run.get("conclusion") if status == "completed" else status
            if run_state != last_run_state:
                print(f"INFO GitHub Actions run {run['id']}: {run_state}", flush=True)
                last_run_state = run_state
            jobs = api("GET", f"/repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=10")
            for job in jobs.get("jobs", []):
                if job.get("name") == "build":
                    build_job_id = job.get("id")
                for step in job.get("steps", []):
                    step_id = step.get("number")
                    step_status = step.get("conclusion") or step.get("status")
                    if step_id is not None and step_status != step_states.get(step_id):
                        print(
                            f"INFO GitHub Actions step {step.get('name')}: {step_status}",
                            flush=True,
                        )
                        step_states[step_id] = step_status
                    if (
                        step.get("name") == "Compile"
                        and step.get("status") == "completed"
                        and build_job_id
                        and not compile_log_emitted
                    ):
                        try:
                            compile_log_emitted = emit_compile_log(build_job_id)
                        except (HTTPError, URLError, OSError):
                            pass
            if status == "completed":
                if build_job_id and not compile_log_emitted:
                    try:
                        compile_log_emitted = emit_compile_log(build_job_id)
                    except (HTTPError, URLError, OSError) as exc:
                        print(f"WARNING GitHub Actions log unavailable: {exc}", flush=True)
                if run.get("conclusion") != "success":
                    print(f"ERROR GitHub Actions concluded {run.get('conclusion')}", flush=True)
                    return 1
                break
        time.sleep(5)
    if not run or run.get("status") != "completed":
        raise TimeoutError("timed out waiting for GitHub Actions")
    artifacts = api("GET", f"/repos/{REPO}/actions/runs/{run['id']}/artifacts").get("artifacts", [])
    wanted = next((a for a in artifacts if a.get("name") == f"esphome-{device}"), None)
    if not wanted:
        raise RuntimeError(f"firmware artifact not found for run {run['id']}")
    with tempfile.NamedTemporaryFile(prefix="esphome-gh-", suffix=".zip", delete=False) as out:
        archive = Path(out.name)
    try:
        download_artifact(wanted["archive_download_url"], archive)
        install_artifacts(device, archive, Path(os.environ.get("ESPHOME_DATA_DIR", ".esphome")))
    finally:
        archive.unlink(missing_ok=True)
    print("INFO GitHub Actions artifacts installed", flush=True)
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "compile" not in args:
        os.execv(REAL, [REAL, *args])
    idx = args.index("compile")
    config = next((Path(a) for a in args[idx + 1:] if a.endswith((".yaml", ".yml"))), None)
    if config is None:
        os.execv(REAL, [REAL, *args])
    try:
        return remote_compile(config)
    except (HTTPError, URLError, OSError, RuntimeError, TimeoutError) as exc:
        print(f"ERROR GitHub remote build failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
