import json
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

stepfunctions = boto3.client("stepfunctions")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    try:
        params = event.get("queryStringParameters") or {}
        execution_arn = params.get("executionArn")
        if not execution_arn:
            return _response(400, {"error": "Missing executionArn"})

        execution = stepfunctions.describe_execution(executionArn=execution_arn)
        status = execution["status"]

        if status == "RUNNING":
            return _response(200, {"status": "RUNNING"})

        if status == "SUCCEEDED":
            output = json.loads(execution["output"])
            result = json.loads(output["body"])
            return _response(200, {"status": "SUCCEEDED", "result": result})

        logger.error("Execution %s ended with status %s", execution_arn, status)
        return _response(200, {"status": "FAILED", "error": "Analysis failed, please try again"})

    except Exception:
        logger.exception("Status handler failed")
        return _response(500, {"error": "Failed to check status"})
