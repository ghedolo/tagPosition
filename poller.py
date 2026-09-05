import sys
import os
import json
import time
import fcntl
import logging
import datetime
import hashlib
import argparse
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "GoogleFindMyTools")
sys.path.insert(0, LIB_DIR)

ARCHIVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "positions.json")
LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".poller.lock")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp", "poller.log")
PURGE_DAYS = 7

# Cron runs the poller every 15 minutes, so the log grows without an upper bound
# unless it is rotated. 5 MB x 5 backups caps it at ~30 MB on the Pi's SD card.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# Position history is personal data: keep it readable by the owner only.
DATA_DIR_MODE = 0o700
DATA_FILE_MODE = 0o600

from Auth.fcm_receiver import FcmReceiver
from Auth.token_cache import get_cached_value
from NovaApi.ExecuteAction.LocateTracker.location_request import create_location_request
from NovaApi.ExecuteAction.LocateTracker.decrypt_locations import retrieve_identity_key, is_mcu_tracker
from NovaApi.ExecuteAction.nbe_execute_action import create_action_request, serialize_action_request
from NovaApi.ListDevices.nbe_list_devices import request_device_list
from NovaApi.nova_request import nova_request
from NovaApi.scopes import NOVA_ACTION_API_SCOPE
from NovaApi.util import generate_random_uuid
from ProtoDecoders import Common_pb2, DeviceUpdate_pb2
from ProtoDecoders.decoder import parse_device_list_protobuf, parse_device_update_protobuf, get_canonic_ids
from KeyBackup.cloud_key_decryptor import decrypt_aes_gcm
from FMDNCrypto.foreign_tracker_cryptor import decrypt


def _secure_dir(path):
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, DATA_DIR_MODE)
    except OSError as ex:
        log.warning("[Poller] WARNING: cannot chmod %s: %s", path, ex)


def _secure_file(path):
    try:
        os.chmod(path, DATA_FILE_MODE)
    except OSError as ex:
        log.warning("[Poller] WARNING: cannot chmod %s: %s", path, ex)


class _DynamicStreamHandler(logging.StreamHandler):
    """StreamHandler that resolves sys.stdout/sys.stderr at emit time.

    A plain StreamHandler binds the stream object when it is built, at import
    time, so anything that replaces sys.stdout later (pytest's capsys, a shell
    redirect set up by a wrapper) would not see the records.
    """

    def __init__(self, stream_name):
        logging.Handler.__init__(self)
        self._stream_name = stream_name

    @property
    def stream(self):
        return getattr(sys, self._stream_name)

    @stream.setter
    def stream(self, value):
        pass


