#!/bin/sh
set -e

# Wait for MinIO to start
until (/usr/bin/mc alias set localminio http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"); do
    echo "Waiting for MinIO service..."
    sleep 2
done

# Create buckets if they do not exist
/usr/bin/mc mb --ignore-existing localminio/"$MINIO_BUCKET_RAW"
/usr/bin/mc mb --ignore-existing localminio/"$MINIO_BUCKET_RESEARCH"

echo "MinIO buckets created successfully."
