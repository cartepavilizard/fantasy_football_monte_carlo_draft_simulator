// Re-export shim: the real implementation lives in the Hawk UI design-system
// package (frontend/packages/hawk-ui/src/draft-on-clock.tsx) so it can be
// built and synced to claude.ai/design independently. Existing imports from
// "@/components/draft-on-clock" keep working unchanged.
export * from "@hawkmode/ui/draft-on-clock";
