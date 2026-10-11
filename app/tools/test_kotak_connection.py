"""
Kotak Neo API Connection & Authentication Test Utility.
Based on official Kotak Neo Python SDK (neo_api_client v3.0.7).
Repository: https://github.com/Kotak-Neo/Kotak-neo-api-v2
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv, set_key

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

try:
    import pyotp
    HAS_PYOTP = True
except ImportError:
    HAS_PYOTP = False

try:
    from neo_api_client import NeoAPI
    HAS_NEO_API = True
except ImportError:
    HAS_NEO_API = False

# Tell pytest this is a CLI tool, not a test suite
__test__ = False


def run_kotak_connection_test(
    consumer_key: str,
    mobile_number: str,
    ucc: str,
    mpin: str,
    totp_or_secret: str,
    environment: str = "prod",
    save_to_env: bool = True,
):
    print("=" * 70)
    print(" [*] KOTAK NEO API CONNECTION TEST UTILITY")
    print("=" * 70)

    if not HAS_NEO_API:
        print(" [!] Error: 'neo_api_client' is not installed. Run: pip install kotakneoapi")
        return False

    # Normalize mobile number: Kotak Neo API requires country code (e.g., +919876543210)
    clean_mob = mobile_number.strip().replace(" ", "").replace("-", "")
    if len(clean_mob) == 10 and clean_mob.isdigit():
        clean_mob = f"+91{clean_mob}"
    elif len(clean_mob) == 12 and clean_mob.startswith("91") and clean_mob.isdigit():
        clean_mob = f"+{clean_mob}"

    print(f"[*] Environment: {environment}")
    print(f"[*] Consumer Key: {consumer_key[:6]}...{consumer_key[-4:] if len(consumer_key) > 10 else ''}")
    print(f"[*] Mobile Number: {clean_mob[:5]}******{clean_mob[-2:] if len(clean_mob) >= 4 else ''}")
    print(f"[*] UCC (Client ID): {ucc}")

    # Determine TOTP
    clean_totp_input = totp_or_secret.strip().replace(" ", "")
    current_totp = ""
    if len(clean_totp_input) == 6 and clean_totp_input.isdigit():
        current_totp = clean_totp_input
        print(f"[*] Using provided 6-digit TOTP: {current_totp}")
    else:
        # Treat as Base32 TOTP Secret
        if HAS_PYOTP:
            try:
                totp_gen = pyotp.TOTP(clean_totp_input)
                current_totp = totp_gen.now()
                print(f"[+] Successfully generated TOTP from Secret: {current_totp}")
            except Exception as e:
                print(f"[-] Failed to generate TOTP from secret: {e}")
                return False
        else:
            print("[-] Error: pyotp required for secret generation. Provide 6-digit TOTP directly.")
            return False

    print("\n[Step 1] Initializing NeoAPI client...")
    try:
        client = NeoAPI(
            consumer_key=consumer_key,
            environment=environment,
        )
        print(" [+] NeoAPI Client initialized.")
    except Exception as e:
        print(f" [-] Failed to initialize NeoAPI Client: {e}")
        return False

    print("\n[Step 2] Executing 2FA TOTP Login (totp_login)...")
    try:
        login_resp = client.totp_login(
            mobile_number=clean_mob,
            ucc=ucc,
            totp=current_totp,
        )
        print(f" [+] TOTP Login Response: {json.dumps(login_resp, indent=2)}")
        
        # Check if error or status failure
        if isinstance(login_resp, dict) and login_resp.get("error"):
            print(f" [-] TOTP Login Rejected: {login_resp.get('error')}")
            return False
    except Exception as e:
        print(f" [-] Exception during totp_login: {e}")
        return False

    print("\n[Step 3] Validating 6-digit MPIN (totp_validate)...")
    trade_token = ""
    session_id = ""
    try:
        val_resp = client.totp_validate(mpin=mpin)
        print(f" [+] MPIN Validation Response: {json.dumps(val_resp, indent=2)}")
        
        if isinstance(val_resp, dict) and val_resp.get("error"):
            print(f" [-] MPIN Validation Rejected: {val_resp.get('error')}")
            return False
        
        if isinstance(val_resp, dict) and "data" in val_resp:
            trade_token = val_resp["data"].get("token", "")
            session_id = val_resp["data"].get("sid", "")
    except Exception as e:
        print(f" [-] Exception during totp_validate: {e}")
        return False

    print("\n[Step 4] Querying Account Limits & Available Margin...")
    try:
        limits_resp = client.limits()
        print(f" [+] Account Limits: {json.dumps(limits_resp, indent=2)}")
    except Exception as e:
        print(f" [!] Warning: limits() query had note: {e}")

    print("\n[Step 5] Querying Positions Book...")
    try:
        pos_resp = client.positions()
        print(f" [+] Positions Response: {json.dumps(pos_resp, indent=2)}")
    except Exception as e:
        print(f" [!] Warning: positions() query had note: {e}")

    print("\n" + "=" * 70)
    print(" [SUCCESS] ALL CHECKS PASSED: Kotak Neo Connection Established Successfully!")
    print("=" * 70)

    if save_to_env:
        env_path = PROJECT_ROOT / ".env"
        print(f"[+] Persisting Kotak configuration to {env_path}...")
        set_key(env_path, "KOTAK_CONSUMER_KEY", consumer_key)
        set_key(env_path, "KOTAK_MOBILE_NUMBER", clean_mob)
        set_key(env_path, "KOTAK_UCC", ucc)
        set_key(env_path, "KOTAK_MPIN", mpin)
        if len(clean_totp_input) > 6:
            set_key(env_path, "KOTAK_TOTP_SECRET", clean_totp_input)
        if trade_token:
            set_key(env_path, "KOTAK_SESSION_TOKEN", trade_token)
        if session_id:
            set_key(env_path, "KOTAK_SESSION_ID", session_id)
        print(" [+] Configuration successfully saved to .env!")

    return True


def interactive_cli():
    load_dotenv()
    default_key = os.getenv("KOTAK_CONSUMER_KEY", "")
    default_mobile = os.getenv("KOTAK_MOBILE_NUMBER", "")
    default_ucc = os.getenv("KOTAK_UCC", "")
    default_mpin = os.getenv("KOTAK_MPIN", "")
    default_totp_secret = os.getenv("KOTAK_TOTP_SECRET", "")

    print("\n" + "=" * 70)
    print(" [*] KOTAK NEO API CREDENTIALS SETUP")
    print("=" * 70)

    ck = input(f"1. Consumer Key / App Token [{default_key}]: ").strip() or default_key
    mob = input(f"2. Registered Mobile Number [{default_mobile}]: ").strip() or default_mobile
    ucc = input(f"3. UCC (User Client Code / Client ID) [{default_ucc}]: ").strip() or default_ucc
    mpin = input(f"4. 6-digit MPIN [{default_mpin}]: ").strip() or default_mpin
    
    totp_prompt = "5. Current 6-digit TOTP (from Authenticator) OR TOTP Secret Key"
    if default_totp_secret:
        totp_prompt += f" [{default_totp_secret[:4]}...{default_totp_secret[-4:]}]"
    totp_val = input(f"{totp_prompt}: ").strip() or default_totp_secret

    if not all([ck, mob, ucc, mpin, totp_val]):
        print("\n[-] Error: All 5 parameters are required for Kotak Neo 2FA authentication.")
        return

    run_kotak_connection_test(
        consumer_key=ck,
        mobile_number=mob,
        ucc=ucc,
        mpin=mpin,
        totp_or_secret=totp_val,
        save_to_env=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Kotak Neo API Connection")
    parser.add_argument("--key", help="Kotak Neo Consumer Key / App Token")
    parser.add_argument("--mobile", help="Registered Mobile Number")
    parser.add_argument("--ucc", help="User Client Code (UCC)")
    parser.add_argument("--mpin", help="6-digit MPIN")
    parser.add_argument("--totp", help="Current 6-digit TOTP or TOTP Secret Key")
    args = parser.parse_args()

    if any([args.key, args.mobile, args.ucc, args.mpin, args.totp]):
        load_dotenv()
        key = args.key or os.getenv("KOTAK_CONSUMER_KEY", "")
        mobile = args.mobile or os.getenv("KOTAK_MOBILE_NUMBER", "")
        ucc = args.ucc or os.getenv("KOTAK_UCC", "")
        mpin = args.mpin or os.getenv("KOTAK_MPIN", "")
        totp = args.totp or os.getenv("KOTAK_TOTP_SECRET", "")
        run_kotak_connection_test(key, mobile, ucc, mpin, totp)
    else:
        interactive_cli()
