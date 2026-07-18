# USB Video Vault

A small desktop app for keeping a password-protected collection of videos on
a USB drive: it shrinks `.mp4` files (H.265 re-encode) so more fit, and
encrypts each one (AES-256) so the drive is unreadable without a password.

## Why

- **Space**: H.265 re-encoding typically cuts file size by 60-80% with
  similar visual quality.
- **Privacy**: without the password, nobody can read the video content, and
  can't even see the real filenames (they're stored under anonymous names
  like `v1.dat`; a small encrypted index inside the vault maps them back to
  the real name once you unlock it).
- **FAT32-friendly**: FAT32 (common on USB sticks) caps any single file at
  ~4GB. Each video is its own encrypted file, so that cap applies per video,
  not to your whole collection.

## Requirements

- Python 3.9+ with Tk (`python3-tk` on some Linux distros, e.g. `apt install
  python3-tk`)
- [ffmpeg](https://ffmpeg.org/) on your PATH (`apt`/`brew`/`choco install
  ffmpeg`)
- `pip install -r requirements.txt` (just [pyzipper](https://pypi.org/project/pyzipper/), for AES-256 zip encryption)

## Usage

Double-click `vault_app.py` (or `Vault.command` on macOS). First run asks
you to set a vault password; later runs ask for it to unlock.

- **Watch**: pick a video from the list, click Watch (or double-click it).
  It's decrypted to a temp file, opened in your system's default player, and
  the temp copy is deleted once you close the confirmation dialog.
- **Add videos**: drop `.mp4` files into `incoming/`, click **Scan
  incoming/**. If it finds anything, the **Compress** button turns red with
  a count -- click it to actually re-encode and encrypt them into the vault.
  Compressing freezes the window until ffmpeg finishes (there's no progress
  bar, just a status line), which is why scanning and compressing are
  separate, deliberate steps instead of automatic.
- Dropping a video with a name that's already in the vault prompts you to
  replace it, rename the new one, or skip it.

Everything lives in the `vault/` folder once added; the password is kept in
memory only for as long as the app window is open, never written to disk.

## Security notes

- Video content is genuinely AES-256 encrypted (via `pyzipper`), not just
  zipped.
- Filenames are anonymized on disk; only the encrypted index (unlockable
  with the password) maps them back to real names.
- Standard zip-AES password-based key derivation uses relatively few KDF
  iterations compared to modern password hashes (Argon2/bcrypt) -- use a
  long passphrase, not a short password, since an attacker with a copy of
  the vault could brute-force a weak one.
- **There is no password recovery.** Forgetting it means the videos are
  permanently unrecoverable by design -- there's no backdoor.

## Known limitations

- ffmpeg runs synchronously, so the window is unresponsive while compressing
  a video (see Usage above).
- Every `put()` into the vault only touches that one video's file -- fast --
  but there's no dedup/integrity check beyond the zip's own CRC.
- No built-in backup: if the drive is lost, stolen, or dies, the videos go
  with it. Back up the `vault/` folder yourself if that matters to you.
- Tested primarily on Linux; the Windows/macOS paths in `watch_selected()`
  (`os.startfile` / `open`) are implemented but not exercised in CI.
- FAT32 isn't journaled: unplugging the drive mid-write (during a compress or
  while the vault is being updated) can corrupt the filesystem itself, not
  just the file being written. Always close the app and eject/unmount the
  drive properly before disconnecting it.

## Development

`vaultlib.py` has a self-test covering the core read/write/wrong-password
paths:

```
python3 vaultlib.py
```

`test_integration.py` exercises the real pipeline end to end: generates a
synthetic test clip, compresses it with the same ffmpeg command the app
uses, stores and reads it back through the vault, and confirms the result
is still a playable video. Skips itself if ffmpeg isn't installed.

```
python3 test_integration.py
```

## License

MIT, see [LICENSE](LICENSE).
