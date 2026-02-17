# Goose coding agent with Python and Node.js runtimes
# The base Goose image has bash but no language runtimes,
# making the developer extension's shell tool much less useful.

FROM ghcr.io/block/goose:latest

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Make python3 available as "python"
RUN ln -sf /usr/bin/python3 /usr/bin/python

USER goose
