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
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
