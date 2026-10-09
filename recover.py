#!/usr/bin/env python3
"""Read-only file carver for disk images and storage devices."""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
FORMATS = (
    ("jpeg", ".jpg", b"\xff\xd8\xff", "jpeg"),
    ("png", ".png", b"\x89PNG\r\n\x1a\n", "png"),
    ("gif", ".gif", b"GIF87a", "gif"),
    ("gif", ".gif", b"GIF89a", "gif"),
    ("pdf", ".pdf", b"%PDF-", "pdf"),
    ("bmp", ".bmp", b"BM", "bmp"),
    ("wav", ".wav", b"RIFF", "wav"),
    ("zip", ".zip", b"PK\x03\x04", "zip"),
    ("mp4", ".mp4", b"ftyp", "mp4"),
)
MAX_SIGNATURE_SIZE = max(len(signature) for _, _, signature, _ in FORMATS)


@dataclass(frozen=True)
class Candidate:
    offset: int
    name: str
    extension: str
    kind: str


def find_next_candidate(source: BinaryIO, start: int, source_size: int) -> Candidate | None:
    """Find the earliest supported file signature at or after start."""
    source.seek(start)
    read_position = start
    overlap = b""

    while read_position < source_size:
        block = source.read(min(CHUNK_SIZE, source_size - read_position))
        if not block:
            return None
        data = overlap + block
        base_offset = read_position - len(overlap)
        matches: list[Candidate] = []

        for name, extension, signature, kind in FORMATS:
            index = data.find(signature)
            while index != -1:
                offset = base_offset + index
                if kind == "mp4":
                    offset -= 4
                if offset >= start and offset >= 0:
                    matches.append(Candidate(offset, name, extension, kind))
                    break
                index = data.find(signature, index + 1)

        if matches:
            return min(matches, key=lambda candidate: candidate.offset)

        read_position += len(block)
        overlap = data[-(MAX_SIGNATURE_SIZE - 1) :]

    return None


def _find_marker_end(
    source: BinaryIO, start: int, source_size: int, max_file_size: int, marker: bytes
) -> int | None:
    limit = min(source_size, start + max_file_size)
    source.seek(start)
    position = start
    overlap = b""

    while position < limit:
        block = source.read(min(CHUNK_SIZE, limit - position))
        if not block:
            return None
        data = overlap + block
        base = position - len(overlap)
        index = data.find(marker)
        if index != -1:
            return base + index + len(marker)
        position += len(block)
        overlap = data[-(len(marker) - 1) :] if len(marker) > 1 else b""

    return None


def _read_at(source: BinaryIO, offset: int, length: int) -> bytes:
    source.seek(offset)
    return source.read(length)


def _png_end(source: BinaryIO, start: int, source_size: int, max_file_size: int) -> int | None:
    position = start + 8
    limit = min(source_size, start + max_file_size)
    while position + 12 <= limit:
        header = _read_at(source, position, 8)
        if len(header) != 8:
            return None
        length = struct.unpack(">I", header[:4])[0]
        chunk_type = header[4:]
        chunk_end = position + 12 + length
        if chunk_end > limit:
            return None
        if chunk_type == b"IEND":
            return chunk_end if length == 0 else None
        position = chunk_end
    return None


def _zip_end(source: BinaryIO, start: int, source_size: int, max_file_size: int) -> int | None:
    limit = min(source_size, start + max_file_size)
    source.seek(start + 4)
    position = start + 4
    overlap = b""
    marker = b"PK\x05\x06"

    while position < limit:
        source.seek(position)
        block = source.read(min(CHUNK_SIZE, limit - position))
        if not block:
            return None
        data = overlap + block
        base = position - len(overlap)
        index = data.find(marker)
        while index != -1:
            end_record = base + index
            header = _read_at(source, end_record, 22)
            if len(header) == 22:
                comment_length = struct.unpack_from("<H", header, 20)[0]
                archive_end = end_record + 22 + comment_length
                if archive_end <= limit:
                    return archive_end
            index = data.find(marker, index + 1)
        position += len(block)
        overlap = data[-(len(marker) - 1) :]

    return None


