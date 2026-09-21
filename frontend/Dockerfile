# syntax=docker/dockerfile:1
#
# Frontend image: Vite build served as static files by nginx.
# Build from the frontend/ directory:
#   docker build -t interview-simulator-frontend ./frontend
#
# VITE_API_BASE_URL is baked in at BUILD time, not read at runtime — Vite
# substitutes import.meta.env values during the build. A deployment pointing at
# a different API host therefore needs its own build:
#   docker build --build-arg VITE_API_BASE_URL=https://api.example.com ./frontend

# ---------------------------------------------------------------------------
# Stage 1 — build
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# Copy manifests first so the dependency layer is cached independently of source
# changes. npm ci installs exactly the lockfile, unlike npm install.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG VITE_API_BASE_URL=http://127.0.0.1:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — serve
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html

# Rendered at container start by the official image's envsubst step, so the CSP
# can name a deployment's real API origin without rebuilding the image.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Restrict substitution to CSP_* so nginx's own $uri / $host / $request_uri are
# never candidates. Without a filter the entrypoint substitutes every variable
# present in the environment, which is a foot-gun waiting for someone to export
# something unfortunate.
ENV NGINX_ENVSUBST_FILTER=^CSP_

# Content-Security-Policy, derived from what the built bundle actually loads
# rather than from a template:
#
#   script-src   cdn.jsdelivr.net — @monaco-editor/react has no loader.config(),
#                so the DSA editor is fetched from jsdelivr at RUNTIME. See the
#                note in the deployment plan; self-hosting it would let this be
#                tightened to 'self'.
#   'wasm-unsafe-eval' — onnxruntime, behind the browser-side Silero VAD,
#                compiles WebAssembly. Without it the microphone path dies.
#   style-src    'unsafe-inline' + fonts.googleapis.com — styles.css @imports
#                Google Fonts, and React inline styles (including the error
#                boundary) are inline by construction.
#   worker-src   blob: — Monaco creates its workers from blob URLs.
#   connect-src  overridden per deployment; the API and WebSocket live on a
#                different origin. Default stays broad so a misconfiguration
#                degrades to "less strict" rather than "app is broken".
ENV CSP_POLICY="default-src 'self'; script-src 'self' 'wasm-unsafe-eval' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; media-src 'self' blob:; worker-src 'self' blob:; connect-src 'self' https: wss:; frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'"

# Report-only by default, and that is a considered choice rather than timidity.
# An enforcing CSP that is even slightly wrong breaks the app silently in a
# browser, and this policy has not yet been exercised against a real session
# with the editor and microphone in use. Report-only sends the same violations
# to the console so the policy can be tuned against real traffic first.
#
# Switch to enforcing once a full interview has been driven with a clean
# console:
#     CSP_HEADER_NAME=Content-Security-Policy
ENV CSP_HEADER_NAME=Content-Security-Policy-Report-Only

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
