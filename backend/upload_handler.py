import base64
import json
import logging
import os
import uuid

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
stepfunctions = boto3.client("stepfunctions")

BUCKET = os.environ["DOCUMENT_BUCKET"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
MAX_BYTES = 10 * 1024 * 1024

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    try:
        body = event.get("body")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        payload = json.loads(body)
        file_b64 = payload["file"]

        file_bytes = base64.b64decode(file_b64)
        if len(file_bytes) > MAX_BYTES:
            return _response(400, {"error": "File too large (max 10MB)"})
        if not file_bytes.startswith(b"%PDF"):
            return _response(400, {"error": "File does not appear to be a valid PDF"})

        document_id = uuid.uuid4().hex[:12]
        key = f"uploads/{document_id}.pdf"

        s3.put_object(Bucket=BUCKET, Key=key, Body=file_bytes, ContentType="application/pdf")
        logger.info("Uploaded %s to s3://%s/%s", document_id, BUCKET, key)

        execution = stepfunctions.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            input=json.dumps({"bucket": BUCKET, "key": key}),
        )

        return _response(200, {
            "executionArn": execution["executionArn"],
            "document_id": document_id,
        })

    except Exception as exc:
        logger.exception("Upload handler failed")
        return _response(500, {"error": "Failed to process upload"})
