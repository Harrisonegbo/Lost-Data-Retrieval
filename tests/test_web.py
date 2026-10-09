import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from web import RecoveryServer


class RecoveryWebsiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = RecoveryServer(("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self.server.scan_thread is not None:
            self.server.scan_thread.join(timeout=2)

    def _post_scan(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        request = Request(
            f"{self.base_url}/api/scan",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_serves_local_website_with_security_headers(self) -> None:
        with urlopen(self.base_url, timeout=3) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertIn("Start read-only scan", html)
        self.assertIn("different physical device", html)

    def test_requires_separate_device_confirmation(self) -> None:
        status, result = self._post_scan(
            {"source": "source.img", "output": "recovered", "max_file_size": 64}
        )
        self.assertEqual(status, 400)
        self.assertIn("different physical device", result["error"])

    def test_scans_locally_and_serves_recovered_file(self) -> None:
        root = Path(self.temp_dir.name)
        source_path = root / "source.img"
        output_path = root / "recovered"
        jpeg = b"\xff\xd8\xffsample image\xff\xd9"
        source_path.write_bytes(b"disk image " + jpeg)

        status, _ = self._post_scan(
            {
                "source": str(source_path),
                "output": str(output_path),
                "max_file_size": 1024,
                "confirmed_separate_device": True,
            }
        )
        self.assertEqual(status, 202)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with urlopen(f"{self.base_url}/api/status", timeout=3) as response:
                state = json.loads(response.read())
            if state["status"] != "running":
                break
            time.sleep(0.02)

        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["counts"], {"jpeg": 1})
        self.assertEqual(state["files"], ["recovered_000001.jpg"])
        with urlopen(
            f"{self.base_url}/api/files/recovered_000001.jpg", timeout=3
        ) as response:
            self.assertEqual(response.read(), jpeg)

    def test_rejects_remote_host_and_cross_origin_requests(self) -> None:
        request = Request(
            f"{self.base_url}/api/status",
            headers={"Host": "attacker.example"},
        )
        with self.assertRaises(HTTPError) as host_error:
            urlopen(request, timeout=3)
        self.assertEqual(host_error.exception.code, 403)

        request = Request(
            f"{self.base_url}/api/status",
            headers={"Origin": "http://attacker.example"},
        )
        with self.assertRaises(HTTPError) as origin_error:
            urlopen(request, timeout=3)
        self.assertEqual(origin_error.exception.code, 403)

    def test_only_downloads_files_from_the_active_scan(self) -> None:
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base_url}/api/files/README.md", timeout=3)
        self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
