# Bedrock Auth via dotenv Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load AWS Bedrock IAM credentials from a `.env` file at server startup and fail fast if any required variable is missing.

**Architecture:** Add `python-dotenv` to load `.env` into the process environment before the FastAPI app initialises. A dedicated `validate_aws_env()` function checks the three required vars and calls `sys.exit(1)` with a clear message if any are absent. The function is called inside `if __name__ == "__main__"` so tests can import and call it under controlled conditions. Because the Agent SDK spawns a subprocess that inherits the parent's environment, no further changes are needed for the credentials to reach the SDK.

**Tech Stack:** Python, python-dotenv, pytest

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `requirements.txt` | Add `python-dotenv` |
| Create | `.gitignore` | Exclude `.env` and common Python artifacts |
| Create | `.env.example` | Committed template for required vars |
| Create | `pytest.ini` | Set `pythonpath = .` so tests can import `server_agent` |
| Modify | `server_agent.py` | Load dotenv + `validate_aws_env()` function |
| Create | `tests/test_startup.py` | Validate env-check logic |
| Modify | `README.md` | Document authentication setup |

---

### Task 1: Add dependency and safety files

**Files:**
- Modify: `requirements.txt`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `pytest.ini`

- [ ] **Step 1: Add python-dotenv to requirements.txt**

Replace the contents of `requirements.txt` with:

```
websockets>=12.0
fastapi>=0.111.0
uvicorn>=0.29.0
claude-agent-sdk>=0.0.14
anthropic>=0.52.0
python-dotenv>=1.0.0
```

Note: `anthropic` is retained — the Agent SDK depends on it transitively and it is already in use directly; remove only if a future audit confirms it is unused.

- [ ] **Step 2: Create .gitignore**

Create `.gitignore` at the project root:

```
.env
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 3: Create .env.example**

Create `.env.example` at the project root:

```
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_DEFAULT_REGION=us-east-1
```

- [ ] **Step 4: Create pytest.ini**

Create `pytest.ini` at the project root so `import server_agent` works from the `tests/` directory:

```ini
[pytest]
pythonpath = .
```

- [ ] **Step 5: Install new dependency**

```bash
pip install python-dotenv
```

Expected: installs without error.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .gitignore .env.example pytest.ini
git commit -m "chore: add python-dotenv, .gitignore, .env.example, and pytest.ini for Bedrock auth"
```

---

### Task 2: Write failing tests for startup validation

**Files:**
- Create: `tests/test_startup.py`

- [ ] **Step 1: Create tests/test_startup.py**

```python
"""Tests for AWS env-var startup validation in server_agent."""
import os
import pytest
import server_agent


REQUIRED_VARS = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"]


def _run_validate(monkeypatch, env: dict):
    """
    Call server_agent.validate_aws_env() with a controlled environment.
    Clears all three required vars first, then sets only what is in `env`.
    Does NOT reload the module — just calls the function directly.
    """
    for var in REQUIRED_VARS:
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    server_agent.validate_aws_env()


def test_all_vars_present_does_not_exit(monkeypatch):
    """No exception when all three vars are set."""
    _run_validate(monkeypatch, {
        "AWS_ACCESS_KEY_ID": "AKIATEST",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_DEFAULT_REGION": "us-east-1",
    })
    # reaching here means no SystemExit was raised


def test_missing_access_key_exits(monkeypatch):
    """sys.exit(1) when AWS_ACCESS_KEY_ID is absent."""
    with pytest.raises(SystemExit) as exc_info:
        _run_validate(monkeypatch, {
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_DEFAULT_REGION": "us-east-1",
        })
    assert exc_info.value.code == 1


def test_missing_secret_key_exits(monkeypatch):
    """sys.exit(1) when AWS_SECRET_ACCESS_KEY is absent."""
    with pytest.raises(SystemExit) as exc_info:
        _run_validate(monkeypatch, {
            "AWS_ACCESS_KEY_ID": "AKIATEST",
            "AWS_DEFAULT_REGION": "us-east-1",
        })
    assert exc_info.value.code == 1


def test_missing_region_exits(monkeypatch):
    """sys.exit(1) when AWS_DEFAULT_REGION is absent."""
    with pytest.raises(SystemExit) as exc_info:
        _run_validate(monkeypatch, {
            "AWS_ACCESS_KEY_ID": "AKIATEST",
            "AWS_SECRET_ACCESS_KEY": "secret",
        })
    assert exc_info.value.code == 1


def test_all_vars_missing_exits(monkeypatch):
    """sys.exit(1) when all vars are absent."""
    with pytest.raises(SystemExit) as exc_info:
        _run_validate(monkeypatch, {})
    assert exc_info.value.code == 1
```

- [ ] **Step 2: Run tests — expect failure (validate_aws_env not yet defined)**

```bash
pytest tests/test_startup.py -v
```

Expected: `AttributeError: module 'server_agent' has no attribute 'validate_aws_env'`.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_startup.py
git commit -m "test: add failing tests for AWS env-var startup validation"
```

---

### Task 3: Implement startup validation in server_agent.py

**Files:**
- Modify: `server_agent.py`

- [ ] **Step 1: Add new imports at the top of server_agent.py**

After the existing imports (after line 34, the `claude_agent_sdk` import block), add:

```python
import os
import sys

from dotenv import load_dotenv

load_dotenv()  # Load .env into process environment before anything else
```

- [ ] **Step 2: Add validate_aws_env() function**

Immediately after the `load_dotenv()` call (and before `logging.basicConfig(...)`), insert:

```python
_REQUIRED_AWS_VARS = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"]


def validate_aws_env() -> None:
    """Exit with code 1 if any required AWS environment variable is missing."""
    missing = [v for v in _REQUIRED_AWS_VARS if not os.environ.get(v)]
    if missing:
        print(
            f"[AGENT-SERVER] ERROR: Missing required environment variable(s): "
            f"{', '.join(missing)}. "
            f"Set them in a .env file or in the process environment.",
            file=sys.stderr,
        )
        sys.exit(1)
```

- [ ] **Step 3: Call validate\_aws\_env() under \_\_main\_\_ guard**

At the bottom of `server_agent.py`, replace:

```python
if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8766)
```

with:

```python
if __name__ == "__main__":
    validate_aws_env()
    uvicorn.run(app, host="localhost", port=8766)
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_startup.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit implementation**

```bash
git add server_agent.py
git commit -m "feat: load .env and validate AWS Bedrock credentials at startup"
```

---

### Task 4: Verify and document

- [ ] **Step 1: Confirm server refuses to start without credentials**

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION && python server_agent.py
```

Expected: error message naming the missing variables printed to stderr, process exits immediately.

- [ ] **Step 2: Confirm server starts with credentials**

Create a local `.env` file from `.env.example` with valid (or dummy) values and run:

```bash
python server_agent.py
```

Expected: server starts and logs `Uvicorn running on http://localhost:8766`.

- [ ] **Step 3: Update README.md**

In `README.md`, insert a new `## Authentication` section immediately before `## Quickstart` (currently at line 49):

```markdown
## Authentication

The agent server authenticates to Claude via **Amazon Bedrock**. Before starting the server, create a `.env` file in the project root (see `.env.example`):

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

If any variable is missing the server exits immediately with a descriptive error. The `.env` file is git-ignored — never commit real credentials.
```

- [ ] **Step 4: Final commit**

```bash
git add README.md
git commit -m "docs: document Bedrock authentication setup in README"
```
