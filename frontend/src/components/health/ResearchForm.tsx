// ---------------------------------------------------------------------------
// ResearchForm — reusable add/edit form for health research notes [bu-wamzk]
//
// Mirrors ConditionForm (bu-a7vw9): a controlled form with per-field state, a
// single `onSubmit` that builds the request body and calls a create/update
// mutation, inline validation for the required title and content fields, a
// disabled submit while pending, and toast feedback that surfaces the API error
// message.
//
// Research notes are PROPERTY facts (like conditions, NOT temporal): an edit
// supersedes the prior note keyed on its `research:{title}` subject. Pair this
// form with the `useCreateResearch` / `useUpdateResearch` hooks in
// hooks/use-health.ts.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { HealthResearch } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCreateResearch, useUpdateResearch } from "@/hooks/use-health";

interface ResearchFormProps {
  /** When provided, the form edits this note; otherwise it creates a new one. */
  research?: HealthResearch;
  /** Called after a successful create/update so the caller can close the dialog. */
  onDone: () => void;
  /** Called when the user cancels. */
  onCancel: () => void;
}

/** Split a comma-separated tag string into a trimmed, de-duplicated list. */
function parseTags(raw: string): string[] {
  const seen = new Set<string>();
  for (const part of raw.split(",")) {
    const tag = part.trim();
    if (tag) seen.add(tag);
  }
  return Array.from(seen);
}

export function ResearchForm({ research, onDone, onCancel }: ResearchFormProps) {
  const isEdit = research != null;

  const [title, setTitle] = useState(research?.title ?? "");
  const [content, setContent] = useState(research?.content ?? "");
  const [tags, setTags] = useState((research?.tags ?? []).join(", "));
  const [sourceUrl, setSourceUrl] = useState(research?.source_url ?? "");

  const createMutation = useCreateResearch();
  const updateMutation = useUpdateResearch();
  const isPending = createMutation.isPending || updateMutation.isPending;

  function handleError(err: unknown) {
    const message =
      err instanceof ApiError ? err.message : "Something went wrong saving the research note.";
    toast.error(message);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      toast.error("Title is required.");
      return;
    }

    const trimmedContent = content.trim();
    if (!trimmedContent) {
      toast.error("Content is required.");
      return;
    }

    const parsedTags = parseTags(tags);
    const trimmedSource = sourceUrl.trim();

    try {
      if (isEdit) {
        await updateMutation.mutateAsync({
          id: research.id,
          body: {
            title: trimmedTitle,
            content: trimmedContent,
            tags: parsedTags,
            source_url: trimmedSource === "" ? null : trimmedSource,
          },
        });
        toast.success("Research note updated.");
      } else {
        await createMutation.mutateAsync({
          title: trimmedTitle,
          content: trimmedContent,
          tags: parsedTags,
          source_url: trimmedSource === "" ? null : trimmedSource,
        });
        toast.success("Research note added.");
      }
      onDone();
    } catch (err) {
      handleError(err);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" data-testid="research-form">
      <div className="space-y-2">
        <Label htmlFor="research-title">Title</Label>
        <Input
          id="research-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Magnesium and sleep"
          autoFocus
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="research-content">Content</Label>
        <Textarea
          id="research-content"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Summary, findings, or notes about this research."
          rows={5}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="research-tags">Tags (optional)</Label>
          <Input
            id="research-tags"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="comma, separated, tags"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="research-source">Source URL (optional)</Label>
          <Input
            id="research-source"
            type="url"
            value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)}
            placeholder="https://example.com/study"
          />
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={isPending}>
          {isEdit ? "Save changes" : "Add research"}
        </Button>
      </div>
    </form>
  );
}
