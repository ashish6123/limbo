FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .
# Render passes service env vars as build args; NEXT_PUBLIC_* must exist at build time.
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_DASHBOARD_API_KEY
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_DASHBOARD_API_KEY=$NEXT_PUBLIC_DASHBOARD_API_KEY
RUN npm run build

ENV PORT=3000
CMD ["sh", "-c", "npx next start -p ${PORT}"]
