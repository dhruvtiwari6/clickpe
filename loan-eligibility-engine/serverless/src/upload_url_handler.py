"""Presigned-URL upload Lambda (API Gateway, GET /upload-url).

Returns a short-lived S3 presigned PUT URL so the browser uploads the CSV
*directly to S3* -- the small JSON request/response goes through API Gateway,
but the (potentially large) file bytes never do. This is the scalable,
event-driven ingestion entry point the assignment asks for: it sidesteps the
API Gateway / Lambda request-size (6 MB sync) and timeout limits.

Flow:
    UI --GET /upload-url?filename=users.csv--> this Lambda --> { url, key }
    UI --HTTP PUT (file bytes)--> S3 (uploads/...) --> S3 event --> ingest_handler
"""
import json
import os
import re
import uuid

import boto3

s3 = boto3.client("s3")

BUCKET = os.environ.get("UPLOAD_BUCKET", "")
PREFIX = os.environ.get("UPLOAD_PREFIX", "uploads/")
URL_TTL = int(os.environ.get("PRESIGN_TTL_SECONDS", "300"))

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(name: str) -> str:
    name = (name or "users.csv").strip().split("/")[-1]
    name = _SAFE.sub("_", name)
    if not name.lower().endswith(".csv"):
        name += ".csv"
    return name


def _cors(body, status=200):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(body),
    }


def handler(event, context):
    # CORS preflight
    if (event.get("requestContext", {}).get("http", {}).get("method")
            or event.get("httpMethod")) == "OPTIONS":
        return _cors({"ok": True})

    if not BUCKET:
        return _cors({"error": "UPLOAD_BUCKET not configured"}, 500)

    params = event.get("queryStringParameters") or {}
    filename = _safe_name(params.get("filename", "users.csv"))
    key = f"{PREFIX}{uuid.uuid4()}-{filename}"

    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": key, "ContentType": "text/csv"},
        ExpiresIn=URL_TTL,
    )
    return _cors({"url": url, "key": key, "expires_in": URL_TTL, "method": "PUT"})
