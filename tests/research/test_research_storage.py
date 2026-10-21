from botocore.exceptions import ClientError
import io
import json

from app.storage import ResearchStorage


class _MissingBucketClient:
    def __init__(self):
        self.put_calls = 0
        self.created = False

    def put_object(self, **_kwargs):
        self.put_calls += 1
        if self.put_calls == 1:
            raise ClientError(
                {"Error": {"Code": "NoSuchBucket", "Message": "missing"}},
                "PutObject",
            )

    def create_bucket(self, **_kwargs):
        self.created = True


class _RecordingClient:
    def __init__(self):
        self.keys = []

    def put_object(self, **kwargs):
        self.keys.append(kwargs["Key"])

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(json.dumps({"page_index": 2, "html": "feed"}).encode())}


def test_archive_creates_missing_bucket_and_retries_put():
    storage = ResearchStorage()
    client = _MissingBucketClient()
    storage._s3_client = client

    key = storage.archive_document("player-1", "sha256:abc123", {"html": "saved"})

    assert key is not None
    assert client.created
    assert client.put_calls == 2


def test_storage_client_initialization_does_not_probe_bucket(monkeypatch):
    import boto3

    calls = []
    client = object()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: calls.append((args, kwargs)) or client)

    storage = ResearchStorage()

    assert storage._get_client() is client
    assert len(calls) == 1


def test_retrieval_observations_do_not_overwrite_same_content():
    storage = ResearchStorage()
    client = _RecordingClient()
    storage._s3_client = client

    first = storage.archive_document("match-1", "sha256:same", {"retrieved_at": "t1", "html": "A"})
    second = storage.archive_document("match-1", "sha256:same", {"retrieved_at": "t2", "html": "A"})

    assert first != second
    assert len(set(client.keys)) == 2


def test_archived_page_can_be_read_for_durable_resume():
    storage = ResearchStorage()
    storage._s3_client = _RecordingClient()
    assert storage.read_document("snapshots/research/2026-10-20/player/page.json") == {
        "page_index": 2, "html": "feed",
    }
    assert storage.read_document("../outside.json") is None
