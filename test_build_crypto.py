import base64
import io
import os
import tarfile
import tempfile
import unittest
from cryptography.exceptions import InvalidTag
from build_crypto import decrypt, encrypt, extract_bundle


class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        os.environ["ESPHOME_BUILD_KEY"] = base64.b64encode(os.urandom(32)).decode()

    def test_roundtrip_and_random_nonce(self):
        data = b"private WiFi/API credentials"
        a, b = [encrypt(data, "door1", "request-1", "input") for _ in range(2)]
        self.assertNotEqual(a, b)
        self.assertNotIn(data, a)
        self.assertEqual(decrypt(a, "door1", "request-1", "input"), data)

    def test_tampering_and_context_rejected(self):
        data = encrypt(b"private", "door1", "request-1", "input")
        for device, request, kind in [("other", "request-1", "input"), ("door1", "request-2", "input"), ("door1", "request-1", "output")]:
            with self.assertRaises(InvalidTag):
                decrypt(data, device, request, kind)
        with self.assertRaises(InvalidTag):
            decrypt(data[:-1] + bytes([data[-1] ^ 1]), "door1", "request-1", "input")
        os.environ["ESPHOME_BUILD_KEY"] = base64.b64encode(os.urandom(32)).decode()
        with self.assertRaises(InvalidTag):
            decrypt(data, "door1", "request-1", "input")
        with self.assertRaises(ValueError):
            decrypt(b"plaintext", "door1", "request-1", "input")

    def test_archive_traversal_and_links_rejected(self):
        for name, link in [("../escape", False), ("safe", True)]:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                item = tarfile.TarInfo(name)
                if link:
                    item.type = tarfile.SYMTYPE
                    item.linkname = "/etc/passwd"
                tf.addfile(item)
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    extract_bundle(buf.getvalue(), tmp)

if __name__ == "__main__":
    unittest.main()
