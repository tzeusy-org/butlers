# Butlers Helm chart. See README.md. Recipes use tabs.
RELEASE_NAME := butlers
TAG ?= $(shell git rev-parse --short=12 HEAD)
LOCAL_VALUES := $(wildcard values.local.yaml)

NAMESPACE_dev := butlers-dev
NAMESPACE_prod := butlers

# The public MCP owner step after every dev release (RFC 0039 D3, OD20). Set to off only in a
# reviewed change (a dev teardown, task 8.1); the step then prints a notice and exits 0.
PUBLIC_MCP_OWNER_STEP_dev := on

helm_args = -n $(NAMESPACE_$(1)) -f values.yaml -f values.$(1).yaml $(if $(LOCAL_VALUES),-f values.local.yaml) \
	--set image.tag=$(TAG) --set frontendImage.tag=$(TAG)-$(1) \
	$(shell ../../../scripts/k8s/site-helm-args.sh)

.PHONY: lint

# Site hosts are never committed, so lint supplies documentation-range placeholders.
LINT_SITE := --set 'trackerEgress.deniedCidrs={192.0.2.1/32}' --set 'egressPolicy.allowedCidrs={192.0.2.2/32}'

lint:
	helm lint . -f values.yaml -f values.dev.yaml --set image.tag=lint --set frontendImage.tag=lint $(LINT_SITE)
	helm lint . -f values.yaml -f values.prod.yaml --set image.tag=lint --set frontendImage.tag=lint

template-%:
	rm -rf _templates/$*
	helm template $(RELEASE_NAME) . $(call helm_args,$*) --output-dir _templates/$*

# Run under `bws run` (see README).
secrets-%:
	../../../scripts/k8s/bootstrap-secrets.sh $*

image-%:
	../../../scripts/k8s/build-push.sh $*

# Online migrations check the privileged bootstrap but never create it, so
# rerun the deployed tag's scripts/init-db.sql before the rollout (dev only).
bootstrap-%:
	../../../scripts/k8s/bootstrap-db.sh $* $(TAG)

# Wrapped so each release leaves one public.deployments row (success or failed).
# The public MCP owner step runs after the release whatever it returned, because the
# migrations (and reconcile) may have run before a later rollout or readiness failure; its
# guard refuses when they did not. The recipe exits with the first non-zero status.
deploy-%:
	@../../../scripts/k8s/runtime-topology-preflight.sh "$(NAMESPACE_$*)" "$(TAG)" "$(RELEASE_NAME)" -- $(call helm_args,$*)
	$(MAKE) --no-print-directory bootstrap-$* TAG="$(TAG)"
	../../../scripts/k8s/record-deploy.sh "$(NAMESPACE_$*)" "$(TAG)" "$(RELEASE_NAME)" -- $(call helm_args,$*); \
	rd=$$?; \
	../../../scripts/k8s/public-mcp-owner-step.sh "$*" "$(TAG)" "$(PUBLIC_MCP_OWNER_STEP_$*)" "$(RELEASE_NAME)"; \
	os=$$?; \
	if [ $$rd -ne 0 ]; then exit $$rd; fi; exit $$os

# Build + push images for the current commit, then deploy them. Migrations run
# in dashboard-api's initContainer on every rollout. Run under `bws run`.
ship-%:
	$(MAKE) deploy-$* TAG=$$(../../../scripts/k8s/build-push.sh $*)

status-%:
	kubectl -n $(NAMESPACE_$*) get pods,svc,pvc,externalsecret
