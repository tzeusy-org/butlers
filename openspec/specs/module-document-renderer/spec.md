# Document Renderer Module

## Purpose
Defines a shared, opt-in module providing pure-computation document rendering — Markdown/HTML to PDF, static chart/graph image generation, and templated document assembly — producing blobs in the S3-compatible blob store for delivery via notify attachments. The module has no external side effects.

## ADDED Requirements

### Requirement: [TARGET-STATE] Render to Blob
The module SHALL expose tools that render structured input (Markdown, HTML, or a chart spec) to a binary artifact (PDF, PNG, or SVG) and persist it to the blob store, returning a `storage_ref`.

#### Scenario: Markdown rendered to PDF blob
- **WHEN** a runtime instance calls the render tool with Markdown content and `format="pdf"`
- **THEN** a PDF is produced and stored in the blob store
- **AND** the tool returns a `storage_ref` usable as a `notify` attachment

#### Scenario: Chart rendered to image blob
- **WHEN** the render tool is called with a chart specification and `format` in `("png","svg")`
- **THEN** a static chart image is produced and stored, and a `storage_ref` is returned

### Requirement: [TARGET-STATE] No External Side Effects
Document rendering SHALL be pure computation with no network egress and no approval gating (internal infrastructure).

#### Scenario: Render requires no approval
- **WHEN** any render tool is invoked
- **THEN** it executes without creating a pending approval action
