// Re-export shim: the real implementation lives in the Hawk UI design-system
// package (frontend/packages/hawk-ui/src/draft-my-roster.tsx) so it can be
// built and synced to claude.ai/design independently. Existing imports from
// "@/components/draft-my-roster" keep working unchanged.
export * from "@hawkmode/ui/draft-my-roster";
