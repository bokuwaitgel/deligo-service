"""Generate the VAPID key pair web push is signed with.

Run once per environment and put the output in the deployment env file:

    python scripts/generate_vapid_keys.py

Output is the raw base64url encoding both ends expect — the public key goes to
the browser verbatim as ``applicationServerKey``, the private key to
``pywebpush`` as ``vapid_private_key``.

Rotating the keys invalidates every stored subscription: browsers bind a
subscription to the application server key it was created with, and pushes
signed by a new key are rejected with 403. After a rotation, truncate
``push_subscriptions`` and let clients resubscribe.
"""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _b64url(raw: bytes) -> str:
    """base64url without padding — what the Web Push spec and py_vapid use."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate() -> tuple[str, str]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_raw = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = private_key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    return _b64url(public_raw), _b64url(private_raw)


if __name__ == "__main__":
    public, private = generate()
    print("# Web Push (VAPID) — add to your env file")
    print(f"VAPID_PUBLIC_KEY={public}")
    print(f"VAPID_PRIVATE_KEY={private}")
    print("VAPID_SUBJECT=mailto:admin@deligo.mn")
    print()
    print("# Frontend build arg — same public key, exposed to the browser")
    print(f"NEXT_PUBLIC_VAPID_PUBLIC_KEY={public}")
