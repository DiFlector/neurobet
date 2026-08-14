FROM node:22-alpine AS builder

WORKDIR /app

# Enable corepack for pnpm v10
RUN corepack enable && corepack prepare pnpm@10 --activate

COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install

COPY . .

# NEXT_PUBLIC_* variables are inlined into the client JS bundle at `next build` time —
# setting them as a plain runtime `environment:` in docker-compose does NOT affect
# already-built code, only server-side code running after `pnpm start`. So this has to
# come in as a build ARG (passed via docker-compose's `build.args`, sourced from .env)
# and get baked in here, before the build step.
#
# Default is empty on purpose: an empty NEXT_PUBLIC_API_URL makes the client fetch
# same-origin relative paths ("/api/..."), which next.config.ts then proxies server-side
# to the backend — see that file. Only set NEXT_PUBLIC_API_URL if you deliberately want
# the browser to call the backend directly on its own exposed origin/port instead.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# next.config.ts's rewrites() is evaluated at `next build` time and its destination is
# baked into .next/routes-manifest.json — a runtime-only `environment:` value in
# docker-compose is too late to affect it. So BACKEND_INTERNAL_URL must also be
# available here, as a build ARG, matching the runtime default of "http://backend:8000".
ARG BACKEND_INTERNAL_URL="http://backend:8000"
ENV BACKEND_INTERNAL_URL=$BACKEND_INTERNAL_URL

RUN pnpm run build

FROM node:22-alpine AS runner

WORKDIR /app

ARG NEXT_PUBLIC_API_URL=""
ENV NODE_ENV=production
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV PORT=3000

RUN corepack enable && corepack prepare pnpm@10 --activate

COPY --from=builder /app/package.json ./
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules

EXPOSE 3000

CMD ["pnpm", "start"]
