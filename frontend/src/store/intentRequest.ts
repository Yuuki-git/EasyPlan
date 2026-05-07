import type { IntentCreateRequest } from '../types/api';

export type PlannerProvider = NonNullable<IntentCreateRequest['planner_provider']>;

const DEFAULT_PLANNER_PROVIDER: PlannerProvider = 'openai';
const PLANNER_PROVIDERS = new Set<PlannerProvider>(['openai', 'deepseek', 'xiaomi']);

export function resolvePlannerProvider(env: Record<string, string | undefined>): PlannerProvider {
  const configuredProvider = env.VITE_PLANNER_PROVIDER?.trim().toLowerCase();
  if (configuredProvider && PLANNER_PROVIDERS.has(configuredProvider as PlannerProvider)) {
    return configuredProvider as PlannerProvider;
  }
  return DEFAULT_PLANNER_PROVIDER;
}

export function buildIntentRequest({
  intentText,
  preferredProvider,
  plannerProvider,
}: {
  intentText: string;
  preferredProvider: string;
  plannerProvider: PlannerProvider;
}): IntentCreateRequest {
  return {
    intent_text: intentText,
    preferred_provider: preferredProvider,
    planner_provider: plannerProvider,
  };
}
