/**
 * TanStack Query hooks for the health butler API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createCondition,
  createMeal,
  createMeasurement,
  createMedication,
  createResearch,
  createSymptom,
  deleteCondition,
  deleteMeal,
  deleteMeasurement,
  deleteMedication,
  deleteResearch,
  deleteSymptom,
  getConditions,
  getMeals,
  getNutritionSummary,
  getMeasurements,
  getMeasurementsLatest,
  getMeasurementSources,
  getMeasurementsTrend,
  getMedicationAdherence,
  getMedicationDoses,
  getMedications,
  logMedicationDose,
  getResearch,
  getSleepLatest,
  getSymptoms,
  updateCondition,
  updateMeal,
  updateMeasurement,
  updateMedication,
  updateResearch,
  updateSymptom,
} from "@/api/index.ts";
import type {
  ConditionCreateRequest,
  ConditionUpdateRequest,
  DoseLogRequest,
  MealCreateRequest,
  MealParams,
  MealUpdateRequest,
  NutritionSummaryParams,
  MeasurementCreateRequest,
  MeasurementParams,
  MeasurementTrendParams,
  MeasurementUpdateRequest,
  MedicationCreateRequest,
  MedicationParams,
  MedicationUpdateRequest,
  ResearchCreateRequest,
  ResearchParams,
  ResearchUpdateRequest,
  SymptomCreateRequest,
  SymptomParams,
  SymptomUpdateRequest,
} from "@/api/index.ts";

/** Fetch a paginated list of health measurements. */
export function useMeasurements(params?: MeasurementParams) {
  return useQuery({
    queryKey: ["health-measurements", params],
    queryFn: () => getMeasurements(params),
    refetchInterval: 30_000,
  });
}

/**
 * Fetch the bucketed mean/min/max trend for a single measurement type.
 *
 * Deterministic read — auto-refreshes every 30s per the health auto-refresh
 * contract. Disabled until a `type` is supplied.
 */
export function useMeasurementTrend(params: MeasurementTrendParams) {
  return useQuery({
    queryKey: ["health-measurement-trend", params],
    queryFn: () => getMeasurementsTrend(params),
    refetchInterval: 30_000,
    enabled: !!params.type,
  });
}

/**
 * Invalidate every measurement-list query so freshly mutated readings appear.
 *
 * The measurement-list cache is keyed by the params object (type/since/until/...),
 * so we invalidate on the `["health-measurements"]` prefix to cover all variants.
 */
function useInvalidateMeasurements() {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: ["health-measurements"] });
}

/**
 * Log a measurement. On success, invalidates the measurement list so the new
 * reading appears without a manual refetch.
 */
export function useCreateMeasurement() {
  const invalidate = useInvalidateMeasurements();
  return useMutation({
    mutationFn: (body: MeasurementCreateRequest) => createMeasurement(body),
    onSuccess: invalidate,
  });
}

/** Update a measurement by id (only supplied fields are applied). */
export function useUpdateMeasurement() {
  const invalidate = useInvalidateMeasurements();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: MeasurementUpdateRequest }) =>
      updateMeasurement(id, body),
    onSuccess: invalidate,
  });
}

/** Soft-delete a measurement by id. */
export function useDeleteMeasurement() {
  const invalidate = useInvalidateMeasurements();
  return useMutation({
    mutationFn: (id: string) => deleteMeasurement(id),
    onSuccess: invalidate,
  });
}

/** Fetch a paginated list of medications. */
export function useMedications(params?: MedicationParams) {
  return useQuery({
    queryKey: ["health-medications", params],
    queryFn: () => getMedications(params),
    refetchInterval: 30_000,
  });
}

/** Fetch dose log entries for a specific medication. */
export function useMedicationDoses(
  medicationId: string,
  params?: { since?: string; until?: string },
) {
  return useQuery({
    queryKey: ["health-medication-doses", medicationId, params],
    queryFn: () => getMedicationDoses(medicationId, params),
    enabled: !!medicationId,
  });
}

/**
 * Fetch the server-computed adherence summary for a medication.
 *
 * Reads GET /health/medications/{id}/adherence — the frequency-expected
 * adherence rate, NOT a naive client-side taken/total ratio. Conditionally
 * enabled so it never fires for an empty/placeholder id.
 */
export function useMedicationAdherence(
  medicationId: string,
  params?: { start?: string; end?: string },
) {
  return useQuery({
    queryKey: ["health-medication-adherence", medicationId, params],
    queryFn: () => getMedicationAdherence(medicationId, params),
    enabled: !!medicationId,
  });
}

