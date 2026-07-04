FROM node:20-alpine

RUN mkdir -p /frontend

WORKDIR /frontend

COPY package*.json /frontend

RUN npm ci

COPY . /frontend

EXPOSE 3000

CMD ["npm", "run", "dev"]