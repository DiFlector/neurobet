"""MinIO Object Storage archiver for raw research documents."""

import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from .config import config

logger = logging.getLogger("research.storage")


class ResearchStorage:
    """Archives raw research documents into MinIO S3."""

    def __init__(self):
        self.bucket = config.minio_bucket
        self._s3_client = None

    def _get_client(self):
        if self._s3_client is None:
            try:
                import boto3
                from botocore.client import Config

                self._s3_client = boto3.client(
                    "s3",
                    endpoint_url=f"http://{config.minio_host}:{config.minio_port}",
                    aws_access_key_id=config.minio_access_key,
                    aws_secret_access_key=config.minio_secret_key,
                    config=Config(signature_version="s3v4", connect_timeout=3, retries={"max_attempts": 1}),
                    region_name="us-east-1",
                )
            except Exception as e:
                logger.warning(f"Failed to initialize S3 client: {e}")
                self._s3_client = False
        return self._s3_client if self._s3_client is not False else None

    def archive_document(self, event_id: str, content_hash: str, document_data: Dict[str, Any]) -> Optional[str]:
        """
        Saves raw document to snapshots/research/{event_id}/{content_hash}.json.
        Returns S3 key or None.
        """
        client = self._get_client()
        if not client:
            return None

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hash_id = content_hash.replace("sha256:", "")[:16]
        key = f"snapshots/research/{date_str}/{event_id}/{hash_id}.json"

        try:
            body = json.dumps(document_data, ensure_ascii=False, default=str)
            client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/json",
            )
            return key
        except Exception as e:
            logger.warning(f"Could not archive document to S3 key '{key}': {e}")
            return None
