import json
import tempfile
from pathlib import Path
from unittest import TestCase

from botocore.exceptions import ClientError

from adb_source import query_fermat_audio
from s3_sink import S3Sink
from state import SyncState
from sync import day_partition, metadata_for, opaque_record_id, pull_stable_audio


class FakeDevice:
    def __init__(self, output=""):
        self.output = output

    def root_shell(self, _command):
        return self.output

    def describe(self):
        return {"model": "Synthetic Pixel", "codename": "sample"}


class StableDevice:
    def root_shell(self, _command):
        return "9|10\n"

    def pull_root(self, _remote, local):
        local.write_bytes(b"synthetic")


class DeviceParsingTests(TestCase):
    def test_partition_is_utc(self):
        self.assertEqual(day_partition(30 * 60 * 1000), "1970-01-01")

    def test_parses_private_audio(self):
        device = FakeDevice(
            "\n".join(
                [
                    "/data/user/0/com.google.android.dialer/files/fermat_files/1000.m4a|42|3",
                    "bad-row",
                    "/data/user/0/com.google.android.dialer/files/fermat_files/2000.m4a|84|5",
                ]
            )
        )
        items = query_fermat_audio(device, since_epoch_ms=1000)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["call_epoch_ms"], 2000)
        self.assertEqual(items[0]["bytes"], 84)

    def test_contact_data_is_redacted_by_default(self):
        call = {
            "direction": "incoming",
            "duration_sec": 60,
            "features": 1,
            "contact_name": "Synthetic Person",
            "phone_number": "synthetic-number",
            "country_iso": "US",
        }
        metadata = metadata_for(
            call,
            {"call_epoch_ms": 1000},
            device=FakeDevice(),
            device_id="primary",
            record_id="opaque",
            include_call_metadata=False,
            include_contact_details=False,
            include_device_details=False,
        )
        self.assertNotIn("contact", metadata)
        self.assertNotIn("call", metadata)
        self.assertFalse(metadata["contact_details_included"])
        self.assertEqual(metadata["source"], {"device_id": "primary"})

    def test_public_object_id_does_not_repeat_call_epoch(self):
        record_id = opaque_record_id("primary", 1000)
        self.assertEqual(record_id, opaque_record_id("primary", 1000))
        self.assertNotIn("1000", record_id)
        self.assertEqual(len(record_id), 24)

    def test_stable_audio_size_and_mtime_are_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary) / "audio.m4a"
            pull_stable_audio(
                StableDevice(),
                {
                    "bytes": 9,
                    "modified_epoch_s": 10,
                    "device_path": "/private/1000.m4a",
                },
                local,
            )
            self.assertEqual(local.read_bytes(), b"synthetic")


class ErrorS3Client:
    def __init__(self, code):
        self.code = code

    def head_object(self, **_kwargs):
        raise ClientError(
            {"Error": {"Code": self.code, "Message": "synthetic"}},
            "HeadObject",
        )


class ExistingS3Client:
    def __init__(self, digest):
        self.digest = digest

    def head_object(self, **_kwargs):
        return {"Metadata": {"sha256": self.digest}}

    def upload_file(self, *_args, **_kwargs):
        raise AssertionError("immutable conflict must not upload")


class NewS3Client:
    def __init__(self):
        self.put = None

    def head_object(self, **_kwargs):
        raise ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "synthetic"}},
            "HeadObject",
        )

    def put_object(self, **kwargs):
        self.put = kwargs
        return {"VersionId": "v1"}


class UnsafeBucketClient:
    def head_bucket(self, **_kwargs):
        return {}

    def get_bucket_location(self, **_kwargs):
        return {"LocationConstraint": "us-west-1"}

    def get_bucket_versioning(self, **_kwargs):
        return {}


class SafeBucketClient:
    def __init__(self, policy=None):
        self.policy = policy or {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": "*",
                }
            ],
        }

    def head_bucket(self, **_kwargs):
        return {}

    def get_bucket_location(self, **_kwargs):
        return {"LocationConstraint": "us-west-1"}

    def get_bucket_versioning(self, **_kwargs):
        return {"Status": "Enabled"}

    def get_public_access_block(self, **_kwargs):
        return {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }

    def get_bucket_encryption(self, **_kwargs):
        return {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        }
                    }
                ]
            }
        }

    def get_bucket_ownership_controls(self, **_kwargs):
        return {
            "OwnershipControls": {
                "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
            }
        }

    def get_bucket_tagging(self, **_kwargs):
        return {
            "TagSet": [
                {"Key": "ManagedBy", "Value": "pixel-call-notes-root-sync"}
            ]
        }

    def get_bucket_policy_status(self, **_kwargs):
        return {"PolicyStatus": {"IsPublic": False}}

    def get_bucket_policy(self, **_kwargs):
        return {"Policy": json.dumps(self.policy)}


class S3SafetyTests(TestCase):
    def sink(self, client):
        sink = object.__new__(S3Sink)
        sink.bucket = "synthetic-private-bucket"
        sink.region = "us-west-1"
        sink.account_id = "000000000000"
        sink.dry_run = False
        sink._client = client
        return sink

    def test_not_found_is_absent(self):
        self.assertIsNone(self.sink(ErrorS3Client("404"))._existing_digest("key"))

    def test_access_denied_fails_closed(self):
        with self.assertRaises(ClientError):
            self.sink(ErrorS3Client("403"))._existing_digest("key")

    def test_unversioned_bucket_is_refused(self):
        sink = self.sink(UnsafeBucketClient())
        with self.assertRaisesRegex(RuntimeError, "without enabled versioning"):
            sink._verify_bucket_safety()

    def test_same_account_managed_bucket_is_accepted(self):
        self.sink(SafeBucketClient())._verify_bucket_safety()

    def test_cross_account_bucket_policy_is_refused(self):
        policy = {
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": "111111111111"
                    },
                    "Action": "s3:GetObject",
                    "Resource": "*",
                }
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "cross-account"):
            self.sink(SafeBucketClient(policy))._verify_bucket_safety()

    def test_immutable_audio_refuses_different_bytes(self):
        sink = self.sink(ExistingS3Client("0" * 64))
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary) / "audio.m4a"
            local.write_bytes(b"synthetic")
            with self.assertRaisesRegex(RuntimeError, "refusing to replace immutable"):
                sink.put_file(local, "call/audio.m4a", immutable=True)

    def test_new_audio_uses_atomic_create_only_put(self):
        client = NewS3Client()
        sink = self.sink(client)
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary) / "audio.m4a"
            local.write_bytes(b"synthetic")
            result = sink.put_file(local, "call/audio.m4a", immutable=True)
        self.assertTrue(result.uploaded)
        self.assertEqual(client.put["IfNoneMatch"], "*")
        self.assertEqual(client.put["ExpectedBucketOwner"], "000000000000")


class StateSafetyTests(TestCase):
    def test_state_is_written_owner_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            state = SyncState(path=path, records={"synthetic": {"ok": True}})
            state.save()
            self.assertEqual(path.stat().st_mode & 0o077, 0)
            self.assertEqual(SyncState.load(path).records, state.records)

    def test_existing_parent_permissions_are_not_changed(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent.chmod(0o755)
            SyncState(path=parent / "state.json").save()
            self.assertEqual(parent.stat().st_mode & 0o777, 0o755)

    def test_scope_mismatch_fails_closed(self):
        state = SyncState(path=Path("synthetic"), scope={"bucket": "one"})
        with self.assertRaisesRegex(RuntimeError, "scope differs"):
            state.bind_scope({"bucket": "two"})
