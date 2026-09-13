# ── Fintraxa Frontend (React 19 + nginx) ──
# Multi-stage: Node builds the React app, nginx serves it

# Stage 1: Build
FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json ./
RUN npm install --production=false
COPY . .
RUN npm run build

# Stage 2: Serve
FROM nginx:alpine
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
