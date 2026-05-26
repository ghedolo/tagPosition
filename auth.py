import sys
import os

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "GoogleFindMyTools")
sys.path.insert(0, LIB_DIR)

from Auth.auth_flow import request_oauth_account_token_flow
from Auth.aas_token_retrieval import get_aas_token
from Auth.fcm_receiver import FcmReceiver
from KeyBackup.shared_key_retrieval import get_shared_key


def main():
    print("tagPosition — Google Find Hub authentication")
    print("=" * 50)
    print()
    print("Step 1/3: OAuth token (Chrome will open — log in to your Google account)")
    request_oauth_account_token_flow()

    print("\nStep 2/3: AAS token + FCM registration...")
    get_aas_token()
    FcmReceiver().get_android_id()

    print("\nStep 3/3: E2EE shared key (Chrome will open again — log in and wait)")
    get_shared_key()

    secrets_path = os.path.join(LIB_DIR, "Auth", "secrets.json")
    print(f"\n[Auth] All steps complete. Credentials saved to: {secrets_path}")
    print("[Auth] You can now run: python poller.py")


if __name__ == "__main__":
    main()
