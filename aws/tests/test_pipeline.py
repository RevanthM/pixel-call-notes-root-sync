import base64
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
from unittest import TestCase

from botocore.exceptions import ClientError

os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


starter = load("callnotes_starter", "functions/start_transcription/app.py")
finalizer = load("callnotes_finalizer", "functions/finalize/app.py")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checksum(value):
    return base64.b64encode(bytes.fromhex(value)).decode()


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.counter = 0

    def seed(self, key, data, *, version_id=None):
        if isinstance(data, dict):
            data = json.dumps(data).encode()
        value = digest(data)
        self.counter += 1
        self.objects[key] = {
            "data": data,
            "version_id": version_id or f"v{self.counter}",
            "sha256": value,
            "checksum": checksum(value),
            "etag": value[:16],
            "metadata": {"sha256": value},
        }
        return self.objects[key]

    def head_object(self, **kwargs):
        item = self.objects.get(kwargs["Key"])
        if not item:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "synthetic"}},
                "HeadObject",
            )
        requested = kwargs.get("VersionId")
        if requested and requested != item["version_id"]:
            raise ClientError(
                {"Error": {"Code": "NoSuchVersion", "Message": "synthetic"}},
                "HeadObject",
            )
        return {
            "ContentLength": len(item["data"]),
            "VersionId": item["version_id"],
            "ETag": f'"{item["etag"]}"',
            "Metadata": item["metadata"],
            "ChecksumSHA256": item["checksum"],
        }

    def get_object(self, **kwargs):
        head = self.head_object(**kwargs)
        item = self.objects[kwargs["Key"]]
        return {
            **head,
            "Body": io.BytesIO(item["data"]),
        }

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": "synthetic"}},
                "PutObject",
            )
        body = kwargs["Body"]
        data = body.read() if hasattr(body, "read") else body
        item = self.seed(key, data)
        metadata = kwargs.get("Metadata") or {}
        item["sha256"] = metadata.get("sha256", item["sha256"])
        item["metadata"] = {**metadata, "sha256": item["sha256"]}
        item["checksum"] = kwargs.get("ChecksumSHA256", item["checksum"])
        self.puts.append(kwargs)
        return {
            "VersionId": item["version_id"],
            "ETag": f'"{item["etag"]}"',
        }


class FakeTranscribe:
    def __init__(self):
        self.request = None
        self.status = "IN_PROGRESS"

    def start_transcription_job(self, **request):
        self.request = request

    def get_transcription_job(self, **_kwargs):
        return {
            "TranscriptionJob": {
                **self.request,
                "TranscriptionJobStatus": self.status,
            }
        }


class ConflictTranscribe(FakeTranscribe):
    def __init__(self, request, status="COMPLETED"):
        super().__init__()
        self.request = request
        self.status = status

    def start_transcription_job(self, **_request):
        raise ClientError(
            {"Error": {"Code": "ConflictException", "Message": "synthetic"}},
            "StartTranscriptionJob",
        )


class FakeSQS:
    def __init__(self):
        self.messages = []

    def send_message(self, **request):
        self.messages.append(request)


class FakeBedrock:
    def __init__(self):
        self.calls = []

    def converse(self, **request):
        self.calls.append(request)
        summary = {
            "overview": "Synthetic overview.",
            "participants": ["ch_0", "ch_1"],
            "topics": ["Test"],
            "decisions": [],
            "action_items": [
                {"owner": None, "task": "Check output", "due": None}
            ],
            "follow_ups": [],
            "important_details": [],
            "uncertainties": ["Speaker identities are unknown."],
        }
        return {
            "output": {
                "message": {
                    "content": [{"text": json.dumps(summary)}],
                }
            }
        }


def setup_contract(store):
    prefix = "call-notes/dt=2026-01-02/" + "a" * 24
    audio_key = f"{prefix}/audio.m4a"
    metadata_key = f"{prefix}/source/metadata.json"
    audio = store.seed(audio_key, b"synthetic stereo audio", version_id="audio-v1")
    metadata = store.seed(metadata_key, {}, version_id="metadata-v1")
    ready_payload = {
        "schema_version": 1,
        "pipeline_version": "v1",
        "record_id": "a" * 24,
        "audio": {
            "key": audio_key,
            "version_id": audio["version_id"],
            "sha256": audio["sha256"],
            "bytes": len(audio["data"]),
        },
        "metadata": {
            "key": metadata_key,
            "version_id": metadata["version_id"],
            "sha256": metadata["sha256"],
            "bytes": len(metadata["data"]),
        },
    }
    ready_key = f"{prefix}/ready.json"
    ready = store.seed(ready_key, ready_payload, version_id="ready-v1")
    source = {
        "bucket": "synthetic-bucket",
        "key": ready_key,
        "version_id": ready["version_id"],
        "etag": ready["etag"],
    }
    return prefix, source


