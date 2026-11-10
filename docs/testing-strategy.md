# 🧪 Testing Strategy & Quality Assurance Framework

EchoScope AI maintains a comprehensive offline test suite verified both by standalone open-source tools (`.github/workflows/ci.yml`) and an automated local QA Engine (`quality.ps1`).

---

## 1. Quality Assurance Metrics & Gates

The codebase enforces explicit thresholds in `.qa/config.yml` (`line_min: 70`, `branch_min: 75`, `vulnerability_gate: medium`) and continuous checks in GitHub Actions (`.github/workflows/ci.yml`):

| Quality Gate | Tool | Target Threshold | Current Status |
| :--- | :--- | :--- | :--- |
| **Credential Scanner** | Internal Regex Scanner | Zero exposed API keys or dummy tokens | ✅ **100% Clean** |
| **Linting & Code Style** | Ruff (`py312`) | Zero errors, import sorting (`I001`) | ✅ **0 Errors** |
| **Formatting** | Ruff Formatter | 100 char line limit, standard formatting | ✅ **38/38 Formatted** |
| **Static Type Checking** | Pyright | Strict/Standard mode across `src`, `tests`, `ui` | ✅ **0 Errors, 0 Warnings** |
| **Statement Coverage** | Coverage.py (`pytest-cov`) | Minimum 70.0% | ✅ **94.60%** |
| **Branch Coverage** | Coverage.py (`pytest-cov`) | Minimum 75.0% | ✅ **77.60%** (298/384 branches) |
| **Combined Coverage** | Coverage.py (`pytest-cov`) | Harmonic line/branch balance | ✅ **92.23%** |
| **Security SAST** | Bandit | Zero High/Medium severity issues | ✅ **0 Issues (3,739 lines)** |
| **Dependency Auditing** | pip-audit | Zero known vulnerable packages | ✅ **0 Vulnerabilities** |

---

## 2. Test Architecture & Structure

```
tests/
├── conftest.py                   # Shared in-memory configured SQLite fixtures
├── integration/
│   └── test_pipeline_e2e.py      # Multi-channel E2E flow (Mock API, SQLite, Analytics, AI)
└── unit/
    ├── test_ai.py                # Pydantic schemas, prompt escaping, fallback behavior
    ├── test_classifier.py        # YouTube rules cutoff (15/10/2024, 08/12/2025 OAC)
    ├── test_cli.py               # Headless CLI commands, exit codes (0, 1, 2, 3), offline use
    ├── test_config.py            # Pydantic Settings precedence (.env, secrets.toml)
    ├── test_database.py          # Foreign keys, snapshot isolation, WAL backup, purge
    ├── test_growth.py            # Growth signals, negative view count adjustments
    ├── test_metrics.py           # Median, percentiles, VPD, VSR, likes-to-views ratio
    ├── test_pipeline_service.py  # Orchestrator, partial tolerance, AST boundary inspection
    └── test_youtube_client.py    # Quota handling, typed HTTP errors, pagination order
```

---

## 3. Notable Architectural Tests

### A. Python AST Architectural Boundary Test
Rather than fragile substring search, [`test_architecture_boundaries_ast_inspection()`](tests/unit/test_pipeline_service.py) inspects the abstract syntax tree (`ast.parse`) of every file in `ui/`. 

It validates that UI modules only import from permitted high-level packages (`echoscope.services`, `echoscope.models`, `streamlit`, `pandas`, `plotly`) and strictly forbids direct imports of:
- `sqlite3`
- `echoscope.database.repository`
- `echoscope.api.youtube_client`
- `echoscope.ai.gemini_client`
- `googleapiclient`
- `google.genai`

### B. Full Pipeline End-to-End Simulation
[`test_pipeline_e2e_full_lifecycle()`](tests/integration/test_pipeline_e2e.py) validates a complete three-channel batch:
- **Channel A:** 20 videos collected successfully.
- **Channel B:** 15 videos collected successfully.
- **Channel C:** Network failure (`NetworkError`).
- **Verifications:** Exactly 35 videos saved, run marked as `partial`, deterministic metrics computed, Gemini strategic report generated with Pydantic validation, and subsequent offline queries executed without touching the external API.

### C. CLI Exit Codes & Automated Scripting
The CLI enforces distinct Unix-compliant exit codes tested in [`test_cli.py`](tests/unit/test_cli.py):
- `0` (`EXIT_SUCCESS`): Complete batch success.
- `1` (`EXIT_ERROR`): Critical runtime failure.
- `2` (`EXIT_INVALID_ARGUMENTS`): Argument parsing or date syntax error.
- `3` (`EXIT_PARTIAL`): Partial collection (at least one channel succeeded, at least one failed).

---

## 4. Reproducing Quality Checks Standalone

Any contributor can reproduce all quality gates locally without external scripts:

```bash
ruff check .
ruff format --check .
pyright
pytest -v --cov=src/echoscope --cov-branch --cov-report=term-missing --cov-fail-under=70
bandit -r src -ll
pip-audit
```
