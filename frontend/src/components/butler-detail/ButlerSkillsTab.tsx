import { useState } from "react";
import { useSearchParams } from "react-router";

import { CardSkeleton } from "@/components/skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useButlerSkills } from "@/hooks/use-butlers";
import type { ButlerSkill } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ButlerSkillsTabProps {
  butlerName: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extract the first non-empty line of content as a short description. */
function firstLine(content: string): string {
  const lines = content.split("\n");
  for (const line of lines) {
    const trimmed = line.trim();
    // Skip markdown headings and empty lines
    if (trimmed && !trimmed.startsWith("#")) {
      return trimmed.length > 120 ? trimmed.slice(0, 120) + "..." : trimmed;
    }
  }
  return "No description";
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function SkillsSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 3 }, (_, i) => (
        <CardSkeleton key={i} lines={2} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skill Detail Dialog
// ---------------------------------------------------------------------------

function SkillDetailDialog({
  skill,
  open,
  onOpenChange,
}: {
  skill: ButlerSkill | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (!skill) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{skill.name}</DialogTitle>
          <DialogDescription>Full SKILL.md content</DialogDescription>
        </DialogHeader>
        <pre className="overflow-auto rounded-md bg-muted p-4 text-sm font-mono whitespace-pre-wrap">
          {skill.content}
        </pre>
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// ButlerSkillsTab
// ---------------------------------------------------------------------------

export default function ButlerSkillsTab({ butlerName }: ButlerSkillsTabProps) {
  const { data: skillsResponse, isLoading, isError, error } = useButlerSkills(butlerName);
  const [selectedSkill, setSelectedSkill] = useState<ButlerSkill | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [, setSearchParams] = useSearchParams();

  if (isLoading) {
    return <SkillsSkeleton />;
  }

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Skills</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            Failed to load skills: {error instanceof Error ? error.message : "Unknown error"}
          </p>
        </CardContent>
      </Card>
    );
  }

  const skills = skillsResponse?.data ?? [];

  if (skills.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Skills</CardTitle>
          <CardDescription>Skills available to this butler</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No skills registered</p>
        </CardContent>
      </Card>
    );
  }

  function handleViewSkill(skill: ButlerSkill) {
    setSelectedSkill(skill);
    setDialogOpen(true);
  }

  function handleTriggerSkill(skillName: string) {
    setSearchParams({ tab: "trigger", skill: skillName }, { replace: true });
  }

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {skills.map((skill) => (
          <Card key={skill.name} className="flex flex-col justify-between">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {skill.name}
                <Badge variant="secondary" className="text-xs">
                  skill
                </Badge>
              </CardTitle>
              <CardDescription>{firstLine(skill.content)}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleViewSkill(skill)}
                >
                  View
                </Button>
                <Button
                  size="sm"
                  onClick={() => handleTriggerSkill(skill.name)}
                >
                  Trigger
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <SkillDetailDialog
        skill={selectedSkill}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
      />
    </>
  );
}
