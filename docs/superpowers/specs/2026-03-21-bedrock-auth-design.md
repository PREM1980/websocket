# Design: AWS Bedrock Authentication via dotenv

**Date:** 2026-03-21
**Status:** Approved

## Summary

Add AWS Bedrock authentication support to `server_agent.py` by loading credentials from a `.env` file at startup and validating they are present before the server accepts any connections.

## Motivation

The server currently authenticates to Claude via `ANTHROPIC_API_KEY`. The requirement is to switch to Amazon Bedrock using IAM long-term credentials (`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`), with the region also configurable via env var. Credentials should be loaded from a `.env` file for local development convenience.

## Required Environment Variables

| Variable | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM access key ID |
| `AWS_SECRET_ACCESS_KEY` | IAM secret access key |
| `AWS_DEFAULT_REGION` | AWS region for Bedrock (e.g. `us-east-1`) — uses the boto3/AWS CLI canonical name |

Note: `AWS_DEFAULT_REGION` is used (not `AWS_REGION`) to match boto3 and AWS CLI conventions, ensuring the subprocess picks it up automatically.

## Changes

### `requirements.txt`
Add `python-dotenv`.

### `.gitignore` (new file)
Add `.env` entry to prevent accidental credential commits.

### `.env.example` (new file, committed)
Template showing the required variables with placeholder values — gives new developers a discoverable reference.

### `server_agent.py`
1. Import `load_dotenv` from `dotenv`, plus `os` and `sys`.
2. Call `load_dotenv()` immediately after the import block, before any other module-level statements.
3. Validate all three required vars are present; if any are missing, log a clear error message naming the missing variables and call `sys.exit(1)`.

No changes to `ClaudeAgentOptions`, `run_agent()`, or WebSocket logic. The Agent SDK subprocess inherits the parent process environment automatically, so the AWS credentials are available without explicit forwarding.

### Tests
Add tests for the startup validation logic:
- Server exits with code 1 when any required variable is missing.
- Server starts normally when all three variables are present.

## Usage

Create a `.env` file in the project root based on `.env.example` (do not commit `.env`):

```
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

Then run the server as before:

```bash
python server_agent.py
```

If the `.env` file does not exist, the server still starts normally as long as the three variables are present in the process environment by other means (e.g., exported shell variables, CI secrets injection). If any required variable is missing regardless of source, the server exits immediately with a descriptive error before accepting connections.

## Implementation Notes

- Verify whether the `anthropic` package in `requirements.txt` is still a direct dependency after migrating to Bedrock, or if it becomes transitively pulled in by the Agent SDK only. Remove it as a direct dependency if unused.

## Out of Scope

- `AWS_SESSION_TOKEN` / temporary credentials
- Passing credentials explicitly via `ClaudeAgentOptions.env`
- Any changes to the WebSocket protocol or client
