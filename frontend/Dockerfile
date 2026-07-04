FROM node:20-alpine

RUN mkdir -p /frontend

WORKDIR /frontend

COPY package*.json /frontend

RUN npm ci

COPY . /frontend

RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]