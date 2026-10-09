# Lost Data Retrieval

A small, dependency-free Python file carver for recovering recognizable files
from disk images and raw storage devices. It reads the selected source and
writes recovered files to a separate destination; it does not format, repair,
or write to the source.

## Supported formats

| Format | Extension | Recovery method |
| --- | --- | --- |
| JPEG | `.jpg` | JPEG start and end markers |
| PNG | `.png` | PNG chunk lengths and end chunk |
| GIF | `.gif` | GIF header and trailer |
| PDF | `.pdf` | PDF header and end marker |
| BMP | `.bmp` | Header-declared file size |
| WAV | `.wav` | RIFF/WAVE header-declared size |
| ZIP | `.zip` | Local header and end-of-central-directory record |
| MP4/MOV | `.mp4` | `ftyp`, `mdat`, and `moov` boxes |

## Requirements

- Python 3.10 or newer
- No third-party packages

## Usage

First, stop using the affected device if possible. Save recovered data to a
different physical device; writing to the source can overwrite recoverable
data.

### Scan a disk image

```powershell
python recover.py "D:\images\damaged-drive.img" --output "E:\recovered"
```

### Scan a physical drive on Windows

Open PowerShell as Administrator, identify the correct disk in Windows Disk
Management, and pass its physical-drive path. Double-check the drive number:

```powershell
python recover.py "\\.\PhysicalDrive1" --output "E:\recovered"
```

The source is opened in binary read-only mode. Administrator access may be
required to read a physical device. The destination should be on a separate
device with enough free space.

Set a smaller upper bound for individual recovered files when needed:

```powershell
python recover.py "D:\images\card.img" --output "E:\recovered" --max-file-size 536870912
```

## Browser website

Run the local website from the project directory:

```powershell
python web.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser. Keep the
terminal window open while scanning; press `Ctrl+C` there to stop the server.
Enter a disk image or device path and a destination folder on a different
physical device. The interface shows the scan results and provides downloads
for files recovered in that session.

The website binds only to the local computer. Disk data and recovered files
are processed locally; they are not uploaded to a hosted service. Physical
device access may require starting the server from an Administrator terminal.

## Limitations and safety

- This is **file carving**, not filesystem undelete. It searches raw bytes for
  known signatures and cannot restore original filenames, folder structure,
  or timestamps.
- Recovery is best-effort. Fragmented files, overwritten data, unsupported
  formats, encryption, and SSD TRIM may prevent recovery or produce incomplete
  files. A matching signature does not guarantee that the recovered file opens.
- Do not scan a device that is physically failing if its data is important.
  Create a sector-by-sector image first, ideally with a tool designed to handle
  read errors, and scan the image instead.
- The tool does not automatically select devices. Verify the source and
  destination carefully before running it.

## Tests

Run the standard-library test suite from the project directory:

```powershell
python -m unittest discover -s tests -v
```

## Project structure

```text
recover.py          Read-only scanner and file carver
tests/test_recover.py
web.py              Loopback-only browser interface
static/             Website page, styles, and browser code
tests/test_web.py
README.md
LICENSE
```

## License

MIT; see [LICENSE](LICENSE).
