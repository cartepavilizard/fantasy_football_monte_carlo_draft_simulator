/** @type {import('next').NextConfig} */
const nextConfig = {
  // Production builds can write to their own directory so `npm run build`
  // never corrupts a running dev server's .next cache: set
  // NEXT_DIST_DIR=.next-build when building while dev is up.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

module.exports = nextConfig;
