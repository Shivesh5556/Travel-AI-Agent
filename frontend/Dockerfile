FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173

# --host makes Vite listen outside the container, not just on localhost
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
