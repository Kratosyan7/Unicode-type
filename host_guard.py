"""
Host-bound payload loader.

The real application code lives in encrypted .enc blobs that can only be
decrypted on devices whose hostname matches one of the allowed names embedded
at pack time. Anyone can read this loader, but without an approved hostname
the AEAD check fails and the program exits.

Usage:
    # Run an encrypted payload (called from macos.py / windows.py):
    import host_guard
    host_guard.run("macos.enc")

    # Pack a plaintext source into an encrypted blob (developer-side):
    python3 host_guard.py pack <src.py> <out.enc> "Hostname-1" "Hostname-2" ...
"""

import hashlib
import hmac
import os
import platform
import secrets
import socket
import struct
import subprocess
import sys
from pathlib import Path

_PEPPER = b"utype::a7f3-9bd2-c4e1-2025-host-guard"
_MAGIC = b"UTYP1"


def _normalize(name):
    if not name:
        return ""
    name = name.strip()
    if name.lower().endswith(".local"):
        name = name[:-6]
    return name.casefold()


def _host_key(hostname_norm):
    return hashlib.sha256(_PEPPER + b":host:" + hostname_norm.encode("utf-8")).digest()


def _keystream(key, nbytes, domain):
    out = bytearray()
    counter = 0
    while len(out) < nbytes:
        block = hmac.new(
            key,
            domain + struct.pack(">Q", counter),
            hashlib.sha256,
        ).digest()
        out += block
        counter += 1
    return bytes(out[:nbytes])


def _mac(key, data, domain):
    return hmac.new(key, domain + data, hashlib.sha256).digest()


def _encrypt(key, plaintext, stream_domain, mac_domain):
    ks = _keystream(key, len(plaintext), stream_domain)
    ct = bytes(p ^ k for p, k in zip(plaintext, ks))
    tag = _mac(key, ct, mac_domain)
    return tag + ct


def _decrypt(key, blob, stream_domain, mac_domain):
    if len(blob) < 32:
        return None
    tag, ct = blob[:32], blob[32:]
    if not hmac.compare_digest(tag, _mac(key, ct, mac_domain)):
        return None
    ks = _keystream(key, len(ct), stream_domain)
    return bytes(c ^ k for c, k in zip(ct, ks))


def _current_hostnames():
    names = set()
    try:
        names.add(socket.gethostname())
    except OSError:
        pass

    system = platform.system()
    if system == "Darwin":
        for key in ("ComputerName", "LocalHostName", "HostName"):
            try:
                out = subprocess.run(
                    ["scutil", "--get", key],
                    capture_output=True, text=True, timeout=2,
                )
                value = out.stdout.strip()
                if value:
                    names.add(value)
            except (OSError, subprocess.SubprocessError):
                pass
    elif system == "Windows":
        env = os.environ.get("COMPUTERNAME")
        if env:
            names.add(env)

    return {n for n in names if n}


def pack(plaintext_bytes, allowed_hostnames):
    """Encrypt `plaintext_bytes` so it can be decrypted on any of the given hosts."""
    if not allowed_hostnames:
        raise ValueError("allowed_hostnames must be non-empty")

    data_key = secrets.token_bytes(32)
    payload_blob = _encrypt(
        data_key, plaintext_bytes,
        stream_domain=b"payload-stream",
        mac_domain=b"payload-mac",
    )

    wrapped = []
    for name in allowed_hostnames:
        host_key = _host_key(_normalize(name))
        wrap = _encrypt(
            host_key, data_key,
            stream_domain=b"wrap-stream",
            mac_domain=b"wrap-mac",
        )
        wrapped.append(wrap)

    out = bytearray()
    out += _MAGIC
    out += struct.pack(">I", len(payload_blob))
    out += payload_blob
    out += struct.pack(">I", len(wrapped))
    for w in wrapped:
        out += struct.pack(">I", len(w))
        out += w
    return bytes(out)


def _parse_blob(blob):
    if not blob.startswith(_MAGIC):
        raise ValueError("bad magic")
    pos = len(_MAGIC)
    (plen,) = struct.unpack(">I", blob[pos:pos + 4]); pos += 4
    payload = blob[pos:pos + plen]; pos += plen
    (n,) = struct.unpack(">I", blob[pos:pos + 4]); pos += 4
    wrapped = []
    for _ in range(n):
        (wlen,) = struct.unpack(">I", blob[pos:pos + 4]); pos += 4
        wrapped.append(blob[pos:pos + wlen]); pos += wlen
    return payload, wrapped


def _try_unwrap(host_key, wrapped_list):
    for w in wrapped_list:
        dk = _decrypt(host_key, w, b"wrap-stream", b"wrap-mac")
        if dk is not None:
            return dk
    return None


def _deny():
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Доступ запрещён",
            "Это устройство не входит в список доверенных.",
        )
        root.destroy()
    except Exception:
        pass
    sys.exit(1)


def run(enc_filename):
    blob_path = Path(__file__).with_name(enc_filename)
    try:
        blob = blob_path.read_bytes()
        payload_blob, wrapped = _parse_blob(blob)
    except (OSError, ValueError, struct.error):
        _deny()
        return

    for hostname in _current_hostnames():
        host_key = _host_key(_normalize(hostname))
        dk = _try_unwrap(host_key, wrapped)
        if dk is None:
            continue
        plaintext = _decrypt(dk, payload_blob, b"payload-stream", b"payload-mac")
        if plaintext is None:
            continue
        ns = {"__name__": "__main__", "__file__": str(blob_path)}
        exec(compile(plaintext, enc_filename, "exec"), ns)
        return

    _deny()


def _cli_pack(argv):
    if len(argv) < 3:
        print("Usage: pack <src.py> <out.enc> <hostname1> [<hostname2> ...]")
        sys.exit(2)
    src_path = Path(argv[0])
    out_path = Path(argv[1])
    hostnames = argv[2:]
    plaintext = src_path.read_bytes()
    blob = pack(plaintext, hostnames)
    out_path.write_bytes(blob)
    print(f"packed {src_path} -> {out_path} ({len(blob)} bytes, {len(hostnames)} host(s))")


def _cli_digest(argv):
    for arg in argv:
        norm = _normalize(arg)
        digest = hashlib.sha256(_PEPPER + b":host:" + norm.encode("utf-8")).hexdigest()
        print(f"{arg!r}  norm={norm!r}  key_sha256={digest}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "pack":
        _cli_pack(sys.argv[2:])
    elif len(sys.argv) >= 2 and sys.argv[1] == "digest":
        _cli_digest(sys.argv[2:])
    else:
        print("Commands: pack <src.py> <out.enc> <hostnames...>")
        print("          digest <hostname> [<hostname> ...]")
        sys.exit(2)
