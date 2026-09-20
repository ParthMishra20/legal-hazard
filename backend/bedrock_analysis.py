import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock = boto3.client("bedrock-runtime")
dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "LegalDocumentAnalysis")
MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "apac.amazon.nova-lite-v1:0"
)
MAX_INPUT_CHARACTERS = 18000
MAX_OUTPUT_TOKENS = 1200

ALLOWED_LEVELS = {"High", "Medium", "Low"}
_SEVERITY = {"Low": 0, "Medium": 1, "High": 2}


ANALYSIS_INSTRUCTIONS = """You analyze only the document text provided below.
MANDATORY OUTPUT CONTRACT:
- Return ONLY one raw, valid JSON object.
- Do not use Markdown, code fences, a JSON label, or explanatory text before or after the object.
- The first character of your response must be { and the last character must be }.
Use this exact schema:
{
  "overall_risk_level": "High|Medium|Low",
  "overall_risk_summary": "string",
  "flagged_clauses": [
    {
      "clause_text": "exact text copied from the document",
      "risk_level": "High|Medium|Low",
      "explanation": "specific plain-language explanation"
    }
  ]
}

Rules:
- overall_risk_level must be exactly High, Medium, or Low.
- overall_risk_level must be at least as severe as the single riskiest flagged clause. If any clause is High, overall_risk_level must be High.
- Each flagged clause must quote an exact contiguous clause or sentence from the document.
- Explain why that specific clause may be unusual, one-sided, costly, restrictive, or otherwise important.
- Do not invent facts, clauses, laws, rights, or risks not supported by the document.
- Prefer a small list of material clauses over generic warnings. Return an empty list when no material clause is found.
- The summary must refer to patterns actually present in this document.
- This is document clarity, not legal advice. Do not tell the user that a clause is legally valid or invalid.
"""


def _text_from_event(event):
    if isinstance(event, str):
        event = json.loads(event)

    if isinstance(event, dict) and "text" in event:
        return event["text"], event.get("document_id", "unknown")

    if isinstance(event, dict) and "body" in event:
        body = event["body"]
        if isinstance(body, str):
            body = json.loads(body)
        return body["text"], body.get("document_id", "unknown")

    raise ValueError("Expected event with a text field")

def _invoke_model(text):
    prompt = f"{ANALYSIS_INSTRUCTIONS}\n\nDOCUMENT TEXT:\n{text}"
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
        inferenceConfig={
            "maxTokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
        },
    )
    content = response.get("output", {}).get("message", {}).get("content", [])
    model_text = "".join(
        part.get("text", "") for part in content
    ).strip()
    if not model_text:
        raise ValueError("Bedrock returned no text content")
    if "```" in model_text:
        model_text = model_text.replace(
            "```json", "").replace("```", "").strip()
    start = model_text.find("{")
    end = model_text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Bedrock response did not contain a JSON object")
    return json.loads(model_text[start: end + 1])


def _validate_analysis(result):
    if not isinstance(result, dict):
        raise ValueError("Analysis must be a JSON object")
    if result.get("overall_risk_level") not in ALLOWED_LEVELS:
        raise ValueError("Invalid overall_risk_level")
    if not isinstance(result.get("overall_risk_summary"), str):
        raise ValueError("Invalid overall_risk_summary")
    clauses = result.get("flagged_clauses")
    if not isinstance(clauses, list):
        raise ValueError("flagged_clauses must be a list")
    for clause in clauses:
        if not isinstance(clause, dict):
            raise ValueError("Each flagged clause must be an object")
        if not isinstance(clause.get("clause_text"), str) or not clause["clause_text"]:
            raise ValueError("Each clause needs clause_text")
        if clause.get("risk_level") not in ALLOWED_LEVELS:
            raise ValueError("Invalid clause risk_level")
        if not isinstance(clause.get("explanation"), str) or not clause["explanation"]:
            raise ValueError("Each clause needs an explanation")
    return result


def _enforce_overall_risk_level(result):
    clauses = result.get("flagged_clauses", [])
    if not clauses:
        return result
    worst = max(_SEVERITY[c["risk_level"]] for c in clauses)
    current = _SEVERITY[result["overall_risk_level"]]
    if worst > current:
        logger.info(
            "Overriding overall_risk_level from %s to match worst flagged clause",
            result["overall_risk_level"],
        )
        for level, rank in _SEVERITY.items():
            if rank == worst:
                result["overall_risk_level"] = level
                break
    return result


def _store_result(result):
    import time
    table = dynamodb.Table(TABLE_NAME)
    item = dict(result)
    item["created_at"] = int(time.time())
    table.put_item(Item=item)
    return result


def lambda_handler(event, context):
    text, document_id = _text_from_event(event)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    text = text[:MAX_INPUT_CHARACTERS]
    logger.info("Analyzing document %s with model %s", document_id, MODEL_ID)
    result = _enforce_overall_risk_level(_validate_analysis(_invoke_model(text)))
    result["document_id"] = document_id
    result = _store_result(result)
    logger.info("Flagged %d clauses for document %s",
                len(result["flagged_clauses"]), document_id)
    return {"statusCode": 200, "body": json.dumps(result)}
