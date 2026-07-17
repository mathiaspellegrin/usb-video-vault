"""Shared helpers to read/write the password-protected video vault.

Each video is its own small AES-encrypted file inside the vault/ folder (named
v1.dat, v2.dat, ...) instead of one big archive -- FAT32 caps any single file
at ~4GB, and one huge combined vault would hit that ceiling for the whole
collection. Per-file storage means the cap applies per video instead.

A small encrypted index.dat file inside vault/ maps anonymous name -> real
name, so browsing the folder without the password reveals only meaningless
filenames, never the real video titles.
"""
import json
import os

import pyzipper

INDEX_NAME = "index.dat"
_ENTRY = "d"  # the single member name inside each per-file AES zip


def _zip_read_single(path: str, password: bytes) -> bytes:
    with pyzipper.AESZipFile(path) as zf:
        zf.setpassword(password)
        try:
            return zf.read(_ENTRY)
        except RuntimeError:
            raise ValueError("wrong password")


def _zip_write_single(path: str, password: bytes, data: bytes) -> None:
    tmp = path + ".tmp"
    with pyzipper.AESZipFile(tmp, "w", encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password)
        zf.writestr(_ENTRY, data, compress_type=pyzipper.ZIP_STORED)
    os.replace(tmp, path)


def _load_index(vault_dir: str, password: bytes) -> dict:
    path = os.path.join(vault_dir, INDEX_NAME)
    if not os.path.isfile(path):
        return {"names": {}, "next": 1}
    return json.loads(_zip_read_single(path, password))


def _save_index(vault_dir: str, password: bytes, index: dict) -> None:
    _zip_write_single(os.path.join(vault_dir, INDEX_NAME), password, json.dumps(index).encode())


def list_entries(vault_dir: str, password: bytes) -> list:
    index = _load_index(vault_dir, password)
    return sorted(index["names"].values())


def extract_to(vault_dir: str, password: bytes, name: str, dest_path: str) -> None:
    index = _load_index(vault_dir, password)
    anon = next((a for a, n in index["names"].items() if n == name), None)
    if anon is None:
        raise ValueError(f"{name} not found in vault")
    data = _zip_read_single(os.path.join(vault_dir, anon), password)
    with open(dest_path, "wb") as f:
        f.write(data)


def put(vault_dir: str, password: bytes, name: str, src_path: str) -> None:
    """Add or replace one video (by real name)."""
    os.makedirs(vault_dir, exist_ok=True)
    index = _load_index(vault_dir, password)
    anon = next((a for a, n in index["names"].items() if n == name), None)
    if anon is None:
        anon = f"v{index['next']}.dat"
        index["next"] += 1
    index["names"][anon] = name

    with open(src_path, "rb") as f:
        data = f.read()
    _zip_write_single(os.path.join(vault_dir, anon), password, data)
    _save_index(vault_dir, password, index)


def _selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        vault_dir = os.path.join(d, "vault")
        src = os.path.join(d, "a.txt")
        with open(src, "w") as f:
            f.write("hello")

        put(vault_dir, b"secret", "a.txt", src)
        assert list_entries(vault_dir, b"secret") == ["a.txt"]

        on_disk = set(os.listdir(vault_dir))
        assert "a.txt" not in on_disk, on_disk
        assert INDEX_NAME in on_disk

        out = os.path.join(d, "out.txt")
        extract_to(vault_dir, b"secret", "a.txt", out)
        assert open(out).read() == "hello"

        try:
            list_entries(vault_dir, b"wrong")
            raise AssertionError("wrong password should have failed")
        except ValueError:
            pass

        src2 = os.path.join(d, "b.txt")
        with open(src2, "w") as f:
            f.write("world")
        put(vault_dir, b"secret", "a.txt", src2)
        assert list_entries(vault_dir, b"secret") == ["a.txt"]
        extract_to(vault_dir, b"secret", "a.txt", out)
        assert open(out).read() == "world"
        assert len([n for n in os.listdir(vault_dir) if n != INDEX_NAME]) == 1
    print("vaultlib selftest OK")


if __name__ == "__main__":
    _selftest()
