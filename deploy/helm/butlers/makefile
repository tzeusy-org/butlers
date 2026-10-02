# Butlers Helm chart. See README.md. Recipes use tabs.
RELEASE_NAME := butlers
TAG ?= $(shell git rev-parse --short=12 HEAD)
LOCAL_VALUES := $(wildcard values.local.yaml)

NAMESPACE_dev := butlers-dev
NAMESPACE_prod := butlers

helm_args = -n $(NAMESPACE_$(1)) -f values.yaml -f values.$(1).yaml $(if $(LOCAL_VALUES),-f values.local.yaml) \
	--set image.tag=$(TAG) --set frontendImage.tag=$(TAG)-$(1) \
	$(shell ../../../scripts/k8s/site-helm-args.sh)

.PHONY: lint

lint:
	helm lint . -f values.yaml -f values.dev.yaml --set image.tag=lint --set frontendImage.tag=lint
	helm lint . -f values.yaml -f values.prod.yaml --set image.tag=lint --set frontendImage.tag=lint

template-%:
	rm -rf _templates/$*
	helm template $(RELEASE_NAME) . $(call helm_args,$*) --output-dir _templates/$*

# Run under `bws run` (see README).
secrets-%:
	../../../scripts/k8s/bootstrap-secrets.sh $*

image-%:
	../../../scripts/k8s/build-push.sh $*

deploy-%:
	helm upgrade --install $(RELEASE_NAME) . --create-namespace $(call helm_args,$*)
	kubectl -n $(NAMESPACE_$*) rollout status deploy/dashboard-api --timeout=10m
	kubectl -n $(NAMESPACE_$*) rollout status deploy/butlers-up --timeout=10m

# Build + push images for the current commit, then deploy them. Migrations run
# in dashboard-api's initContainer on every rollout. Run under `bws run`.
ship-%:
	$(MAKE) deploy-$* TAG=$$(../../../scripts/k8s/build-push.sh $*)

status-%:
	kubectl -n $(NAMESPACE_$*) get pods,svc,pvc,externalsecret
