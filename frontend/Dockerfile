FROM node:20-alpine

RUN mkdir -p /frontend

WORKDIR /frontend

COPY package*.json /frontend

RUN npm ci

COPY . /frontend

# Next.js inlines NEXT_PUBLIC_* at build time, so this must be a build arg
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]