class StartTranscriptionTests(TestCase):
    def setUp(self):
        os.environ.update(
            {
                "AUDIO_BUCKET": "synthetic-bucket",
                "EXPECTED_BUCKET_OWNER": "000000000000",
                "TRANSCRIBE_DATA_ROLE_ARN": (
                    "arn:aws:iam::000000000000:role/synthetic"
                ),
                "COMPLETION_QUEUE_URL": "https://example.invalid/queue",
            }
        )
        self.store = MemoryS3()
        self.prefix, self.source = setup_contract(self.store)
        starter._s3 = self.store
        starter._transcribe = FakeTranscribe()

    def test_eventbridge_ready_object_is_normalized(self):
        message = {
            "source": "aws.s3",
            "detail": {
                "bucket": {"name": "synthetic-bucket"},
                "object": {
                    "key": self.source["key"].replace("/", "%2F"),
                    "etag": self.source["etag"],
                    "version-id": self.source["version_id"],
                },
            },
        }
        self.assertEqual(starter.source_objects(message)[0]["key"], self.source["key"])

    def test_stages_exact_version_before_transcribe(self):
        self.assertTrue(starter.start_one(self.source))
        request = starter._transcribe.request
        self.assertEqual(request["MediaFormat"], "m4a")
        self.assertEqual(request["Settings"], {"ChannelIdentification": True})
        self.assertRegex(
            request["TranscriptionJobName"],
            re.compile(r"^callnotes-[0-9a-f]{24}$"),
        )
        staged_key = request["Media"]["MediaFileUri"].split(
            "s3://synthetic-bucket/", 1
        )[1]
        self.assertIn("/pipeline/v1/input/", staged_key)
        self.assertEqual(
            self.store.objects[staged_key]["data"], b"synthetic stereo audio"
        )
        self.assertEqual(
            request["OutputKey"],
            (
                f"{self.prefix}/asr/v1/jobs/"
                f"{request['TranscriptionJobName']}/transcribe.json"
            ),
        )
        self.assertTrue(request["JobExecutionSettings"]["AllowDeferredExecution"])
        status = json.loads(
            self.store.objects[f"{self.prefix}/pipeline/v1/status.json"]["data"]
        )
        self.assertEqual(status["source"]["ready"]["version_id"], "ready-v1")
        self.assertEqual(status["source"]["audio"]["version_id"], "audio-v1")
        self.assertEqual(
            status["source"]["staged"]["version_id"],
            self.store.objects[staged_key]["version_id"],
        )

    def test_ignores_audio_event_before_ready_commit(self):
        self.assertFalse(
            starter.start_one(
                {
                    "bucket": "synthetic-bucket",
                    "key": f"{self.prefix}/audio.m4a",
                    "version_id": "audio-v1",
                }
            )
        )

    def test_refuses_noncurrent_ready_version(self):
        self.store.objects[self.source["key"]]["version_id"] = "ready-v2"
        with self.assertRaises(ClientError):
            starter.start_one(self.source)

    def test_terminal_transcribe_conflict_is_reenqueued(self):
        starter.start_one(self.source)
        existing_request = starter._transcribe.request
        starter._transcribe = ConflictTranscribe(existing_request)
        starter._sqs = FakeSQS()

        starter.start_one(self.source)

        self.assertEqual(len(starter._sqs.messages), 1)
        body = json.loads(starter._sqs.messages[0]["MessageBody"])
        self.assertEqual(
            body["detail"]["TranscriptionJobStatus"], "COMPLETED"
        )