/**
 * Log (or skip) a dose for a medication. On success, invalidates that
 * medication's dose-log and adherence queries so the row reflects the new dose
 * immediately.
 */
export function useLogMedicationDose() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body?: DoseLogRequest }) =>
      logMedicationDose(id, body),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["health-medication-doses", variables.id],
      });
      queryClient.invalidateQueries({
        queryKey: ["health-medication-adherence", variables.id],
      });
    },
  });
}

/**
 * Invalidate every medication-list query so freshly mutated medications appear.
 *
 * The medication-list cache is keyed by the params object (active/limit/...),
 * so we invalidate on the `["health-medications"]` prefix to cover all variants.
 */
function useInvalidateMedications() {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: ["health-medications"] });
}

/**
 * Create a medication. On success, invalidates the medication list so the new
 * record appears without a manual refetch.
 */
export function useCreateMedication() {
  const invalidate = useInvalidateMedications();
  return useMutation({
    mutationFn: (body: MedicationCreateRequest) => createMedication(body),
    onSuccess: invalidate,
  });
}

/** Update a medication by id (only supplied fields are merged). */
export function useUpdateMedication() {
  const invalidate = useInvalidateMedications();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: MedicationUpdateRequest }) =>
      updateMedication(id, body),
    onSuccess: invalidate,
  });
}

/** Soft-delete a medication by id. */
export function useDeleteMedication() {
  const invalidate = useInvalidateMedications();
  return useMutation({
    mutationFn: (id: string) => deleteMedication(id),
    onSuccess: invalidate,
  });
}

/** Fetch a paginated list of health conditions. */
export function useConditions(params?: { offset?: number; limit?: number }) {
  return useQuery({
    queryKey: ["health-conditions", params],
    queryFn: () => getConditions(params),
    refetchInterval: 30_000,
  });
}

/**
 * Invalidate every condition-list query so freshly mutated conditions appear.
 *
 * The condition-list cache is keyed by the params object (offset/limit/...), so
 * we invalidate on the `["health-conditions"]` prefix to cover all variants.
 */
function useInvalidateConditions() {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: ["health-conditions"] });
}

/**
 * Create a condition. On success, invalidates the condition list so the new
 * record appears without a manual refetch.
 */
export function useCreateCondition() {
  const invalidate = useInvalidateConditions();
  return useMutation({
    mutationFn: (body: ConditionCreateRequest) => createCondition(body),
    onSuccess: invalidate,
  });
}

/** Update a condition by id (only supplied fields are merged). */
export function useUpdateCondition() {
  const invalidate = useInvalidateConditions();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ConditionUpdateRequest }) =>
      updateCondition(id, body),
    onSuccess: invalidate,
  });
}

/** Soft-delete a condition by id. */
export function useDeleteCondition() {
  const invalidate = useInvalidateConditions();
  return useMutation({
    mutationFn: (id: string) => deleteCondition(id),
    onSuccess: invalidate,
  });
}

/** Fetch a paginated list of symptoms. */
export function useSymptoms(params?: SymptomParams) {
  return useQuery({
    queryKey: ["health-symptoms", params],
    queryFn: () => getSymptoms(params),
    refetchInterval: 30_000,
  });
}

/**
 * Invalidate every symptom-list query so freshly mutated symptoms appear.
 *
 * The symptom-list cache is keyed by the params object (name/since/until/...),
 * so we invalidate on the `["health-symptoms"]` prefix to cover all variants.
 */
function useInvalidateSymptoms() {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: ["health-symptoms"] });
}

/**
 * Log a symptom. On success, invalidates the symptom list so the new record
 * appears without a manual refetch.
 */
export function useCreateSymptom() {
  const invalidate = useInvalidateSymptoms();
  return useMutation({
    mutationFn: (body: SymptomCreateRequest) => createSymptom(body),
    onSuccess: invalidate,
  });
}

/** Update a symptom by id (only supplied fields are applied). */
export function useUpdateSymptom() {
  const invalidate = useInvalidateSymptoms();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: SymptomUpdateRequest }) =>
      updateSymptom(id, body),
    onSuccess: invalidate,
  });
}

/** Soft-delete a symptom by id. */
export function useDeleteSymptom() {
  const invalidate = useInvalidateSymptoms();
  return useMutation({
    mutationFn: (id: string) => deleteSymptom(id),
    onSuccess: invalidate,
  });
}

