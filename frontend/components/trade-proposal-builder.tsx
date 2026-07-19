// Re-export shim: the real implementation lives in the Hawk UI design-system
// package (frontend/packages/hawk-ui/src/trade-proposal-builder.tsx) so it can
// be built and synced to claude.ai/design independently. Existing imports from
// "@/components/trade-proposal-builder" keep working unchanged.
export * from "@hawkmode/ui/trade-proposal-builder";