class FinalizeTests(TestCase):
    def setUp(self):
        os.environ.update(
            {
                "AUDIO_BUCKET": "synthetic-bucket",
                "EXPECTED_BUCKET_OWNER": "000000000000",
                "SUMMARY_MODEL_ID": "synthetic-model",
            }
        )

    def test_s3_source(self):
        self.assertEqual(
            finalizer.s3_source(
                "s3://synthetic-bucket/call-notes/a%20b/audio.m4a"
            ),
            ("synthetic-bucket", "call-notes/a b/audio.m4a"),
        )

    def test_channel_words_keep_raw_labels(self):
        raw = {
            "results": {
                "channel_labels": {
                    "channels": [
                        {
                            "channel_label": "ch_0",
                            "items": [
                                {
                                    "start_time": "0.0",
                                    "end_time": "0.2",
                                    "type": "pronunciation",
                                    "alternatives": [{"content": "Hello"}],
                                },
                                {
                                    "type": "punctuation",
                                    "alternatives": [{"content": "."}],
                                },
                            ],
                        },
                        {
                            "channel_label": "ch_1",
                            "items": [
                                {
                                    "start_time": "1.0",
                                    "end_time": "1.2",
                                    "type": "pronunciation",
                                    "alternatives": [{"content": "Hi"}],
                                }
                            ],
                        },
                    ]
                }
            }
        }
        segments, text = finalizer.render_transcript(raw)
        self.assertEqual(
            [segment["speaker"] for segment in segments], ["ch_0", "ch_1"]
        )
        self.assertEqual(segments[0]["text"], "Hello.")
        self.assertIn("[00:01] ch_1: Hi", text)

    def test_non_json_model_output_is_rejected(self):
        with self.assertRaises(finalizer.InvalidModelOutput):
            finalizer.parse_summary("A short synthetic summary.")

    def test_json_summary_requires_exact_schema(self):
        with self.assertRaises(finalizer.InvalidModelOutput):
            finalizer.parse_summary(
                '{"overview":"ok","topics":[],"extra":"not allowed"}'
            )

    def test_complete_pipeline_commits_terminal_manifest_last(self):
        store = MemoryS3()
        prefix, source = setup_contract(store)
        transcribe = FakeTranscribe()
        starter._s3 = store
        starter._transcribe = transcribe
        os.environ.update(
            {
                "TRANSCRIBE_DATA_ROLE_ARN": (
                    "arn:aws:iam::000000000000:role/synthetic"
                ),
                "COMPLETION_QUEUE_URL": "https://example.invalid/queue",
            }
        )
        starter.start_one(source)
        raw = {
            "results": {
                "channel_labels": {
                    "channels": [
                        {
                            "channel_label": "ch_0",
                            "items": [
                                {
                                    "start_time": "0",
                                    "end_time": "0.5",
                                    "type": "pronunciation",
                                    "alternatives": [{"content": "Hello"}],
                                }
                            ],
                        }
                    ]
                }
            }
        }
        store.seed(transcribe.request["OutputKey"], raw)
        transcribe.status = "COMPLETED"
        bedrock = FakeBedrock()
        finalizer._s3 = store
        finalizer._transcribe = transcribe
        finalizer._bedrock = bedrock
        name = transcribe.request["TranscriptionJobName"]

        finalizer.finalize(name, "COMPLETED")

        terminal_key = f"{prefix}/pipeline/v1/terminal.json"
        terminal = json.loads(store.objects[terminal_key]["data"])
        self.assertEqual(terminal["outcome"], "COMPLETED")
        self.assertEqual(terminal["job_name"], name)
        self.assertIn("version_id", terminal["artifacts"]["summary_json"])
        self.assertIn("sha256", terminal["artifacts"]["transcript_json"])
        self.assertEqual(store.puts[-2]["Key"], terminal_key)
        self.assertTrue(store.puts[-2]["IfNoneMatch"] == "*")

        finalizer.finalize(name, "COMPLETED")
        self.assertEqual(len(bedrock.calls), 1)

    def test_sqs_batch_reports_only_failed_message(self):
        calls = []
        original = finalizer.process_event

        def process(event):
            calls.append(event)
            if event.get("detail", {}).get("TranscriptionJobName") == "bad":
                raise RuntimeError("synthetic")
            return "ok"

        finalizer.process_event = process
        try:
            result = finalizer.handler(
                {
                    "Records": [
                        {
                            "messageId": "1",
                            "body": '{"detail":{"TranscriptionJobName":"ok"}}',
                        },
                        {
                            "messageId": "2",
                            "body": '{"detail":{"TranscriptionJobName":"bad"}}',
                        },
                    ]
                },
                None,
            )
        finally:
            finalizer.process_event = original
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            result, {"batchItemFailures": [{"itemIdentifier": "2"}]}
        )
