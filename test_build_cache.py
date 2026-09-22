import base64
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from build_cache import fingerprint, load, save

def bundle(files, mtime=0):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mtime = mtime
            tf.addfile(item, io.BytesIO(data))
    return buf.getvalue()

def artifact(success=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("result.json", json.dumps({"device": "door1", "success": success}))
        z.writestr("artifact/build/door1/firmware.ota.bin", b"test-firmware")
        z.writestr("artifact/idedata/door1.json", "{}")
        z.writestr("artifact/storage/door1.yaml.json", "{}")
    return buf.getvalue()

class CacheTests(unittest.TestCase):
    def setUp(self):
        os.environ["ESPHOME_BUILD_KEY"] = base64.b64encode(os.urandom(32)).decode()
        self.files = {"door1.yaml": b"name: door1", "secrets.yaml": b"private-key", "driver.h": b"v1"}

    def test_timestamps_and_order_do_not_invalidate(self):
        a = fingerprint(bundle(self.files), "recipe", "door1")
        b = fingerprint(bundle(dict(reversed(list(self.files.items()))), 99999), "recipe", "door1")
        self.assertEqual(a, b)

    def test_each_source_secret_and_recipe_invalidates(self):
        a = fingerprint(bundle(self.files), "recipe", "door1")
        for name in self.files:
            changed = dict(self.files)
            changed[name] += b"changed"
            self.assertNotEqual(a, fingerprint(bundle(changed), "recipe", "door1"))
        self.assertNotEqual(a, fingerprint(bundle(self.files), "new-version", "door1"))

    def test_manifest_temporary_path_ignored(self):
        a, b = dict(self.files), dict(self.files)
        a['manifest.json'] = b'{"config_dir":"/tmp/one", "esphome_version":"1"}'
        b['manifest.json'] = b'{"config_dir":"/tmp/two", "esphome_version":"1"}'
        self.assertEqual(fingerprint(bundle(a), "r", "door1"), fingerprint(bundle(b), "r", "door1"))

    def test_only_successful_authenticated_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cache.enc"
            data = artifact()
            save(p, data, "door1", "abc")
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual(load(p, "door1", "abc"), data)
            self.assertNotIn(b"test-firmware", p.read_bytes())
            with self.assertRaises(Exception):
                load(p, "door1", "different-key")
            p.write_bytes(p.read_bytes()[:-1])
            with self.assertRaises(Exception):
                load(p, "door1", "abc")
            with self.assertRaises(ValueError):
                save(p, artifact(False), "door1", "abc")

if __name__ == "__main__":
    unittest.main()
