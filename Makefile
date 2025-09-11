SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c
.DELETE_ON_ERROR:

# --- Paths
PROTO_DEPS := ./proto-deps
COSMOS_SDK_DIR := $(PROTO_DEPS)/cosmos-sdk
COSMOS_PROTO_DIR := $(PROTO_DEPS)/cosmos-proto
GOGOPROTO_DIR := $(PROTO_DEPS)/gogoproto
GOOGLEAPIS_DIR := $(PROTO_DEPS)/googleapis
ALLORA_CHAIN_DIR := $(PROTO_DEPS)/allora-chain

ALLORA_PROTOS_DIR := ./src/allora_sdk/protos
REST_CLIENT_OUT_DIR := ./src/allora_sdk/rest

# --- Stamps (single-file “products” for multi-file gens)
PROTO_STAMP := $(ALLORA_PROTOS_DIR)/.generated.stamp
REST_STAMP  := $(REST_CLIENT_OUT_DIR)/.generated.stamp

# --- Default
.PHONY: dev
dev: install_as_editable $(PROTO_STAMP) $(REST_STAMP)
	@echo "✅ Ready for development."

.PHONY: install_as_editable
install_as_editable:
	uv pip install -e ".[dev]" ".[codegen]"

.PHONY: wheel
wheel:
	uv build

# --- Git dependencies
$(GOGOPROTO_DIR)/.git:
	rm -rf "$(GOGOPROTO_DIR)"
	git clone --depth 1 --single-branch --branch v1.7.0 \
	  https://github.com/cosmos/gogoproto "$(GOGOPROTO_DIR)"

$(COSMOS_PROTO_DIR)/.git:
	rm -rf "$(COSMOS_PROTO_DIR)"
	git clone --depth 1 --single-branch --branch v1.0.0-beta.5 \
	  https://github.com/cosmos/cosmos-proto "$(COSMOS_PROTO_DIR)"

$(COSMOS_SDK_DIR)/.git:
	rm -rf "$(COSMOS_SDK_DIR)"
	git clone --depth 1 --single-branch --branch v0.50.13 \
	  https://github.com/cosmos/cosmos-sdk "$(COSMOS_SDK_DIR)"

$(GOOGLEAPIS_DIR)/.git:
	rm -rf "$(GOOGLEAPIS_DIR)"
	git clone --depth 1 --single-branch --branch master \
	  https://github.com/googleapis/googleapis "$(GOOGLEAPIS_DIR)"

$(ALLORA_CHAIN_DIR)/.git:
	rm -rf "$(ALLORA_CHAIN_DIR)"
	git clone --depth 1 --single-branch --branch v0.12.2 \
	  https://github.com/allora-network/allora-chain "$(ALLORA_CHAIN_DIR)"

.PHONY: proto-deps
proto-deps: \
  $(GOGOPROTO_DIR)/.git \
  $(COSMOS_PROTO_DIR)/.git \
  $(COSMOS_SDK_DIR)/.git \
  $(GOOGLEAPIS_DIR)/.git \
  $(ALLORA_CHAIN_DIR)/.git

.PHONY: proto-deps-update
proto-deps-update:
	git -C "$(GOGOPROTO_DIR)" fetch --depth 1 origin v1.7.0 && git -C "$(GOGOPROTO_DIR)" reset --hard FETCH_HEAD
	git -C "$(COSMOS_PROTO_DIR)" fetch --depth 1 origin v1.0.0-beta.5 && git -C "$(COSMOS_PROTO_DIR)" reset --hard FETCH_HEAD
	git -C "$(COSMOS_SDK_DIR)" fetch --depth 1 origin v0.50.13 && git -C "$(COSMOS_SDK_DIR)" reset --hard FETCH_HEAD
	git -C "$(GOOGLEAPIS_DIR)" fetch --depth 1 origin master && git -C "$(GOOGLEAPIS_DIR)" reset --hard FETCH_HEAD
	git -C "$(ALLORA_CHAIN_DIR)" fetch --depth 1 origin v0.12.2 && git -C "$(ALLORA_CHAIN_DIR)" reset --hard FETCH_HEAD

