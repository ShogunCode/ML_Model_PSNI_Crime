FROM node:20-alpine

WORKDIR /app
COPY frontend/package*.json ./
RUN npm install

COPY frontend .
ENV VITE_API_URL=http://localhost:8000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
