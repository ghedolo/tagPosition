#!/usr/bin/env python3
"""Patch the vendored GoogleFindMyTools submodule: restrict secrets.json permissions.

Upstream ``Auth/token_cache.py`` writes ``secrets.json`` with the process umask,
which on a normal system gives mode 644 — world readable. The file holds the AAS
token, the FCM credentials and the E2EE shared key, i.e. everything needed to read
the position of every tracker on the account.

After the patch the file is created with mode 600 and an existing file is chmod'ed
to 600 on every write.

Idempotent: re-running on an already patched file is a no-op.
Run after every ``git submodule update``.

    python patches/perms_patch.py
"""

import os
import sys

DEFAULT_PATH = "lib/GoogleFindMyTools/Auth/token_cache.py"

OLD_WRITE = """    with open(secrets_file, 'w') as file:
        json.dump(data, file)
"""

NEW_WRITE = """    # secrets.json holds the AAS token, FCM credentials and the E2EE shared key:
    # create it with mode 600 and fix the mode of a pre-existing file.
    fd = os.open(secrets_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(data, file)
    os.chmod(secrets_file, 0o600)
"""


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    try:
        src = open(path).read()
    except OSError as ex:
        print("cannot read %s: %s" % (path, ex), file=sys.stderr)
        print("run from the repo root, after 'git submodule update --init'",
              file=sys.stderr)
        return 1

    if "os.chmod(secrets_file, 0o600)" in src:
        print("already patched: %s" % path)
    else:
        if src.count(OLD_WRITE) != 1:
            print("secrets.json write block not found in %s" % path, file=sys.stderr)
            return 1
        src = src.replace(OLD_WRITE, NEW_WRITE, 1)
        open(path, "w").write(src)
        print("patched: %s" % path)

    # an already existing secrets.json keeps the old permissions: fix it now
    secrets = os.path.join(os.path.dirname(path), "secrets.json")
    if os.path.exists(secrets):
        os.chmod(secrets, 0o600)
        print("chmod 600: %s" % secrets)

    return 0


if __name__ == "__main__":
    sys.exit(main())