class _SecureRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that creates the log and its backups as 0600.

    The log carries tracker names, so it gets the same permissions as the
    position archive rather than whatever the ambient umask allows.
    """

    def _open(self):
        stream = super()._open()
        _secure_file(self.baseFilename)
        return stream


def _below_warning(record):
    return record.levelno < logging.WARNING


def _setup_console():
    """Attach the console handlers. Runs at import so every entry point logs."""
    log.setLevel(logging.INFO)
    log.propagate = False

    out = _DynamicStreamHandler("stdout")
    out.setLevel(logging.INFO)
    out.addFilter(_below_warning)
    out.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(out)

    err = _DynamicStreamHandler("stderr")
    err.setLevel(logging.WARNING)
    err.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(err)


def _setup_file_log(path=None):
    """Attach the rotating file handler. Called from main() only.

    Keeping it out of import time means importing poller.py (tests, tooling)
    never creates or writes the log file.
    """
    path = path or LOG_PATH
    _secure_dir(os.path.dirname(path))
    handler = _SecureRotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, delay=True
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    return handler


log = logging.getLogger("poller")
_setup_console()


@contextmanager
def _data_lock():
    _secure_dir(os.path.dirname(LOCK_PATH))
    with open(LOCK_PATH, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _ts_to_fname(ts_iso):
    return ts_iso.replace("-", "").replace(":", "").replace("T", "T")


def _purge():
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=PURGE_DAYS)

    with _data_lock():
        if not os.path.exists(ARCHIVE_PATH):
            log.info("[Poller] No data file found.")
            return

        recent = []
        old = []

        with open(ARCHIVE_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                loc_time_str = entry.get("location_time", "")
                try:
                    loc_time = datetime.datetime.fromisoformat(loc_time_str.replace("Z", "+00:00"))
                except ValueError:
                    recent.append(entry)
                    continue
                if loc_time < cutoff:
                    old.append(entry)
                else:
                    recent.append(entry)

        if not old:
            log.info("[Poller] Nothing to archive.")
            return

        old.sort(key=lambda e: e.get("location_time", ""))
        ts1 = _ts_to_fname(old[0]["location_time"])
        ts2 = _ts_to_fname(old[-1]["location_time"])
        data_dir = os.path.dirname(ARCHIVE_PATH)
        archive_name = f"position_{ts1}_{ts2}.json"
        archive_path = os.path.join(data_dir, archive_name)

        with open(archive_path, "w") as f:
            for entry in old:
                f.write(json.dumps(entry) + "\n")
        _secure_file(archive_path)

        with open(ARCHIVE_PATH, "w") as f:
            for entry in recent:
                f.write(json.dumps(entry) + "\n")
        _secure_file(ARCHIVE_PATH)

        log.info("[Poller] Archived %d entries -> %s", len(old), archive_name)
        log.info("[Poller] Kept %d entries in positions.json", len(recent))


def _extract_locations(device_update, tag_name):
    device_registration = device_update.deviceMetadata.information.deviceRegistration
    identity_key = retrieve_identity_key(device_registration)
    locations_proto = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    is_mcu = is_mcu_tracker(device_registration)

    recent_location = locations_proto.recentLocation
    recent_location_time = locations_proto.recentLocationTimestamp
    network_locations = list(locations_proto.networkLocations)
    network_locations_time = list(locations_proto.networkLocationTimestamps)

    if locations_proto.HasField("recentLocation"):
        network_locations.append(recent_location)
        network_locations_time.append(recent_location_time)

    results = []

    for loc, ts in zip(network_locations, network_locations_time):
        location_time = datetime.datetime.fromtimestamp(int(ts.seconds), datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        if loc.status == Common_pb2.Status.SEMANTIC:
            entry = {
                "polled_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tag": tag_name,
                "status": "SEMANTIC",
                "semantic_name": loc.semanticLocation.locationName,
                "location_time": location_time,
            }
            results.append(entry)
            continue

        encrypted_location = loc.geoLocation.encryptedReport.encryptedLocation
        public_key_random = loc.geoLocation.encryptedReport.publicKeyRandom

        try:
            if public_key_random == b"":
                if not encrypted_location:
                    log.warning("[Poller] WARNING: empty encrypted_location (own report) for %s", tag_name)
                    continue
                identity_key_hash = hashlib.sha256(identity_key).digest()
                decrypted_location = decrypt_aes_gcm(identity_key_hash, encrypted_location)
            else:
                # deviceTimeOffset may be 0 (field absent in proto) for some crowd reports;
                # fall back to the location timestamp and retry adjacent EID periods (1024s each).
                if is_mcu:
                    time_offset = 0
                else:
                    time_offset = loc.geoLocation.deviceTimeOffset or int(ts.seconds)
                decrypted_location = None
                for delta in (0, -1024, 1024):
                    try:
                        decrypted_location = decrypt(
                            identity_key, encrypted_location, public_key_random, time_offset + delta
                        )
                        break
                    except Exception:
                        continue
                if decrypted_location is None:
                    raise ValueError("MAC check failed")
        except Exception as e:
            own = public_key_random == b""
            log.warning(
                "[Poller] WARNING: could not decrypt location for %s "
                "(path=%s, enc_loc_len=%d, pub_key_len=%d): %s: %s",
                tag_name,
                "own" if own else "crowd",
                len(encrypted_location),
                len(public_key_random),
                type(e).__name__,
                e,
            )
            continue

        proto_loc = DeviceUpdate_pb2.Location()
        proto_loc.ParseFromString(decrypted_location)

        entry = {
            "polled_at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tag": tag_name,
            "lat": proto_loc.latitude / 1e7,
            "lon": proto_loc.longitude / 1e7,
            "altitude_m": proto_loc.altitude,
            "accuracy_m": loc.geoLocation.accuracy,
            "status": _status_name(loc.status),
            "is_own_report": loc.geoLocation.encryptedReport.isOwnReport,
            "location_time": location_time,
        }
        results.append(entry)

    return results


def _status_name(status_code):
    names = {
        Common_pb2.Status.SEMANTIC: "SEMANTIC",
        Common_pb2.Status.LAST_KNOWN: "LAST_KNOWN",
        Common_pb2.Status.CROWDSOURCED: "CROWDSOURCED",
        Common_pb2.Status.AGGREGATED: "AGGREGATED",
    }
    return names.get(status_code, str(status_code))


def _fetch_location(canonic_device_id, name, timeout=60):
    result_holder = [None]
    request_uuid = generate_random_uuid()

    def handle_response(response_hex):
        device_update = parse_device_update_protobuf(response_hex)
        if device_update.fcmMetadata.requestUuid == request_uuid:
            result_holder[0] = device_update

    fcm_token = FcmReceiver().register_for_location_updates(handle_response)
    hex_payload = create_location_request(canonic_device_id, fcm_token, request_uuid)
    nova_request(NOVA_ACTION_API_SCOPE, hex_payload)

    deadline = time.time() + timeout
    while result_holder[0] is None and time.time() < deadline:
        time.sleep(0.2)

    if result_holder[0] is None:
        log.warning("[Poller] WARNING: timeout waiting for location response for %s", name)
        return []

    return _extract_locations(result_holder[0], name)


def _load_archive_state():
    """Return (seen_keys, latest_location_time_per_tag). Must be called inside _data_lock."""
    seen_keys = set()
    latest = {}
    if not os.path.exists(ARCHIVE_PATH):
        return seen_keys, latest
    with open(ARCHIVE_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            tag = entry.get("tag")
            loc_time = entry.get("location_time", "")
            seen_keys.add((tag, loc_time))
            if tag not in latest or loc_time > latest[tag]:
                latest[tag] = loc_time
    return seen_keys, latest


def _check_auth():
    required = ["aas_token", "fcm_credentials", "shared_key"]
    missing = [k for k in required if not get_cached_value(k)]
    if missing:
        log.error("[Poller] ERROR: missing credentials: %s", ", ".join(missing))
        log.error("[Poller] Run: python auth.py")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--purge", action="store_true", help="Archive entries older than 7 days, no network calls")
    parser.add_argument("--log-file", default=LOG_PATH, help=f"Rotating log file (default: {LOG_PATH})")
    args = parser.parse_args()

    _setup_file_log(args.log_file)

    if args.purge:
        log.info("[Poller] Start: %s", datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"))
        _purge()
        log.info("[Poller] End: %s", datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"))
        return

    _check_auth()
    log.info("[Poller] Start: %s", datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"))
    log.info("[Poller] Fetching device list...")
    result_hex = request_device_list()
    device_list = parse_device_list_protobuf(result_hex)
    canonic_ids = get_canonic_ids(device_list)

    if not canonic_ids:
        log.error("[Poller] No devices found. Make sure authentication is complete.")
        sys.exit(1)

    log.info("[Poller] Found %d tracker(s):", len(canonic_ids))
    for name, cid in canonic_ids:
        log.info("  - %s (%s)", name, cid)

    all_locations = []
    for name, cid in canonic_ids:
        log.info("[Poller] Requesting location for: %s", name)
        all_locations.append((name, _fetch_location(cid, name)))

    new_entries = []
    with _data_lock():
        existing_keys, latest_per_tag = _load_archive_state()

        for name, locations in all_locations:
            for entry in locations:
                tag = entry.get("tag")
                loc_time = entry.get("location_time", "")
                key = (tag, loc_time)

                if key in existing_keys:
                    continue
                if tag in latest_per_tag and loc_time <= latest_per_tag[tag]:
                    continue

                new_entries.append(entry)
                existing_keys.add(key)
                if tag not in latest_per_tag or loc_time > latest_per_tag[tag]:
                    latest_per_tag[tag] = loc_time

        _secure_dir(os.path.dirname(ARCHIVE_PATH))
        with open(ARCHIVE_PATH, "a") as f:
            for entry in new_entries:
                f.write(json.dumps(entry) + "\n")
        _secure_file(ARCHIVE_PATH)

    FcmReceiver().stop_listening()

    log.info("[Poller] Done. %d new entry/entries written to %s", len(new_entries), ARCHIVE_PATH)
    log.info("[Poller] End: %s", datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"))


if __name__ == "__main__":
    main()
