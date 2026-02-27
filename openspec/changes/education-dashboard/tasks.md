## 1. Backend API Endpoints

- [x] 1.1 Add `GET /mind-maps/{id}/pending-reviews` endpoint in `roster/education/api/router.py` — import `spaced_repetition_pending_reviews`, verify mind map exists (404), return node array via `_node_dict_to_response`
- [x] 1.2 Add `GET /mind-maps/{id}/mastery-summary` endpoint — import `mastery_get_map_summary`, verify mind map exists (404), return `MasterySummaryResponse`
- [x] 1.3 Add `PUT /mind-maps/{id}/status` endpoint — accept `{"status": "..."}` body, validate against `active/completed/abandoned` (422), import `mind_map_update_status`, return updated mind map (404 if not found)
- [x] 1.4 Add `POST /curriculum-requests` endpoint — accept `{"topic", "goal?"}` body, validate topic (non-empty, max 200 chars, 422), check KV store for existing `pending_curriculum_request` key (409 if present), write KV entry with `requested_at` timestamp, return 202
- [x] 1.5 Add Pydantic models in `roster/education/api/models.py` — `StatusUpdateRequest`, `CurriculumRequestBody`, `CurriculumRequestResponse`, `PendingReviewNodeResponse` (if distinct from `MindMapNodeResponse`)
- [x] 1.6 Write tests for all 4 new endpoints — cover happy path, 404, 422, 409 scenarios per spec (12+ test cases in `roster/education/tests/`)

## 2. Frontend API Types and Client

- [x] 2.1 Add TypeScript interfaces to `frontend/src/api/types.ts` — `MindMap`, `MindMapNode`, `MindMapEdge`, `QuizResponse`, `AnalyticsSnapshot`, `TeachingFlow`, `CrossTopicAnalytics`, `MasterySummary`, `CurriculumRequest`, `PendingReviewNode`
- [x] 2.2 Add education client functions to `frontend/src/api/client.ts` — `getMindMaps`, `getMindMap`, `getMindMapFrontier`, `getMindMapAnalytics`, `getMindMapPendingReviews`, `getMindMapMasterySummary`, `getQuizResponses`, `getTeachingFlows`, `getCrossTopicAnalytics`, `updateMindMapStatus`, `requestCurriculum`
- [x] 2.3 Create `frontend/src/hooks/use-education.ts` — query hooks (`useMindMaps`, `useMindMap`, `useFrontierNodes`, `useMindMapAnalytics`, `usePendingReviews`, `useMasterySummary`, `useQuizResponses`, `useTeachingFlows`, `useCrossTopicAnalytics`) with 30s/15s refetch intervals + mutation hooks (`useUpdateMindMapStatus`, `useRequestCurriculum`) with cache invalidation

## 3. Page Routing and Navigation

- [x] 3.1 Add sidebar entry in `frontend/src/components/layout/Sidebar.tsx` — "Education" item with `butler: 'education'` guard
- [x] 3.2 Add route in `frontend/src/router.tsx` — `{ path: '/education', element: <EducationPage /> }`
- [x] 3.3 Create `frontend/src/pages/EducationPage.tsx` — page header, mind map selector dropdown, three-tab layout (Curriculum/Reviews/Analytics), shared selected-mind-map state, empty state when no mind maps exist

## 4. Curriculum Tab Components

- [x] 4.1 Create `frontend/src/components/education/MindMapGraph.tsx` — XYFlow + dagre top-to-bottom layout, custom node component with mastery-status color coding (emerald/blue/amber/slate/gray), prerequisite edges as solid arrows, related edges as dashed lines, frontier node pulsing ring indicator
- [x] 4.2 Create `frontend/src/components/education/NodeDetailPanel.tsx` — slide-out panel on node click showing label, description, mastery score, mastery status badge, next review date, effort estimate, and embedded quiz history section
- [x] 4.3 Create `frontend/src/components/education/CurriculumActions.tsx` — status badge, Abandon button (with confirmation dialog, visible when active), Re-activate button (visible when abandoned), cache invalidation on success
- [x] 4.4 Create `frontend/src/components/education/RequestCurriculumDialog.tsx` — dialog with topic (required, max 200) and goal (optional, max 500) fields, submit calls `POST /curriculum-requests`, success toast, 409 conflict error display

## 5. Reviews Tab Components

- [x] 5.1 Create `frontend/src/components/education/ReviewTimeline.tsx` — fetch pending reviews for all active mind maps, group into Overdue/Today/This Week/Later sections, each entry shows node label + mind map title + mastery badge + review date, red left border for Overdue, amber for Today, empty state message when no reviews

## 6. Analytics Tab Components

- [x] 6.1 Create `frontend/src/components/education/MasterySummaryCards.tsx` — row of 4 summary cards (total nodes, mastered count, avg mastery score, estimated completion days) from mastery-summary and analytics endpoints
- [x] 6.2 Create `frontend/src/components/education/MasteryTrendChart.tsx` — Recharts AreaChart of `mastery_pct` over 30 days, blue fill (#3b82f6) at 20% opacity, date x-axis, 0–100% y-axis, empty state when no snapshots
- [x] 6.3 Create `frontend/src/components/education/CrossTopicChart.tsx` — portfolio toggle, Recharts BarChart of `mastery_pct` per mind map from `/analytics/cross-topic`, headline `portfolio_mastery` score
- [x] 6.4 Create `frontend/src/components/education/StrugglingNodesCard.tsx` — warning card listing struggling nodes by label (from analytics snapshot `struggling_nodes`), each clickable to open quiz history for that node

## 7. Quiz History Components

- [x] 7.1 Create `frontend/src/components/education/QuizHistoryList.tsx` — reusable component accepting `mind_map_id` and optional `node_id` filter, paginated list (20 per page, "Load more" button), each entry shows question text, user answer (or "No answer"), quality badge (0–2 red, 3 amber, 4–5 green), response type label, timestamp, newest-first ordering