def _mp4_end(source: BinaryIO, start: int, source_size: int, max_file_size: int) -> int | None:
    limit = min(source_size, start + max_file_size)
    position = start
    saw_mdat = False
    saw_moov = False

    while position + 8 <= limit:
        header = _read_at(source, position, 8)
        if len(header) != 8:
            break
        box_size = struct.unpack(">I", header[:4])[0]
        box_type = header[4:]
        header_size = 8
        if box_size == 1:
            extended_size = _read_at(source, position + 8, 8)
            if len(extended_size) != 8:
                break
            box_size = struct.unpack(">Q", extended_size)[0]
            header_size = 16
        elif box_size == 0:
            box_size = limit - position

        if (
            box_size < header_size
            or position + box_size > limit
            or not all(32 <= byte <= 126 for byte in box_type)
        ):
            break
        saw_mdat |= box_type == b"mdat"
        saw_moov |= box_type == b"moov"
        position += box_size

    return position if saw_mdat and saw_moov and position > start else None


def find_file_end(
    source: BinaryIO,
    candidate: Candidate,
    source_size: int,
    max_file_size: int,
) -> int | None:
    start = candidate.offset
    limit = min(source_size, start + max_file_size)

    if candidate.kind == "jpeg":
        return _find_marker_end(source, start + 3, source_size, max_file_size, b"\xff\xd9")
    if candidate.kind == "gif":
        return _find_marker_end(source, start + 6, source_size, max_file_size, b";")
    if candidate.kind == "pdf":
        return _find_marker_end(source, start + 5, source_size, max_file_size, b"%%EOF")
    if candidate.kind == "png":
        return _png_end(source, start, source_size, max_file_size)
    if candidate.kind == "bmp":
        header = _read_at(source, start, 6)
        if len(header) == 6 and header[:2] == b"BM":
            size = struct.unpack_from("<I", header, 2)[0]
            return start + size if 14 <= size <= max_file_size and start + size <= source_size else None
        return None
    if candidate.kind == "wav":
        header = _read_at(source, start, 12)
        if len(header) == 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            size = struct.unpack_from("<I", header, 4)[0] + 8
            return start + size if 12 <= size <= max_file_size and start + size <= source_size else None
        return None
    if candidate.kind == "zip":
        return _zip_end(source, start, source_size, max_file_size)
    if candidate.kind == "mp4":
        return _mp4_end(source, start, source_size, max_file_size)
    return None


def carve(
    source: BinaryIO,
    source_size: int,
    output_dir: Path,
    max_file_size: int = MAX_FILE_SIZE,
) -> dict[str, int]:
    """Carve contiguous files from a seekable source without modifying it."""
    recovered: dict[str, int] = {}
    scan_offset = 0
    file_number = 1
    output_dir.mkdir(parents=True, exist_ok=True)

    while scan_offset < source_size:
        candidate = find_next_candidate(source, scan_offset, source_size)
        if candidate is None:
            break
        end = find_file_end(source, candidate, source_size, max_file_size)
        if end is None or end <= candidate.offset:
            scan_offset = candidate.offset + 1
            continue

        while True:
            destination = output_dir / f"recovered_{file_number:06d}{candidate.extension}"
            try:
                output = destination.open("xb")
                break
            except FileExistsError:
                file_number += 1
        source.seek(candidate.offset)
        remaining = end - candidate.offset
        with output:
            while remaining:
                data = source.read(min(CHUNK_SIZE, remaining))
                if not data:
                    raise OSError(f"Source ended while recovering bytes at offset {candidate.offset}.")
                output.write(data)
                remaining -= len(data)

        recovered[candidate.name] = recovered.get(candidate.name, 0) + 1
        file_number += 1
        scan_offset = end

    return recovered


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only file carving from a disk image or storage device."
    )
    parser.add_argument(
        "source",
        help=r"Disk image or device path (for example: \\.\PhysicalDrive1)",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="Destination directory on a different storage device",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=MAX_FILE_SIZE,
        metavar="BYTES",
        help=f"Maximum recovered file size (default: {MAX_FILE_SIZE} bytes)",
    )
    args = parser.parse_args()

    if args.max_file_size <= 0:
        parser.error("--max-file-size must be greater than zero")

    try:
        with open(args.source, "rb", buffering=0) as source:
            source.seek(0, 2)
            source_size = source.tell()
            if source_size <= 0:
                parser.error("Could not determine the source size or the source is empty.")
            print(f"Scanning {args.source} ({source_size:,} bytes) in read-only mode.")
            counts = carve(source, source_size, args.output, args.max_file_size)
    except OSError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    total = sum(counts.values())
    if total == 0:
        print("No supported, contiguous files were found.")
    else:
        print(f"Recovered {total} file(s) to {args.output}:")
        for file_type, count in sorted(counts.items()):
            print(f"  {file_type}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
