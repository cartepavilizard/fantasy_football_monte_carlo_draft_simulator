// Re-export shim: the real implementation lives in the Hawk UI design-system
// package (frontend/packages/hawk-ui/src/trade-header.tsx) so it can be built
// and synced to claude.ai/design independently. Existing imports from
// "@/components/trade-header" keep working unchanged.
export * from "@hawkmode/ui/trade-header";
