import { Children, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

import { prepareStreamingMarkdown, safeMarkdownUrl } from "./markdown";

/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- this component's two named overflow regions must be keyboard-focusable for arrow-key scrolling; button semantics would misstate their read-only content. */

const ALLOWED_ELEMENTS = [
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "ul",
  "ol",
  "li",
  "em",
  "strong",
  "blockquote",
  "code",
  "pre",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "a",
  "hr",
  "br",
] as const;

function textContent(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) => (typeof child === "string" || typeof child === "number" ? child : ""))
    .join("")
    .trim();
}

function TableCell({ children, header = false }: { children?: ReactNode; header?: boolean }) {
  const Tag = header ? "th" : "td";
  const numeric = /^[-+]?[$€£]?\d[\d,]*(?:\.\d+)?%?$/.test(textContent(children));
  return (
    <Tag
      className={cn(
        "border-b border-border/70 px-3 py-2 text-left align-top text-[13px]",
        header && "font-sans font-medium text-foreground",
        numeric && "font-mono tabular-nums text-right",
      )}
    >
      {children}
    </Tag>
  );
}

export function AnswerBody({ content }: { content: string }) {
  return (
    <div
      data-answer-prose
      className="min-w-0 max-w-full overflow-hidden break-words font-serif text-[16px] leading-[1.6] text-foreground"
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        allowedElements={[...ALLOWED_ELEMENTS]}
        unwrapDisallowed
        urlTransform={safeMarkdownUrl}
        components={{
          p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
          h1: ({ children }) => (
            <h1 className="mb-2 mt-5 font-sans text-xl font-medium leading-tight first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-5 font-sans text-lg font-medium leading-tight first:mt-0">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1.5 mt-4 font-sans text-base font-medium first:mt-0">
              {children}
            </h3>
          ),
          h4: ({ children }) => (
            <h4 className="mb-1 mt-3 font-sans text-sm font-medium first:mt-0">{children}</h4>
          ),
          ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
          blockquote: ({ children }) => (
            <blockquote className="my-3 border-l-2 border-border-strong pl-3 text-muted-foreground">
              {children}
            </blockquote>
          ),
          code: ({ className, children }) => (
            <code
              className={cn(
                "font-mono text-[11px] leading-[1.4] tabular-nums",
                !className && "rounded bg-background/70 px-1 py-0.5",
                className,
              )}
            >
              {children}
            </code>
          ),
          pre: ({ children }) => (
            <pre
              role="region"
              aria-label="Code block"
              tabIndex={0}
              className="my-3 max-w-full overflow-x-auto rounded-md border bg-background/70 p-3 font-mono text-[11px] leading-[1.4] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div
              data-answer-table-region
              role="region"
              aria-label="Answer table"
              tabIndex={0}
              className="my-3 max-w-full overflow-x-auto rounded-md border bg-background/35 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              <table className="w-max min-w-full border-collapse font-sans">{children}</table>
            </div>
          ),
          th: ({ children }) => <TableCell header>{children}</TableCell>,
          td: ({ children }) => <TableCell>{children}</TableCell>,
          a: ({ href, children }) => {
            const safe = href ? safeMarkdownUrl(href) : "";
            return safe ? (
              <a
                href={safe}
                target="_blank"
                rel="noopener noreferrer"
                className="underline decoration-border-strong underline-offset-4 hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
              >
                {children}
              </a>
            ) : (
              <span>{children}</span>
            );
          },
        }}
      >
        {prepareStreamingMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
}
