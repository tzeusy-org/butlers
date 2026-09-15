#!/usr/bin/env bash
# Create or update the `main-merge-queue` repository ruleset (bu-r5mnn).
#
# RUN THIS ONLY AFTER THE bu-r5mnn BRANCH IS MERGED TO MAIN. The ruleset
# requires the `check`, `guards`, and `frontend` statuses on merge_group runs,
# and those statuses only exist once .github/workflows/ci.yml on main carries
# the `on.merge_group` trigger and the `guards` job. Creating the ruleset first
# would queue every PR behind statuses that can never report.
#
# What it configures on refs/heads/main:
#   - merge_queue: SQUASH merges, ALLGREEN grouping, up to 5 entries built and
#     merged per batch, merge as soon as 1 entry is ready or 5 minutes pass,
#     60 minute check timeout.
#   - required_status_checks: check, guards, frontend (non-strict, so a PR does
#     not have to be rebased onto main first; the queue validates the merged
#     tree instead).
#   - deletion + non_fast_forward protection.
#   - bypass: repository admins (RepositoryRole actor_id 5), always. The owner
#     and the coordinator's direct fast-forward pushes keep working.
#
# Idempotent: looks up an existing ruleset by name and PUTs to it, otherwise
# POSTs a new one. Prints the resulting ruleset id.
#
# Usage:
#   scripts/setup_main_ruleset.sh                 # target the origin remote's repo
#   REPO=owner/name scripts/setup_main_ruleset.sh # explicit repo
#   DRY_RUN=1 scripts/setup_main_ruleset.sh       # print the payload, change nothing

set -euo pipefail

RULESET_NAME="main-merge-queue"
REPO="${REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
DRY_RUN="${DRY_RUN:-0}"

payload=$(cat <<JSON
{
  "name": "${RULESET_NAME}",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/main"],
      "exclude": []
    }
  },
  "bypass_actors": [
    {
      "actor_id": 5,
      "actor_type": "RepositoryRole",
      "bypass_mode": "always"
    }
  ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "merge_queue",
      "parameters": {
        "merge_method": "SQUASH",
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 5,
        "max_entries_to_merge": 5,
        "min_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 5,
        "check_response_timeout_minutes": 60
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          { "context": "check" },
          { "context": "guards" },
          { "context": "frontend" }
        ]
      }
    }
  ]
}
JSON
)

if [ "${DRY_RUN}" = "1" ]; then
  echo "DRY_RUN=1: would apply to ${REPO}:"
  printf '%s\n' "${payload}"
  exit 0
fi

existing_id=$(gh api "repos/${REPO}/rulesets" --paginate \
  --jq ".[] | select(.name == \"${RULESET_NAME}\") | .id" | head -n 1)

if [ -n "${existing_id}" ]; then
  echo "Updating existing ruleset ${RULESET_NAME} (id ${existing_id}) on ${REPO}"
  result_id=$(printf '%s' "${payload}" \
    | gh api --method PUT "repos/${REPO}/rulesets/${existing_id}" --input - --jq .id)
else
  echo "Creating ruleset ${RULESET_NAME} on ${REPO}"
  result_id=$(printf '%s' "${payload}" \
    | gh api --method POST "repos/${REPO}/rulesets" --input - --jq .id)
fi

echo "ruleset id: ${result_id}"
gh api "repos/${REPO}/rules/branches/main" --jq '[.[] | .type] | sort | unique | join(", ")' \
  | sed 's/^/active rule types on main: /'
