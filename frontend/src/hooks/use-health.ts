/**
 * TanStack Query hooks for the health butler API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createCondition,
  createMedication,
  createSymptom,
  deleteCondition,
  deleteMedication,
  deleteSymptom,
  getConditions,
  getMeals,
  getMeasurements,
  getMeasurementsLatest,
  getMeasurementSources,
  getMedicationDoses,
  getMedications,
  getResearch,
  getSleepLatest,
  getSymptoms,
  updateCondition,
  updateMedication,
  updateSymptom,
} from "@/api/index.ts";
import type {
  ConditionCreateRequest,
  ConditionUpdateRequest,
  MealParams,
  MeasurementParams,
  MedicationCreateRequest,
  MedicationParams,
  MedicationUpdateRequest,
  ResearchParams,
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

/** Fetch a paginated list of health research notes. */
export function useResearch(params?: ResearchParams) {
  return useQuery({
    queryKey: ["health-research", params],
    queryFn: () => getResearch(params),
    refetchInterval: 30_000,
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
