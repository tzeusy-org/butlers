import { useCallback, useEffect, useMemo, useState } from "react";

import { callButlerMcpTool, getButlerMcpTools } from "@/api/index.ts";
import { ApiError } from "@/api/client.ts";
import type { ButlerMcpTool, ButlerMcpToolCallResponse } from "@/api/types.ts";
import JsonViewer from "@/components/general/JsonViewer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

interface ButlerMcpTabProps {
  butlerName: string;
}

export default function ButlerMcpTab({ butlerName }: ButlerMcpTabProps) {
  const [tools, setTools] = useState<ButlerMcpTool[]>([]);
  const [isLoadingTools, setIsLoadingTools] = useState(false);
  const [toolLoadError, setToolLoadError] = useState<string | null>(null);

  const [selectedTool, setSelectedTool] = useState("");
  const [argumentsText, setArgumentsText] = useState("");
  const [argumentsError, setArgumentsError] = useState<string | null>(null);

  const [isCalling, setIsCalling] = useState(false);
  const [callError, setCallError] = useState<string | null>(null);
  const [lastResponse, setLastResponse] = useState<ButlerMcpToolCallResponse | null>(null);

  const canCall = selectedTool.trim().length > 0 && !isCalling;

  const loadTools = useCallback(async () => {
    setIsLoadingTools(true);
    setToolLoadError(null);

    try {
      const response = await getButlerMcpTools(butlerName);
      const nextTools = response.data ?? [];
      setTools(nextTools);
      setSelectedTool((previous) => {
        if (nextTools.length === 0) return "";
        if (!previous) return nextTools[0].name;
        if (nextTools.some((tool) => tool.name === previous)) return previous;
        return nextTools[0].name;
      });
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Failed to load MCP tools";
      setToolLoadError(message);
      setTools([]);
      setSelectedTool("");
    } finally {
      setIsLoadingTools(false);
    }
  }, [butlerName]);

  useEffect(() => {
    void loadTools();
  }, [loadTools]);

  const selectedToolDescription = useMemo(
    () => tools.find((tool) => tool.name === selectedTool)?.description ?? null,
    [tools, selectedTool],
  );

  const handleCall = useCallback(async () => {
    const toolName = selectedTool.trim();
    if (!toolName || isCalling) return;

    let parsedArguments: Record<string, unknown> = {};
    const trimmedArgs = argumentsText.trim();
    if (trimmedArgs) {
      try {
        const parsed = JSON.parse(trimmedArgs) as unknown;
        if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
          setArgumentsError("Arguments must be a JSON object.");
          return;
        }
        parsedArguments = parsed as Record<string, unknown>;
      } catch {
        setArgumentsError("Arguments must be valid JSON.");
        return;
      }
    }

    setArgumentsError(null);
    setCallError(null);
    setIsCalling(true);

    try {
      const response = await callButlerMcpTool(butlerName, {
        tool_name: toolName,
        arguments: parsedArguments,
      });
      setLastResponse(response.data);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "MCP tool call failed";
      setCallError(message);
      setLastResponse(null);
    } finally {
      setIsCalling(false);
    }
  }, [argumentsText, butlerName, isCalling, selectedTool]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>MCP Debugging</CardTitle>
          <CardDescription>
            Call MCP tools on this butler with optional JSON arguments.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              {tools.length} {tools.length === 1 ? "tool" : "tools"} available
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void loadTools()}
              disabled={isLoadingTools}
            >
              {isLoadingTools ? "Refreshing..." : "Refresh Tools"}
            </Button>
          </div>

          {toolLoadError && (
            <p className="text-sm text-destructive">Failed to load tools: {toolLoadError}</p>
          )}

          <div className="space-y-2">
            <label htmlFor="mcp-tool-select" className="text-sm font-medium">
              Tool
            </label>
            <Select value={selectedTool} onValueChange={setSelectedTool}>
              <SelectTrigger id="mcp-tool-select" className="w-full">
                <SelectValue placeholder={isLoadingTools ? "Loading tools..." : "Select a tool"} />
              </SelectTrigger>
              <SelectContent>
                {tools.map((tool) => (
                  <SelectItem key={tool.name} value={tool.name}>
                    {tool.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedToolDescription && (
              <p className="text-xs text-muted-foreground">{selectedToolDescription}</p>
            )}
          </div>

          <div className="space-y-2">
            <label htmlFor="mcp-tool-arguments" className="text-sm font-medium">
              Arguments (optional JSON object)
            </label>
            <Textarea
              id="mcp-tool-arguments"
              value={argumentsText}
              onChange={(e) => {
                setArgumentsText(e.target.value);
                setArgumentsError(null);
              }}
              placeholder='{"key":"value"}'
              className="min-h-28 font-mono text-sm"
              disabled={isCalling}
            />
            {argumentsError && (
              <p className="text-sm text-destructive">{argumentsError}</p>
            )}
          </div>

          <Button onClick={() => void handleCall()} disabled={!canCall}>
            {isCalling ? "Calling..." : "Call Tool"}
          </Button>
        </CardContent>
      </Card>

      {(callError || lastResponse) && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-3">
              Last Response
              {lastResponse && (
                <Badge
                  variant={lastResponse.is_error ? "destructive" : "secondary"}
                >
                  {lastResponse.is_error ? "Tool Error" : "OK"}
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {callError && <p className="text-sm text-destructive">{callError}</p>}
            {lastResponse && (
              <>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Tool</p>
                  <p className="font-mono text-sm">{lastResponse.tool_name}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Arguments</p>
                  <JsonViewer data={lastResponse.arguments} defaultCollapsed />
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Parsed Result</p>
                  <JsonViewer data={lastResponse.result} defaultCollapsed />
                </div>
                {lastResponse.raw_text && (
                  <div className="space-y-1">
                    <p className="text-xs text-muted-foreground">Raw Text</p>
                    <pre className="overflow-auto rounded-md bg-muted p-3 text-xs font-mono whitespace-pre-wrap">
                      {lastResponse.raw_text}
                    </pre>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
