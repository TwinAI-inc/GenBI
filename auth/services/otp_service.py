"""
OTP generation, hashing, and verification.
"""

import secrets
import bcrypt as _bcrypt


def generate_otp() -> str:
    """Return a 6-digit OTP string."""
    return str(secrets.randbelow(900000) + 100000)


def hash_otp(otp: str) -> str:
    """Bcrypt-hash an OTP code (cost 12)."""
    return _bcrypt.hashpw(otp.encode(), _bcrypt.gensalt(rounds=12)).decode()


def verify_otp(otp: str, hashed: str) -> bool:
    """Check an OTP against its bcrypt hash."""
    return _bcrypt.checkpw(otp.encode(), hashed.encode())


def mask_email(email: str) -> str:
    """Mask email for display: r***s@gmail.com"""
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        masked = local[0] + '***'
    else:
        masked = local[0] + '***' + local[-1]
    return f'{masked}@{domain}'
