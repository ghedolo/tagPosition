#!/usr/bin/env python3
"""Patch the vendored GoogleFindMyTools submodule.

Fixes FCM push decryption: the upstream code assumes the ``crypto-key`` and
``encryption`` app_data headers always start with ``dh=`` / ``salt=`` and are
base64url padded. When they are not, decryption fails with
``binascii.Error: Incorrect padding`` or ``ValueError: Invalid EC key.``.

Idempotent: re-running on an already patched file is a no-op.
Run after every ``git submodule update``.

    python patches/fcm_patch.py
"""

import re
import sys

DEFAULT_PATH = "lib/GoogleFindMyTools/Auth/firebase_messaging/fcmpushclient.py"

HELPER = '''    @staticmethod
    def _decode_header_param(value: str, prefix: str) -> bytes:
        """Decode a base64url param from an FCM header value.

        Handles values with or without the ``dh=``/``salt=`` prefix, multiple
        semicolon/comma separated params, optional quoting and missing padding.
        """
        raw = value.strip()
        for part in re.split(r"[;,]", raw):
            part = part.strip()
            if part.startswith(prefix):
                raw = part[len(prefix):]
                break
        else:
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        raw = raw.strip('"').strip()
        return urlsafe_b64decode(raw.encode("ascii") + b"=" * (-len(raw) % 4))

'''

DECRYPT_SIG = '''    def _decrypt_raw_data(
        credentials: dict[str, dict[str, str]],
        crypto_key_str: str,
        salt_str: str,
        raw_data: bytes,
    ) -> bytes:
'''

OLD_CALL = '''        decrypted = http_decrypt(
            raw_data,
            salt=salt,
            private_key=privkey,
            dh=crypto_key,
            version="aesgcm",
            auth_secret=secret,
        )
        return decrypted'''

NEW_CALL = '''        try:
            decrypted = http_decrypt(
                raw_data,
                salt=salt,
                private_key=privkey,
                dh=crypto_key,
                version="aesgcm",
                auth_secret=secret,
            )
        except Exception as ex:
            raise RuntimeError(
                "http_ece decrypt failed (%s): dh=%d bytes, salt=%d bytes, "
                "secret=%d bytes, raw_data=%d bytes"
                % (ex, len(crypto_key), len(salt), len(secret), len(raw_data))
            ) from ex
        return decrypted'''


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    try:
        src = open(path).read()
    except OSError as ex:
        print("cannot read %s: %s" % (path, ex), file=sys.stderr)
        print("run from the repo root, after 'git submodule update --init'",
              file=sys.stderr)
        return 1
    original = src

    # 1. helper method, inserted before _decrypt_raw_data
    if "_decode_header_param" not in src:
        if src.count(DECRYPT_SIG) != 1:
            print("_decrypt_raw_data signature not found", file=sys.stderr)
            return 1
        src = src.replace(DECRYPT_SIG, HELPER + "    @staticmethod\n" + DECRYPT_SIG)

    # 2. use the helper (matches both the pristine and the +b"========" variant)
    for pat, rep in (
        (r'crypto_key = urlsafe_b64decode\(crypto_key_str\.encode\("ascii"\)'
         r'(?: \+ b"=+")?\)',
         'crypto_key = FcmPushClient._decode_header_param(crypto_key_str, "dh=")'),
        (r'salt = urlsafe_b64decode\(salt_str\.encode\("ascii"\)(?: \+ b"=+")?\)',
         'salt = FcmPushClient._decode_header_param(salt_str, "salt=")'),
    ):
        src = re.sub(pat, rep, src)

    # 3. no blind prefix stripping in _handle_data_message
    src = src.replace(
        'crypto_key = self._app_data_by_key(msg, "crypto-key")[3:]  # strip dh=',
        'crypto_key = self._app_data_by_key(msg, "crypto-key")')
    src = src.replace(
        'salt = self._app_data_by_key(msg, "encryption")[5:]  # strip salt=',
        'salt = self._app_data_by_key(msg, "encryption")')

    # 4. report byte lengths when decryption fails (no key material logged)
    src = src.replace(OLD_CALL, NEW_CALL)

    # 5. the helper needs `re`
    if not re.search(r"^import re$", src, re.M):
        src = src.replace("import ssl\n", "import re\nimport ssl\n", 1)

    missing = [
        name for name, ok in (
            ("_decode_header_param helper", "_decode_header_param" in src),
            ("crypto_key decode", '_decode_header_param(crypto_key_str, "dh=")' in src),
            ("salt decode", '_decode_header_param(salt_str, "salt=")' in src),
            ("decrypt diagnostic", "http_ece decrypt failed" in src),
            ("re import", bool(re.search(r"^import re$", src, re.M))),
        ) if not ok
    ]
    if missing:
        print("patch incomplete: " + ", ".join(missing), file=sys.stderr)
        return 1

    if src == original:
        print("already patched: %s" % path)
        return 0

    open(path, "w").write(src)
    print("patched: %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
