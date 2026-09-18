# syntax=docker/dockerfile:1
#
# Backend image for the AI Interview Simulator.
# Build from the REPOSITORY ROOT so the backend package is in context:
#   docker build -f backend/Dockerfile -t interview-simulator-backend .

# ---------------------------------------------------------------------------
# Stage 1 — build the dependency tree and pre-fetch models
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/hf-cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf-cache

# build-essential is needed by a few wheels that lack manylinux builds.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# torch first, from the CPU-only index. sentence-transformers depends on torch,
# and the default PyPI wheel on Linux is the CUDA build (~2.5 GB) which this
# service never uses. Installing CPU-only up front means the later resolve finds
# the requirement already satisfied.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Pre-fetch both models that would otherwise be downloaded from HuggingFace on
# first use: the role-matching encoder and the speech-to-text model. Baking them
# in keeps cold starts fast and lets the runtime stage run fully offline, so a
# HuggingFace outage cannot degrade a running deployment.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" \
    && python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')"

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf-cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf-cache \
    # Both models are baked in above, so no network call to HuggingFace is
    # needed or wanted at runtime.
    #
    # If you change a model name without also updating the pre-fetch step, the
    # load fails against this offline cache. That does NOT stop the container:
    # role matching catches the failure and falls back to token-overlap
    # matching, which is materially worse, while /health still returns 200.
    # (An earlier version of this comment claimed startup would fail. It does
    # not — verified.) The fallback is now logged as a warning, and
    # REQUIRE_SEMANTIC_ENCODER below turns it into a hard startup failure.
    HF_HUB_OFFLINE=1 \
    # Make a missing semantic encoder fatal rather than a silent downgrade.
    # Set in the image because the models ARE baked in here, so a failure to
    # load one is a real packaging error and should stop the deployment rather
    # than quietly degrade every candidate's role matches. Unset it if you
    # deliberately want to run on the token-overlap fallback.
    REQUIRE_SEMANTIC_ENCODER=true

# curl is used by the container HEALTHCHECK below.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf-cache /opt/hf-cache

# Run as an unprivileged user. Created before COPY so the layer is cached.
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --chown=appuser:appuser backend/ /app/backend/

USER appuser
EXPOSE 8000

# Liveness only — /health does not touch MongoDB, so it reports whether the
# process is serving, not whether every dependency is reachable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8000/health || exit 1

# No --reload: that is a development-only flag.
#
# Two caveats when raising UVICORN_WORKERS:
#   1. The auth rate limiter in backend/api/rate_limit.py keeps its counters in
#      process memory, so N workers multiply the effective limit by N. See
#      README section 7.7 before scaling this up.
#   2. Each worker loads its own copy of the sentence-transformers and whisper
#      models, so memory scales roughly linearly with worker count.
ENV UVICORN_WORKERS=1

# Rate limiting keys on the direct peer address, because backend/api/routes_auth.py
# deliberately refuses to trust X-Forwarded-For (an attacker could otherwise mint a
# fresh budget per request by inventing a value). The consequence is that behind a
# reverse proxy every user collapses into ONE bucket: the whole deployment shares a
# single 10-attempts-per-minute auth budget, and one user tripping it locks out
# everybody.
#
# --proxy-headers makes uvicorn rewrite request.client from the forwarded headers,
# but only for peers listed in --forwarded-allow-ips, so the header is still not
# blindly trusted. The default below is uvicorn's own: localhost only, which means
# nothing changes for a direct or docker-compose run.
#
# A proxied deployment MUST widen this to the proxy's address. On a platform where
# the container is only reachable through the platform's own ingress (Hugging Face
# Spaces, Cloud Run), "*" is the practical value:
#     UVICORN_FORWARDED_ALLOW_IPS=*
# Do not set "*" anywhere the container port is reachable directly, or the header
# becomes forgeable again and the rate limit can be bypassed per request.
ENV UVICORN_FORWARDED_ALLOW_IPS=127.0.0.1

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS} --proxy-headers --forwarded-allow-ips \"${UVICORN_FORWARDED_ALLOW_IPS}\""]