# --- Ensure output dirs exist (order-only)
$(ALLORA_PROTOS_DIR):
	mkdir -p "$@"

$(REST_CLIENT_OUT_DIR):
	mkdir -p "$@"

# --- Protobuf generation (stamp depends on repos + generator + Makefile)
$(PROTO_STAMP): \
  $(ALLORA_CHAIN_DIR)/.git \
  $(COSMOS_SDK_DIR)/.git \
  $(COSMOS_PROTO_DIR)/.git \
  $(GOOGLEAPIS_DIR)/.git \
  $(GOGOPROTO_DIR)/.git \
  $(PROTO_INIT_TEMPLATE) \
  Makefile \
  | $(ALLORA_PROTOS_DIR)
	rm -rf "$(ALLORA_PROTOS_DIR)"
	mkdir -p "$(ALLORA_PROTOS_DIR)"

	# Run protoc via uv/venv; evaluate `find` at recipe time with $$()
	python -m grpc_tools.protoc \
		--proto_path="$(ALLORA_CHAIN_DIR)/x/emissions/proto" \
		--proto_path="$(ALLORA_CHAIN_DIR)/x/mint/proto" \
		--proto_path="$(COSMOS_SDK_DIR)/proto" \
		--proto_path="$(COSMOS_PROTO_DIR)/proto" \
		--proto_path="$(GOOGLEAPIS_DIR)" \
		--proto_path="$(GOGOPROTO_DIR)" \
		--python_betterproto2_out="$(ALLORA_PROTOS_DIR)" \
		--python_betterproto2_opt=client_generation=sync_async \
		$$(find "$(ALLORA_CHAIN_DIR)/x/emissions/proto" -type f -name '*.proto') \
		$$(find "$(ALLORA_CHAIN_DIR)/x/mint/proto" -type f -name '*.proto') \
		$$(find "$(COSMOS_SDK_DIR)/proto" -type f -name '*.proto')

	python scripts/generate_custom_message_pool.py \
		--out "$(ALLORA_PROTOS_DIR)"

	# ensure packages are importable
	find "$(ALLORA_PROTOS_DIR)"/ -type d -exec sh -c 'touch "$$1/__init__.py"' _ {} \;

	touch "$@"

# Convenience alias
.PHONY: proto
proto: $(PROTO_STAMP)

# --- REST client generation
$(REST_STAMP): \
  $(PROTO_STAMP) \
  scripts/generate_rest_client_from_protos.py \
  Makefile \
  | $(REST_CLIENT_OUT_DIR)
	rm -rf "$(REST_CLIENT_OUT_DIR)"
	mkdir -p "$(REST_CLIENT_OUT_DIR)"

	python scripts/generate_rest_client_from_protos.py \
		--out "$(REST_CLIENT_OUT_DIR)" \
		--include-tags emissions.v9 mint.v5 cosmos.tx cosmos.base.tendermint.v1beta1 cosmos.auth.v1beta1 cosmos.bank.v1beta1 \
		--proto-files-dirs "$(ALLORA_CHAIN_DIR)/x" "$(COSMOS_SDK_DIR)/proto" \
		--include-dirs \
			"$(ALLORA_CHAIN_DIR)/x/emissions/proto" \
			"$(ALLORA_CHAIN_DIR)/x/mint/proto" \
			"$(COSMOS_SDK_DIR)/proto" \
			"$(COSMOS_PROTO_DIR)/proto" \
			"$(GOGOPROTO_DIR)" \
			"$(GOOGLEAPIS_DIR)"

	touch "$(REST_CLIENT_OUT_DIR)/__init__.py"
	touch "$@"

.PHONY: generate_rest_clients
generate_rest_clients: $(REST_STAMP)

# --- Clean
.PHONY: clean
clean:
	rm -rf "$(ALLORA_PROTOS_DIR)" "$(REST_CLIENT_OUT_DIR)" "$(PROTO_DEPS)"

.PHONY: distclean
distclean: clean
	rm -rf "$(PROTO_DEPS)"

.PHONY: test
test:
	tox run-parallel

