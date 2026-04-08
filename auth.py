"""
auth.py — JWT authentication for Ghost.

Each device has its own token. Tokens are 30-day, signed with JWT_SECRET.

Usage from terminal:
  python auth.py generate samsung        # generate token for one device
  python auth.py generate-all            # generate tokens for all devices
  python auth.py verify <token>          # check if a token is valid
"""

import sys
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config

# FastAPI's built-in Bearer token extractor
# Looks for "Authorization: Bearer <token>" header on every request
_bearer = HTTPBearer()


def generate_token(device_id: str) -> str:
    """
    Create a signed JWT token for a device.

    The token contains:
      sub — subject, who this token belongs to (e.g. "samsung")
      iat — issued at, when it was created
      exp — expiry, when it stops working (30 days from now)

    All of this is signed with JWT_SECRET so it cannot be faked.
    """
    now = datetime.now(timezone.utc)

    payload = {
        "sub": device_id,
        "iat": now,
        "exp": now + timedelta(days=config.JWT_EXPIRE_DAYS),
    }

    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT token.

    Checks:
      1. Token was signed with our JWT_SECRET (not forged)
      2. Token has not expired
      3. Token structure is valid

    Returns the payload dict if valid.
    Raises HTTPException 401 if anything is wrong.
    """
    try:
        payload = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM]
        )
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


async def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(_bearer)
) -> str:
    """
    FastAPI dependency — plugs into any route with Depends(verify_token).

    FastAPI automatically:
      1. Extracts the Bearer token from the Authorization header
      2. Passes it to this function
      3. Injects the return value into the route function

    Returns the device_id (e.g. "samsung") if token is valid.
    Raises 401 if token is missing, expired, or forged.

    Usage in a route:
      @app.post("/command")
      async def command(device_id: str = Depends(verify_token)):
          # device_id is "samsung", "macbook" etc
          ...
    """
    payload = decode_token(credentials.credentials)

    device_id = payload.get("sub")
    if not device_id:
        raise HTTPException(status_code=401, detail="Token missing device ID")

    return device_id


# ── CLI tool — run this directly to generate tokens ───────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("\nUsage:")
        print("  python auth.py generate <device>     generate token for one device")
        print("  python auth.py generate-all          generate tokens for all devices")
        print("  python auth.py verify <token>        verify a token\n")
        sys.exit(1)

    command = sys.argv[1]

    if command == "generate":
        if len(sys.argv) < 3:
            print("Error: provide a device name — samsung, macbook, thinkpad, iphone")
            sys.exit(1)
        device = sys.argv[2]
        token = generate_token(device)
        print(f"\nToken for [{device}]:")
        print(token)
        print(f"\nAdd this to your .env:")
        print(f"  DEVICE_TOKEN_{device.upper()}={token}\n")

    elif command == "generate-all":
        devices = ["samsung", "macbook", "thinkpad", "iphone"]
        print("\nAdd these to your .env:\n")
        for device in devices:
            token = generate_token(device)
            print(f"DEVICE_TOKEN_{device.upper()}={token}")
        print()

    elif command == "verify":
        if len(sys.argv) < 3:
            print("Error: provide a token to verify")
            sys.exit(1)
        try:
            payload = decode_token(sys.argv[2])
            print(f"\nValid token")
            print(f"  Device:  {payload['sub']}")
            print(f"  Expires: {payload['exp']}\n")
        except HTTPException as e:
            print(f"\nInvalid token: {e.detail}\n")