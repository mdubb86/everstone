set shell := ["bash", "-cu"]
IMAGE := "everstone:dev"
NAME := "everstone-e2e"

build:
    docker build -t {{IMAGE}} .

up: build
    e2e/.venv/bin/python e2e/up.py

test:
    cd e2e && uv run pytest -v

down:
    docker rm -f {{NAME}} 2>/dev/null || true

e2e: build
    cd e2e && uv run pytest -v
