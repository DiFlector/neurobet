FROM node:20-alpine

WORKDIR /app

COPY services/frontend/package*.json ./
RUN npm install --omit=dev

COPY services/frontend/server.js ./
COPY services/frontend/public ./public

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:3000/health || exit 1

CMD ["node", "server.js"]
