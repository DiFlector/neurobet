"""MinIO / S3 Raw Snapshot Storage Writer."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import settings

logger = logging.getLogger("collector.s3_writer")


class MinIORawWriter:
    """Manages raw snapshot storage in S3/MinIO bucket 'raw-snapshots'."""

    def __init__(self):
        self.endpoint = settings.s3_endpoint
        self.bucket = settings.s3_bucket_raw
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
            region_name="us-east-1",
        )

    def ensure_bucket(self) -> None:
        """Create bucket if it does not already exist."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
            logger.info("S3 bucket '%s' verified.", self.bucket)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code in ("404", "NoSuchBucket"):
                logger.info("Bucket '%s' not found. Creating it...", self.bucket)
                self.client.create_bucket(Bucket=self.bucket)
                logger.info("Bucket '%s' created successfully.", self.bucket)
            else:
                logger.warning("Error checking bucket '%s': %s", self.bucket, e)

    def save_raw_snapshot(
        self,
        content_hash: str,
        snapshot_dict: Dict[str, Any],
        collected_at: datetime,
    ) -> str:
        """
        Store full raw snapshot in S3.
        Object key format: fonbet/{sport}/{YYYY-MM-DD}/{hash_prefix}/{content_hash}.json
        """
        date_str = collected_at.strftime("%Y-%m-%d")
        sport = snapshot_dict.get("sport_code", settings.sport_code)
        clean_hash = content_hash.replace("sha256:", "")
        object_key = f"fonbet/{sport}/{date_str}/{clean_hash[:4]}/{clean_hash}.json"

        body_bytes = json.dumps(snapshot_dict, ensure_ascii=False, indent=2).encode("utf-8")

        self.client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=body_bytes,
            ContentType="application/json; charset=utf-8",
            Metadata={
                "content_hash": content_hash,
                "collected_at": collected_at.isoformat(),
                "collector_version": settings.collector_version,
            },
        )
        logger.info("Saved raw snapshot to s3://%s/%s (%d bytes)", self.bucket, object_key, len(body_bytes))
        return object_key
