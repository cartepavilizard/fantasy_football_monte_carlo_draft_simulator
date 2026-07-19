// Re-export shim: the real implementation lives in the Hawk UI design-system
// package (frontend/packages/hawk-ui/src/inseason-lineup-optimizer.tsx) so it
// can be built and synced to claude.ai/design independently. Existing imports
// from "@/components/inseason-lineup-optimizer" keep working unchanged.
export * from "@hawkmode/ui/inseason-lineup-optimizer";
