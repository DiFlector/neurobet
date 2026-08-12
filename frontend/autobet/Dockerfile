FROM node:22-alpine AS builder

WORKDIR /app

# Enable corepack for pnpm v10
RUN corepack enable && corepack prepare pnpm@10 --activate

COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install

COPY . .

# Environment variable for API host during build / runtime
ENV NEXT_PUBLIC_API_URL=http://localhost:8000

RUN pnpm run build

FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_PUBLIC_API_URL=http://localhost:8000
ENV PORT=3000

RUN corepack enable && corepack prepare pnpm@10 --activate

COPY --from=builder /app/package.json ./
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules

EXPOSE 3000

CMD ["pnpm", "start"]
