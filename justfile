# Task runner for the takibiapi workspace. `just` comes with `uv sync` (the rust-just dev dependency).
# uv manages the Python workspace (packages/*), Cargo the Rust extension of takibiapi, and
# `uv build` / maturin produce the distributions that go to PyPI.

set shell := ["bash", "-euo", "pipefail", "-c"]

# List the recipes
default:
    @just --list

# Install every workspace member editable into .venv (builds the Rust extension with maturin)
sync:
    uv sync

# Rebuild the Rust extension into .venv after changing packages/takibiapi/src
develop:
    uv sync --reinstall-package takibiapi

# Run pytest over every package, or the given paths: `just test packages/takibiapi/tests/test_app.py`
test *args:
    uv run pytest {{ args }}

# Run the Rust unit tests
test-rust *args:
    cargo test --workspace {{ args }}

# Format Python (ruff) and Rust (rustfmt); apply safe lint fixes
fmt:
    uv run ruff check --fix .
    uv run ruff format .
    cargo fmt --all

# Lint and check formatting without changing files: ruff, clippy, rustfmt, actionlint, zizmor
lint:
    uv run ruff check .
    uv run ruff format --check .
    cargo clippy --workspace --all-targets -- -D warnings
    cargo fmt --all --check
    uv run actionlint
    uv run zizmor --offline .github

# Type-check with pyright
typecheck:
    uv run pyright

# Everything CI runs
check: lint typecheck test test-rust

# Build the wheels and sdist of one package into dist/, e.g. `just dist takibiapi`
dist package:
    uv build --package {{ package }} --out-dir dist
