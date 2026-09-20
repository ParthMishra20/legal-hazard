# Legal Hazard

**Know what you're signing, clause by clause.**

Legal Hazard reads a rental agreement, offer letter, or any other contract and flags the risky or unusual clauses in plain language, so a normal person can understand what they're about to sign without needing a lawyer. It is not legal advice, and the product is explicit about that throughout.

Built for the AWS Bharat Builds Tour "First Commit" hackathon (WeMakeDevs x AWS), Ship It track.

**Live app:** https://main.d1guinljtydfid.amplifyapp.com

## How it works

1. **Upload** — you drop in a PDF. It's uploaded straight to a private S3 bucket over an encrypted connection.
2. **Extract** — Amazon Textract reads the document page by page and pulls out the full text, including multi-column layouts and scanned pages.
3. **Analyze** — Amazon Bedrock (Nova Lite) reads the extracted text clause by clause and flags anything one-sided, unusual, or costly, with a plain-language explanation for each flag.
4. **Verdict** — you get an overall risk level (Low / Medium / High), a summary, and every flagged clause listed individually with its own explanation.

## Architecture

```
Frontend (static HTML/CSS/JS)
   hosted on AWS Amplify Hosting
        │
        │  POST /analyze  (base64 PDF)
        ▼
API Gateway (HTTP API)
        │
        ▼
Lambda: LegalDocumentUploadHandler
   - decodes base64, validates it's a PDF, enforces a 10MB limit
   - uploads the file to S3
   - starts a Step Functions execution
   - returns { executionArn, document_id }
        │
        ▼
Step Functions: LegalDocumentProcessingPipeline
        │
        ├─ State 1 — Lambda: LegalDocumentProcessorTextract
        │      Amazon Textract extracts the full document text
        │      (sync for images, async for PDF/TIFF)
        │
        └─ State 2 — Lambda: LegalDocumentProcessorBedrock
               Amazon Bedrock (Nova Lite, via the Converse API)
               analyzes the extracted text and returns structured JSON
               Result is written to DynamoDB, keyed by document_id
        │
        ▼
Frontend polls:  GET /status?executionArn=...
        │
        ▼
Lambda: LegalDocumentStatusHandler
   - checks the Step Functions execution via DescribeExecution
   - returns RUNNING while in progress, or SUCCEEDED/FAILED with the result
```

API Gateway enforces a 29-second timeout, and the full pipeline can take 15–45 seconds, so the frontend never makes one blocking request. It POSTs to `/analyze`, gets an `executionArn` back immediately, and polls `/status` every few seconds until the result is ready.

### AWS services used

| Service | Role |
|---|---|
| Amazon S3 | Stores uploaded PDFs |
| AWS Lambda | Upload handling, status checks, Textract orchestration, Bedrock orchestration |
| Amazon API Gateway (HTTP API) | Public `/analyze` and `/status` endpoints |
| AWS Step Functions | Chains the Textract and Bedrock steps into one pipeline, with retries |
| Amazon Textract | Extracts text from uploaded PDFs, including scanned/photographed pages |
| Amazon Bedrock (Nova Lite) | Reads the extracted text and flags risky clauses in plain language |
| Amazon DynamoDB | Stores each analysis result, keyed by document ID |
| AWS Amplify Hosting | Hosts the static frontend |

All resources are deployed in `ap-south-1`.

## Repository structure

```
backend/          Lambda source code and the Step Functions state machine definition
frontend/          The static site (index.html — no framework, no build step)
test-data/          Sample contracts used to test the pipeline end to end
```

## Running the frontend locally

The frontend is a single self-contained HTML file with no build step.

```
cd frontend
python3 -m http.server 8000
```

Then open `http://localhost:8000`. (Opening the file directly with `file://` won't work — the page's origin needs to be a real `http://` origin for the API's CORS configuration to allow requests.)

## Test documents

`test-data/` includes three different contract types used to verify the pipeline handles more than one document structure:

- `sample-rental-agreement.pdf` — a residential lease
- `sample-offer-letter.pdf` — an employment offer letter
- `sample-freelance-contract.pdf` — a multi-page independent contractor agreement with a payment schedule table, used specifically to confirm Textract and Bedrock hold up on a longer, differently structured document

## Disclaimer

Legal Hazard is a document-clarity tool, not a substitute for legal advice. If anything it flags concerns you, or you're simply not sure, talk to a qualified lawyer before signing.