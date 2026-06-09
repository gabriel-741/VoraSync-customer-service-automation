#app/utils/webhook_security.py

import hmac
import hashlib



def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False

    if isinstance(body, str):
        body = body.encode()

    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    signature = signature.replace("sha256=", "").strip()

    return hmac.compare_digest(expected, signature)