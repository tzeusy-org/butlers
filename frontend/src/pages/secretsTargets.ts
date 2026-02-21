export const SHARED_SECRETS_TARGET = "shared";

export function buildSecretsTargets(butlerNames: string[]): string[] {
  const sharedTarget = SHARED_SECRETS_TARGET.toLowerCase();
  const nonSharedButlers = butlerNames.filter(
    (name) => name.trim().toLowerCase() !== sharedTarget,
  );
  return [SHARED_SECRETS_TARGET, ...nonSharedButlers];
}
