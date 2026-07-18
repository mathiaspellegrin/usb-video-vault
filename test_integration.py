#!/usr/bin/env python3
"""End-to-end check of the real pipeline: ffmpeg compress -> vault store ->
vault read back -> played bytes match. vaultlib.py's own self-test only
covers the encryption logic in isolation; this exercises the actual ffmpeg
command vault_app.py runs, against a real (synthetic) video file.

Skips itself if ffmpeg isn't installed rather than failing -- this is a dev
convenience check, not a CI gate.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import vaultlib

CRF = 28


def main() -> None:
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found, skipping integration test")
        return

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        source = d / "source.mp4"
        vault_dir = d / "vault"

        # 2-second synthetic test clip -- no camera/real footage needed
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
             "-pix_fmt", "yuv420p", str(source)],
            check=True, capture_output=True, text=True,
        )
        assert source.stat().st_size > 0, "test source video wasn't generated"

        compressed = d / "compressed.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(source),
             "-vcodec", "libx265", "-crf", str(CRF), "-preset", "medium",
             "-tag:v", "hvc1", "-c:a", "copy", str(compressed)],
            check=True, capture_output=True, text=True,
        )
        assert compressed.stat().st_size > 0, "ffmpeg produced an empty file"

        password = b"test-password"
        vaultlib.put(str(vault_dir), password, "clip.mp4", str(compressed))
        assert vaultlib.list_entries(str(vault_dir), password) == ["clip.mp4"]

        # on-disk filename must not reveal the real one
        on_disk = [p.name for p in vault_dir.iterdir()]
        assert "clip.mp4" not in on_disk, on_disk

        out = d / "extracted.mp4"
        vaultlib.extract_to(str(vault_dir), password, "clip.mp4", str(out))
        assert out.read_bytes() == compressed.read_bytes(), "extracted bytes don't match what was stored"

        # the extracted file must still be a playable mp4, not corrupted bytes
        probe = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(out), "-f", "null", "-"],
            capture_output=True, text=True,
        )
        assert probe.returncode == 0, f"ffmpeg couldn't read the extracted video back:\n{probe.stderr}"

    print("integration test OK")


if __name__ == "__main__":
    main()
