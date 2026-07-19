// Re-export shim: the real implementation lives in the Hawk UI design-system
// package (frontend/packages/hawk-ui/src/draft-monte-carlo-panel.tsx) so it
// can be built and synced to claude.ai/design independently. Existing imports
// from "@/components/draft-monte-carlo-panel" keep working unchanged.
export * from "@hawkmode/ui/draft-monte-carlo-panel";
