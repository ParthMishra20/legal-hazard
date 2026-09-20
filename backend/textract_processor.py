import json
import logging
import os
import time
from urllib.parse import unquote_plus

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
textract = boto3.client("textract")

MAX_ASYNC_WAIT_SECONDS = 45
POLL_INTERVAL_SECONDS = 2


def _document_location(event):
    if "Records" in event:
        record = event["Records"][0]
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        return bucket, key

    bucket = event.get("bucket") or os.environ.get("DOCUMENT_BUCKET")
    key = event.get("key")
    if not bucket or not key:
        raise ValueError(
            "Expected an S3 event or an object containing bucket and key")
    return bucket, key


def _line_text(blocks):
    return "\n".join(block["Text"] for block in blocks if block.get("BlockType") == "LINE")


def _sync_extract(bucket, key):
    response = textract.detect_document_text(
        Document={"S3Object": {"Bucket": bucket, "Name": key}}
    )
    return {
        "mode": "sync",
        "job_id": None,
        "pages": 1,
        "text": _line_text(response.get("Blocks", [])),
    }


def _async_extract(bucket, key):
    response = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}}
    )
    job_id = response["JobId"]
    deadline = time.time() + MAX_ASYNC_WAIT_SECONDS

    while time.time() < deadline:
        status_response = textract.get_document_text_detection(JobId=job_id)
        status = status_response["JobStatus"]
        logger.info("Textract job %s status: %s", job_id, status)

        if status == "SUCCEEDED":
            blocks = list(status_response.get("Blocks", []))
            next_token = status_response.get("NextToken")
            while next_token:
                page_response = textract.get_document_text_detection(
                    JobId=job_id, NextToken=next_token
                )
                blocks.extend(page_response.get("Blocks", []))
                next_token = page_response.get("NextToken")
            pages = max((block.get("Page", 1) for block in blocks), default=1)
            return {
                "mode": "async",
                "job_id": job_id,
                "pages": pages,
                "text": _line_text(blocks),
            }

        if status == "FAILED":
            raise RuntimeError(
                f"Textract job {job_id} failed: {status_response.get('StatusMessage', 'unknown error')}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Textract job {job_id} did not finish within {MAX_ASYNC_WAIT_SECONDS} seconds")


def lambda_handler(event, context):
    bucket, key = _document_location(event)
    logger.info("Extracting text from s3://%s/%s", bucket, key)

    # Textract's asynchronous API is required for PDF/TIFF documents.
    if key.lower().endswith((".pdf", ".tif", ".tiff")):
        result = _async_extract(bucket, key)
    else:
        result = _sync_extract(bucket, key)

    result.update({"bucket": bucket, "key": key})
    result["document_id"] = os.path.splitext(os.path.basename(key))[0]
    logger.info("Extracted %d characters from %s", len(result["text"]), key)
    return {"statusCode": 200, "body": json.dumps(result)}
