// Re-export shim: the real implementation lives in the Hawk UI design-system
// package (frontend/packages/hawk-ui/src/image-slot.tsx) so it can be built
// and synced to claude.ai/design independently. Existing imports from
// "@/components/image-slot" keep working unchanged.
export * from "@hawkmode/ui/image-slot";
