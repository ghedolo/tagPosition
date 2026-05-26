import sys
import os
import json
import time
import fcntl
import datetime
import hashlib
import argparse
from contextlib import contextmanager

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "GoogleFindMyTools")
sys.path.insert(0, LIB_DIR)

ARCHIVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "positions.json")
LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".poller.lock")
PURGE_DAYS = 7

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


@contextmanager
def _data_lock():
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
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
            print("[Poller] No data file found.")
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
            print("[Poller] Nothing to archive.")
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

        with open(ARCHIVE_PATH, "w") as f:
            for entry in recent:
                f.write(json.dumps(entry) + "\n")

        print(f"[Poller] Archived {len(old)} entries -> {archive_name}")
        print(f"[Poller] Kept {len(recent)} entries in positions.json")


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
                    print(f"[Poller] WARNING: empty encrypted_location (own report) for {tag_name}", file=sys.stderr)
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
            print(
                f"[Poller] WARNING: could not decrypt location for {tag_name} "
                f"(path={'own' if own else 'crowd'}, "
                f"enc_loc_len={len(encrypted_location)}, "
                f"pub_key_len={len(public_key_random)}): "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
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
        print(f"[Poller] WARNING: timeout waiting for location response for {name}", file=sys.stderr)
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
        print(f"[Poller] ERROR: missing credentials: {', '.join(missing)}")
        print("[Poller] Run: python auth.py")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--purge", action="store_true", help="Archive entries older than 7 days, no network calls")
    args = parser.parse_args()

    if args.purge:
        print(f"[Poller] Start: {datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        _purge()
        print(f"[Poller] End: {datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        return

    _check_auth()
    print(f"[Poller] Start: {datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print("[Poller] Fetching device list...")
    result_hex = request_device_list()
    device_list = parse_device_list_protobuf(result_hex)
    canonic_ids = get_canonic_ids(device_list)

    if not canonic_ids:
        print("[Poller] No devices found. Make sure authentication is complete.")
        sys.exit(1)

    print(f"[Poller] Found {len(canonic_ids)} tracker(s):")
    for name, cid in canonic_ids:
        print(f"  - {name} ({cid})")

    all_locations = []
    for name, cid in canonic_ids:
        print(f"[Poller] Requesting location for: {name}")
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

        os.makedirs(os.path.dirname(ARCHIVE_PATH), exist_ok=True)
        with open(ARCHIVE_PATH, "a") as f:
            for entry in new_entries:
                f.write(json.dumps(entry) + "\n")

    FcmReceiver().stop_listening()

    print(f"[Poller] Done. {len(new_entries)} new entry/entries written to {ARCHIVE_PATH}")
    print(f"[Poller] End: {datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}")


if __name__ == "__main__":
    main()
