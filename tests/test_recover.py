import io
import struct
import tempfile
import unittest
from pathlib import Path

from recover import carve


def png_chunk(chunk_type: bytes, data: bytes = b"") -> bytes:
    return struct.pack(">I", len(data)) + chunk_type + data + b"\x00\x00\x00\x00"


class CarveTests(unittest.TestCase):
    def test_recovers_jpeg_and_png_and_leaves_source_unchanged(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0sample image\xff\xd9"
        png = (
            b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", b"\x00" * 13)
            + png_chunk(b"IDAT", b"image data")
            + png_chunk(b"IEND")
        )
        source_bytes = b"unrelated data" + jpeg + b"gap" + png

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recovered"
            source = io.BytesIO(source_bytes)
            result = carve(source, len(source_bytes), output)

            self.assertEqual(result, {"jpeg": 1, "png": 1})
            self.assertEqual(source.getvalue(), source_bytes)
            self.assertEqual((output / "recovered_000001.jpg").read_bytes(), jpeg)
            self.assertEqual((output / "recovered_000002.png").read_bytes(), png)

    def test_rejects_truncated_png_and_resumes_scanning(self) -> None:
        truncated_png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 100) + b"IHDR"
        jpeg = b"\xff\xd8\xffimage\xff\xd9"
        source_bytes = truncated_png + b"padding" + jpeg

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recovered"
            result = carve(io.BytesIO(source_bytes), len(source_bytes), output)

            self.assertEqual(result, {"jpeg": 1})
            self.assertEqual((output / "recovered_000001.jpg").read_bytes(), jpeg)

    def test_respects_maximum_file_size(self) -> None:
        jpeg = b"\xff\xd8\xff" + b"x" * 32 + b"\xff\xd9"
        with tempfile.TemporaryDirectory() as directory:
            result = carve(io.BytesIO(jpeg), len(jpeg), Path(directory), max_file_size=16)
            self.assertEqual(result, {})

    def test_recovers_other_supported_formats(self) -> None:
        gif = b"GIF89aimage;"
        pdf = b"%PDF-1.4 document %%EOF"
        bmp = b"BM" + struct.pack("<I", 14) + b"\x00" * 8
        wav = b"RIFF" + struct.pack("<I", 4) + b"WAVE"
        zip_file = b"PK\x03\x04dataPK\x05\x06" + b"\x00" * 18
        mp4 = b"".join(
            struct.pack(">I", 8 + len(payload)) + box_type + payload
            for box_type, payload in (
                (b"ftyp", b"isom"),
                (b"mdat", b"video"),
                (b"moov", b"movie"),
            )
        )
        files = (gif, pdf, bmp, wav, zip_file, mp4)
        source_bytes = b"gap".join(files)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recovered"
            result = carve(io.BytesIO(source_bytes), len(source_bytes), output)

            self.assertEqual(
                result,
                {"gif": 1, "pdf": 1, "bmp": 1, "wav": 1, "zip": 1, "mp4": 1},
            )
            for index, (expected, extension) in enumerate(
                zip(files, (".gif", ".pdf", ".bmp", ".wav", ".zip", ".mp4")), start=1
            ):
                self.assertEqual(
                    (output / f"recovered_{index:06d}{extension}").read_bytes(),
                    expected,
                )

    def test_finds_signature_split_across_scan_chunks(self) -> None:
        prefix = b"x" * (1024 * 1024 - 1)
        jpeg = b"\xff\xd8\xffimage\xff\xd9"
        source_bytes = prefix + jpeg

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recovered"
            result = carve(io.BytesIO(source_bytes), len(source_bytes), output)

            self.assertEqual(result, {"jpeg": 1})
            self.assertEqual((output / "recovered_000001.jpg").read_bytes(), jpeg)

    def test_does_not_overwrite_existing_recovery(self) -> None:
        jpeg = b"\xff\xd8\xffimage\xff\xd9"

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recovered"
            output.mkdir()
            existing = output / "recovered_000001.jpg"
            existing.write_bytes(b"keep this file")

            result = carve(io.BytesIO(jpeg), len(jpeg), output)

            self.assertEqual(result, {"jpeg": 1})
            self.assertEqual(existing.read_bytes(), b"keep this file")
            self.assertEqual((output / "recovered_000002.jpg").read_bytes(), jpeg)


if __name__ == "__main__":
    unittest.main()
