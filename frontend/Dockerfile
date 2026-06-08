# Stage 1: Build the React Application
FROM node:20-alpine AS build
WORKDIR /app

ARG VITE_AZURE_CLIENT_ID
ARG VITE_AZURE_TENANT_ID

ENV VITE_AZURE_CLIENT_ID=$VITE_AZURE_CLIENT_ID
ENV VITE_AZURE_TENANT_ID=$VITE_AZURE_TENANT_ID

COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Serve using Nginx
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html

# nginx config is rendered from a template at startup so the backend URL can be
# configured per environment. The official nginx image runs envsubst over files
# in /etc/nginx/templates. NGINX_ENVSUBST_FILTER limits substitution to
# BACKEND_URL so nginx's own $variables (e.g. $host) are left untouched.
COPY default.conf.template /etc/nginx/templates/default.conf.template
ENV BACKEND_URL=http://backend:8001
ENV NGINX_ENVSUBST_FILTER=BACKEND_URL

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
