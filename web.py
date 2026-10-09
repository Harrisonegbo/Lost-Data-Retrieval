#!/usr/bin/env python3
"""Loopback-only browser interface for the read-only file carver."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from recover import MAX_FILE_SIZE, carve

HOST = "127.0.0.1"
PORT = 8765
MAX_REQUEST_SIZE = 16 * 1024
STATIC_DIR = Path(__file__).parent / "static"
SAFE_FILENAME = re.compile(r"recovered_[0-9]{6,}\.(?:jpg|png|gif|pdf|bmp|wav|zip|mp4)\Z")
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
logger = logging.getLogger("lost_data_retrieval.web")


@dataclass
class ScanState:
    status: str = "idle"
    source: str = ""
    output: str = ""
    error: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "output": self.output,
            "error": self.error,
            "counts": self.counts,
            "files": self.files,
            "total": sum(self.counts.values()),
        }


class RecoveryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, RecoveryRequestHandler)
        self.state_lock = threading.Lock()
        self.scan_state = ScanState()
        self.output_dir: Path | None = None
        self.scan_thread: threading.Thread | None = None


class RecoveryRequestHandler(BaseHTTPRequestHandler):
    server: RecoveryServer

    def log_message(self, format: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _request_is_local(self) -> bool:
        host_header = self.headers.get("Host", "")
        host = urlsplit(f"//{host_header}").hostname
        if host not in ALLOWED_HOSTS:
            return False

        origin = self.headers.get("Origin")
        if origin is None:
            return True
        parsed_origin = urlsplit(origin)
        return (
            parsed_origin.scheme == "http"
            and parsed_origin.hostname == host
            and parsed_origin.netloc.lower() == host_header.lower()
        )

    def _require_local_request(self) -> bool:
        if self._request_is_local():
            return True
        self._send_json(403, {"error": "This local recovery service only accepts requests from this computer."})
        return False

    def do_GET(self) -> None:
        if not self._require_local_request():
            return

        if self.path == "/":
            self._send_static("index.html", "text/html; charset=utf-8")
        elif self.path == "/styles.css":
            self._send_static("styles.css", "text/css; charset=utf-8")
        elif self.path == "/app.js":
            self._send_static("app.js", "text/javascript; charset=utf-8")
        elif self.path == "/api/status":
            with self.server.state_lock:
                state = self.server.scan_state.as_dict()
            self._send_json(200, state)
        elif self.path.startswith("/api/files/"):
            self._send_recovered_file(self.path.removeprefix("/api/files/"))
        else:
            self._send_json(404, {"error": "Not found."})

    def _send_static(self, name: str, content_type: str) -> None:
        try:
            body = (STATIC_DIR / name).read_bytes()
        except OSError:
            logger.exception("Could not read website asset %s", name)
            self._send_json(500, {"error": "The website asset could not be read."})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._require_local_request():
            return
        if self.path != "/api/scan":
            self._send_json(404, {"error": "Not found."})
            return

        content_type = self.headers.get_content_type()
        raw_length = self.headers.get("Content-Length", "")
        if content_type != "application/json":
            self._send_json(415, {"error": "Send scan settings as JSON."})
            return
        if not raw_length.isdecimal() or int(raw_length) > MAX_REQUEST_SIZE:
            self._send_json(413, {"error": "The scan settings request is invalid or too large."})
            return

        try:
            payload = json.loads(self.rfile.read(int(raw_length)))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "The scan settings are not valid JSON."})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "The scan settings must be a JSON object."})
            return

        source_value = payload.get("source")
        output_value = payload.get("output")
        max_file_size = payload.get("max_file_size", MAX_FILE_SIZE)
        if (
            not isinstance(source_value, str)
            or not source_value.strip()
            or len(source_value) > 4096
            or not isinstance(output_value, str)
            or not output_value.strip()
            or len(output_value) > 4096
        ):
            self._send_json(400, {"error": "Enter a source path and a destination folder."})
            return
        if type(max_file_size) is not int or not 0 < max_file_size <= MAX_FILE_SIZE:
            self._send_json(
                400,
                {"error": f"Maximum file size must be between 1 and {MAX_FILE_SIZE} bytes."},
            )
            return
        if payload.get("confirmed_separate_device") is not True:
            self._send_json(
                400,
                {"error": "Confirm that the destination is on a different physical device."},
            )
            return

        source = Path(source_value.strip())
        output = Path(output_value.strip())
        try:
            if output.resolve() == source.resolve(strict=False):
                self._send_json(400, {"error": "Choose a destination folder, not the source path."})
                return
        except OSError as error:
            self._send_json(400, {"error": f"Could not validate the selected paths: {error}"})
            return

        with self.server.state_lock:
            if self.server.scan_state.status == "running":
                self._send_json(409, {"error": "A scan is already running."})
                return
            self.server.scan_state = ScanState(
                status="running",
                source=str(source),
                output=str(output),
            )
            self.server.output_dir = output

        worker = threading.Thread(
            target=self._run_scan,
            args=(source, output, max_file_size),
            name="recovery-scan",
            daemon=True,
        )
        self.server.scan_thread = worker
        worker.start()
        self._send_json(202, {"status": "running"})

    def _run_scan(self, source_path: Path, output_dir: Path, max_file_size: int) -> None:
        recovered_files: list[str] = []
        try:
            with source_path.open("rb", buffering=0) as source:
                source.seek(0, 2)
                source_size = source.tell()
                if source_size <= 0:
                    raise ValueError("The source is empty or its size could not be determined.")
                counts = carve(
                    source,
                    source_size,
                    output_dir,
                    max_file_size,
                    on_file_recovered=lambda path: recovered_files.append(path.name),
                )
        except (OSError, ValueError) as error:
            with self.server.state_lock:
                self.server.scan_state.status = "failed"
                self.server.scan_state.error = str(error)
                self.server.scan_state.files = recovered_files
            logger.warning("Recovery scan failed: %s", error)
            return
        except Exception as error:
            with self.server.state_lock:
                self.server.scan_state.status = "failed"
                self.server.scan_state.error = "An unexpected error occurred while scanning."
                self.server.scan_state.files = recovered_files
            logger.error("Unexpected recovery scan failure: %s\n%s", error, traceback.format_exc())
            return

        with self.server.state_lock:
            self.server.scan_state.status = "completed"
            self.server.scan_state.counts = counts
            self.server.scan_state.files = recovered_files

    def _send_recovered_file(self, filename: str) -> None:
        if not SAFE_FILENAME.fullmatch(filename):
            self._send_json(404, {"error": "Not found."})
            return

        with self.server.state_lock:
            output_dir = self.server.output_dir
            is_known = filename in self.server.scan_state.files
        if output_dir is None or not is_known:
            self._send_json(404, {"error": "Not found."})
            return

        try:
            file_path = output_dir / filename
            if file_path.resolve().parent != output_dir.resolve():
                self._send_json(404, {"error": "Not found."})
                return
            with file_path.open("rb") as recovered_file:
                file_size = file_path.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                self.send_header("Content-Length", str(file_size))
                self.end_headers()
                try:
                    while chunk := recovered_file.read(1024 * 1024):
                        self.wfile.write(chunk)
                except OSError:
                    logger.exception("Connection failed while sending recovered file %s", filename)
        except OSError:
            logger.exception("Could not send recovered file %s", filename)
            self._send_json(404, {"error": "The recovered file is no longer available."})


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        server = RecoveryServer((HOST, PORT))
    except OSError as error:
        print(f"Could not start the local website at http://{HOST}:{PORT}: {error}", file=sys.stderr)
        return 1

    print(f"Lost Data Retrieval is available at http://{HOST}:{PORT}")
    print("Keep this window open while using the website. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping the local website.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
