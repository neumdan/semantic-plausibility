FROM docker.io/library/node:24.19.0-bookworm-slim
WORKDIR /app
ENV UCUM_HOST=0.0.0.0
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY server.cjs ./
USER node
EXPOSE 8080
CMD ["node", "server.cjs"]
