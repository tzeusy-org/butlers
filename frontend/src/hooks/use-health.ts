/**
 * TanStack Query hooks for the health butler API.
 */

import { useQuery } from "@tanstack/react-query";

import {
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
} from "@/api/index.ts";
import type {
  MealParams,
  MeasurementParams,
  MedicationParams,
  ResearchParams,
  SymptomParams,
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

/** Fetch a paginated list of health conditions. */
export function useConditions(params?: { offset?: number; limit?: number }) {
  return useQuery({
    queryKey: ["health-conditions", params],
    queryFn: () => getConditions(params),
    refetchInterval: 30_000,
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
