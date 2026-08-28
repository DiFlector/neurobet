FROM node:22-alpine AS builder

WORKDIR /app

# Prefer IPv4: in Docker, Node/undici often hangs on IPv6 to registry.npmjs.org
# and corepack then dies with `Type Error: terminated` (~75s TLS abort).
ENV NODE_OPTIONS=--dns-result-order=ipv4first

# npm ships with the image; corepack prepare hits the registry with no retries.
RUN npm install -g pnpm@10 --fetch-retries=5 --fetch-retry-mintimeout=20000 --fetch-retry-maxtimeout=120000

COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install

COPY . .

# NEXT_PUBLIC_* variables are inlined into the client JS bundle at `next build` time —
# setting them as a plain runtime `environment:` in docker-compose does NOT affect
# already-built code, only server-side code running after `pnpm start`. So this has to
# come in as a build ARG (passed via docker-compose's `build.args`, sourced from .env)
# and get baked in here, before the build step.
#
# На проде корень сайта = /neurobet:
#   NEXT_PUBLIC_API_URL=/neurobet  → fetch идёт на /neurobet/api/...
#   NEXT_PUBLIC_BASE_PATH=/neurobet → Next.js basePath (ассеты, Link)
# Пустой API_URL допустим только локально без basePath (тогда браузер бьёт в /api/...).
ARG NEXT_PUBLIC_API_URL="/neurobet"
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

ARG NEXT_PUBLIC_BASE_PATH="/neurobet"
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH

# next.config.ts's rewrites() is evaluated at `next build` time and its destination is
# baked into .next/routes-manifest.json — a runtime-only `environment:` value in
# docker-compose is too late to affect it. So BACKEND_INTERNAL_URL must also be
# available here, as a build ARG, matching the runtime default of "http://backend:8000".
ARG BACKEND_INTERNAL_URL="http://backend:8000"
ENV BACKEND_INTERNAL_URL=$BACKEND_INTERNAL_URL

ARG NEXT_PUBLIC_DEPLOY_MODE="prod"
ENV NEXT_PUBLIC_DEPLOY_MODE=$NEXT_PUBLIC_DEPLOY_MODE

RUN pnpm run build

FROM node:22-alpine AS runner

WORKDIR /app

ARG NEXT_PUBLIC_API_URL="/neurobet"
ARG NEXT_PUBLIC_BASE_PATH="/neurobet"
ENV NODE_ENV=production
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
ARG NEXT_PUBLIC_DEPLOY_MODE="prod"
ENV NEXT_PUBLIC_DEPLOY_MODE=$NEXT_PUBLIC_DEPLOY_MODE
ENV PORT=3000

COPY --from=builder /app/package.json ./
COPY --from=builder /app/next.config.ts ./
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules

EXPOSE 3000

# next is already in node_modules — no second pnpm download in this stage.
CMD ["node", "./node_modules/next/dist/bin/next", "start"]
