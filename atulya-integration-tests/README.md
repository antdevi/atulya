# Atulya Integration Tests

E2E and integration tests for Atulya API that require a running server.

## Test Types

### 1. Tests with External Server
Tests like `test_mcp_e2e.py` expect a server to already be running.

**Running:**
```bash
# Start the API server
./scripts/dev/start-api.sh

# Run tests
cd atulya-integration-tests
ATULYA_API_URL=http://localhost:8888 uv run pytest tests/test_mcp_e2e.py -v
```

### 2. Self-Contained Tests
Tests like `test_base_path_deployment.py` manage their own server lifecycle and use docker-compose.

**Running:**
```bash
cd atulya-integration-tests

# Run with pytest
uv run pytest tests/test_base_path_deployment.py -v

# Or run directly for nice output
uv run python tests/test_base_path_deployment.py
```

**Requirements:**
- Docker and docker-compose installed (for reverse proxy test)
- No nginx required on host!

**What it tests:**
- ✅ API with base path (direct server)
- ✅ Full reverse proxy via docker-compose + Nginx
- ✅ Regression: API without base path
- ✅ Full retain/recall workflow

These tests:
- Start their own API servers on dedicated ports (18888-18891)
- Use docker-compose to test actual deployment scenarios
- Run in parallel with other tests (no port conflicts)
- Clean up automatically

## Running All Tests

```bash
cd atulya-integration-tests
uv run pytest tests/ -v
```

This runs both types. Self-contained tests won't conflict with the external server.

## Memory-to-Skill Benchmark

This repo now includes a deterministic benchmark harness for the wedge Atulya wants to own:
turning repeated experience into a reusable skill or decision rule.

It covers 24 scenarios across:
- temporal correction
- contradiction handling
- skill emergence
- portability through `.brain` artifacts

Run it locally with one command from the repo root:

```bash
uv run --directory atulya-integration-tests atulya-benchmark
```

Run the real API-backed experiment mode:

```bash
uv run --directory atulya-integration-tests atulya-benchmark --mode live-api
```

This boots a local Atulya API with `pg0`, drives real HTTP retain/recall calls,
and exercises actual `.brain` export/import for the portability scenarios.

Outputs are written to:
- `atulya-integration-tests/benchmark-results/leaderboard.json`
- `atulya-integration-tests/benchmark-results/leaderboard.md`
- `atulya-integration-tests/benchmark-results/leaderboard.live.json`
- `atulya-integration-tests/benchmark-results/leaderboard.live.md`

The harness compares a naive `plain_recall` strategy against a `memory_to_skill`
strategy and fails if the checked-in benchmark contract regresses.

## Environment Variables

- `ATULYA_API_URL` - Base URL for external-server tests (default: `http://localhost:8888`)
