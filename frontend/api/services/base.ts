// Next.js inlines NEXT_PUBLIC_* variables at build time, so set
// NEXT_PUBLIC_API_URL as a build arg (see docker-compose.yml) to deploy
// against a backend that is not on localhost
export const baseQuery = {
  baseUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
};
