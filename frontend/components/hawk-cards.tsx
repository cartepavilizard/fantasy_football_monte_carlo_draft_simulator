// Re-export shim: the real implementation lives in the Hawk UI design-system
// package (frontend/packages/hawk-ui/src/hawk-cards.tsx) so it can be built
// and synced to claude.ai/design independently. Existing imports from
// "@/components/hawk-cards" keep working unchanged.
export * from "@hawkmode/ui/hawk-cards";