/** Fetch a paginated list of meals. */
export function useMeals(params?: MealParams) {
  return useQuery({
    queryKey: ["health-meals", params],
    queryFn: () => getMeals(params),
    refetchInterval: 30_000,
  });
}

/**
 * Invalidate every meal-list query so freshly mutated meals appear.
 *
 * The meal-list cache is keyed by the params object (type/since/until/...), so
 * we invalidate on the `["health-meals"]` prefix to cover all variants.
 */
function useInvalidateMeals() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["health-meals"] });
}

/**
 * Log a meal. On success, invalidates the meal list so the new record appears
 * without a manual refetch.
 */
export function useCreateMeal() {
  const invalidate = useInvalidateMeals();
  return useMutation({
    mutationFn: (body: MealCreateRequest) => createMeal(body),
    onSuccess: invalidate,
  });
}

/** Update a meal by id (only supplied fields are applied). */
export function useUpdateMeal() {
  const invalidate = useInvalidateMeals();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: MealUpdateRequest }) =>
      updateMeal(id, body),
    onSuccess: invalidate,
  });
}

/** Soft-delete a meal by id. */
export function useDeleteMeal() {
  const invalidate = useInvalidateMeals();
  return useMutation({
    mutationFn: (id: string) => deleteMeal(id),
    onSuccess: invalidate,
  });
}

/**
 * Fetch aggregate nutrition totals over a date range.
 *
 * Wraps GET /api/health/nutrition/summary?start=&end=.
 * Both params are required; this hook is disabled when either is absent.
 * Auto-refreshes every 30 seconds (deterministic endpoint, no LLM cost).
 */
export function useNutritionSummary(params: Partial<NutritionSummaryParams>) {
  return useQuery({
    queryKey: ["health-nutrition-summary", params],
    queryFn: () =>
      getNutritionSummary({ start: params.start!, end: params.end! }),
    enabled: Boolean(params.start && params.end),
    refetchInterval: 30_000,
  });
}

/** Fetch a paginated list of health research notes. */
export function useResearch(params?: ResearchParams) {
  return useQuery({
    queryKey: ["health-research", params],
    queryFn: () => getResearch(params),
    refetchInterval: 30_000,
  });
}

/**
 * Invalidate every research-list query so freshly mutated notes appear.
 *
 * The research-list cache is keyed by the params object (q/tag/offset/...), so
 * we invalidate on the `["health-research"]` prefix to cover all variants.
 */
function useInvalidateResearch() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["health-research"] });
}

/**
 * Create a research note. On success, invalidates the research list so the new
 * note appears without a manual refetch.
 */
export function useCreateResearch() {
  const invalidate = useInvalidateResearch();
  return useMutation({
    mutationFn: (body: ResearchCreateRequest) => createResearch(body),
    onSuccess: invalidate,
  });
}

/** Update a research note by id (only supplied fields are merged). */
export function useUpdateResearch() {
  const invalidate = useInvalidateResearch();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ResearchUpdateRequest }) =>
      updateResearch(id, body),
    onSuccess: invalidate,
  });
}

/** Soft-delete a research note by id. */
export function useDeleteResearch() {
  const invalidate = useInvalidateResearch();
  return useMutation({
    mutationFn: (id: string) => deleteResearch(id),
    onSuccess: invalidate,
  });
}

/**
 * Fetch the latest measurement value for each requested type.
 *
 * Wraps GET /api/health/measurements/latest?types=X,Y,Z
 */
export function useMeasurementsLatest(types: string[]) {
  return useQuery({
    queryKey: ["health-measurements-latest", types],
    queryFn: () => getMeasurementsLatest(types),
    refetchInterval: 60_000,
    enabled: types.length > 0,
  });
}

/**
 * Fetch the latest sleep session with stage breakdown.
 *
 * Wraps GET /api/health/measurements/sleep/latest
 */
export function useSleepLatest() {
  return useQuery({
    queryKey: ["health-sleep-latest"],
    queryFn: () => getSleepLatest(),
    refetchInterval: 60_000,
  });
}

/**
 * Fetch all active measurement sources with their last-sample timestamps.
 *
 * Wraps GET /api/health/measurements/sources
 */
export function useMeasurementSources() {
  return useQuery({
    queryKey: ["health-measurement-sources"],
    queryFn: () => getMeasurementSources(),
    refetchInterval: 60_000,
  });
}
