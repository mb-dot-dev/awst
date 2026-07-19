# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`awst` (AWS Console Terminal UI) is a TUI application built on [Textual](https://textual.textualize.io/). At startup the app shows a profile-selector screen when no AWS profile is active and profiles exist in `~/.aws/config`; otherwise (or once a profile is chosen) it opens on the service-menu home screen, with the active profile shown in the header. `ctrl+g` opens a region picker from any screen; switching region resets the cached gateways and returns to the home screen, with the header showing `profile @ region`. Implemented services are CloudFormation (stack list), S3 (bucket list with an empty-bucket action and a read-only object browser: Enter drills into buckets and folders, `m` loads the next page, cross-region buckets use per-region clients), Lambda (function list), and SQS (queue list). On credential failures, list screens for SSO-configured profiles offer `l` to run an in-process SSO OIDC device login. Requires Python >=3.14. Dependency management and packaging use `uv`.

## Commands

All commands run through `uv` and are wrapped by the `Makefile`; prefer the `make` targets.

- `make install-dev` — install all dependencies (including dev group) via `uv sync --frozen`.
- `make install` — install production-only dependencies.
- `make lint` — run `ruff check`, `ruff format --check`, and `ty check` (type checking). Run this before considering any change complete.
- `make format` — auto-format code with `ruff format`.
- `make unit` — run the test suite (`uv run --frozen pytest`).
- `make test` — run `lint` then `unit`; this is the full local check, and mirrors CI.
- `make coverage` — run tests with coverage, writing `build/coverage.xml` (coverage fails under 75%).

Run a single test directly with uv/pytest, e.g.:
```
uv run --frozen pytest tests/test_app.py::test_q_quits_from_home
```

There is no separate typecheck-only Makefile target; `ty check` is invoked as part of `make lint`.

## Architecture

- `src/awst/__init__.py` exposes `main()`, the console-script entry point (`awst` command, see `[project.scripts]` in `pyproject.toml`), which runs `AwstApp` (`src/awst/app.py`).
- `AwstApp` owns AWS access: it lazily builds gateways (e.g. `CloudFormationGateway`) from a `boto3.Session` using the default credential chain, and hands them to screens. Screens never import boto3/botocore.
- `src/awst/aws/` is the AWS layer: `models.py` (frozen dataclasses + `AwsError`), `errors.py` (botocore → `AwsError` mapping, reusable by future gateways), `profiles.py` (profile discovery/selection + SSO config resolution), `regions.py` (region discovery/selection), `sso.py` (SSO OIDC device-flow gateway + token cache writer), and one gateway module per service (`cloudformation.py`, `s3.py`, `lambda_.py`, `sqs.py`).
- `src/awst/screens/` holds one Textual `Screen` per page (`home.py`, `stacks.py`, `buckets.py`, `functions.py`, `queues.py`, `profiles.py` for the profile picker, `regions.py` for the region picker, `sso_login.py` for the SSO login modal, `empty_bucket.py` for the empty-bucket progress modal, `objects.py` for the read-only S3 object browser (one screen per prefix level)), with list screens subclassing `ResourceListScreen` (`resource_list.py`), and pure presentation helpers (`formatting.py`). Screens load data with thread workers (`@work(thread=True, exclusive=True, exit_on_error=False)`) and handle results in `on_worker_state_changed`.
- Adding a service = one new gateway module + one new screen module + an entry in `SERVICES` in `screens/home.py`.
- Tests: UI tests drive the app headlessly with pytest-asyncio + Textual's `run_test()` pilot, injecting `FakeCloudFormationGateway` (`tests/fakes.py`); gateway tests use moto's `mock_aws` or botocore `Stubber` (no network; `sso-oidc` has no moto support, so it's Stubber-only). `tests/conftest.py` sets fake AWS credentials for every test, isolates AWS config files into `tmp_path`, and scrubs `AWS_PROFILE`.

## Linting conventions

Ruff is configured with a broad rule set (see `[tool.ruff.lint]` in `pyproject.toml`), including flake8-annotations, bandit (security), bugbear, complexity, pathlib, and more, with a 120-char line length. Notable per-file relaxations: `tests/**/*.py` may use `assert`, hardcoded-looking values/strings, local imports inside functions, and `print`.

## CI

- `.github/workflows/main.yaml` runs on push/PR to `main`: installs deps, lints, runs coverage, then submits results to SonarQube/SonarCloud (project key `mb-dot-dev_awst`).
- `.github/workflows/release.yaml` publishes to PyPI via trusted publishing when a `v*.*.*` tag is pushed (sets the version from the tag, builds, and publishes with `uv`).
