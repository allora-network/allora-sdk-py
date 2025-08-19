

COSMOS_SDK_DIR := ./proto-deps/cosmos-sdk/proto
COSMOS_PROTO_DIR := ./proto-deps/cosmos-proto/proto
GOGOPROTO_DIR := ./proto-deps/gogoproto
GOOGLEAPIS_DIR := ./proto-deps/googleapis
ALLORA_CHAIN_DIR := ./proto-deps/allora-chain
ALLORA_PROTOS_DIR := ./src/allora_sdk/protobuf_client/protos
REST_CLIENT_OUT_DIR := ./src/allora_sdk/rest


## Bootstrap a fresh clone for development
dev: install_as_editable proto generate_rest_clients

## Create/refresh editable install (makes absolute imports work)
install_as_editable:
	uv venv
	uv pip install -e ".[dev]" ".[codegen]"

## Build a wheel (will run codegen during packaging via setup.py hook)
wheel:
	uv build

proto: git_clone_dependencies generate_proto_types

clean:
	rm -rf ./proto-deps
	rm -rf $(ALLORA_PROTOS_DIR)
	rm -rf $(REST_CLIENT_OUT_DIR)

generate_proto_types: $(ALLORA_CHAIN_DIR)
	rm -frv $(ALLORA_PROTOS_DIR)/*
	mkdir -p $(ALLORA_PROTOS_DIR)
	python -m grpc_tools.protoc \
		--proto_path=$(ALLORA_CHAIN_DIR)/x/emissions/proto \
		--proto_path=$(ALLORA_CHAIN_DIR)/x/mint/proto \
		--proto_path=$(COSMOS_SDK_DIR) \
		--proto_path=$(COSMOS_PROTO_DIR) \
		--proto_path=$(GOOGLEAPIS_DIR) \
		--proto_path=$(GOGOPROTO_DIR) \
		--python_betterproto2_out=$(ALLORA_PROTOS_DIR) \
		--python_betterproto2_opt=client_generation=sync_async \
		$(shell find $(ALLORA_CHAIN_DIR)/x/emissions/proto -type f -name *.proto) \
		$(shell find $(ALLORA_CHAIN_DIR)/x/mint/proto -type f -name *.proto) \
		$(shell find $(COSMOS_SDK_DIR) -type f -name *.proto)

	find $(ALLORA_PROTOS_DIR)/ -type d -exec touch {}/__init__.py \;
# restore root __init__.py as it contains code to have the proto files module available
	git restore $(ALLORA_PROTOS_DIR)/__init__.py

generate_rest_clients: $(ALLORA_CHAIN_DIR)
	rm -rf $(REST_CLIENT_OUT_DIR)

	python scripts/generate_rest_client_from_protos.py \
		--out $(REST_CLIENT_OUT_DIR) \
		--include-tags emissions.v9 mint.v5 cosmos.tx cosmos.base.tendermint.v1beta1 cosmos.auth.v1beta1 cosmos.bank.v1beta1 \
		--proto-files-dirs $(ALLORA_CHAIN_DIR)/x $(COSMOS_SDK_DIR) \
		--include-dirs $(ALLORA_CHAIN_DIR)/x/emissions/proto \
					   $(ALLORA_CHAIN_DIR)/x/mint/proto \
					   $(COSMOS_SDK_DIR) \
					   $(COSMOS_PROTO_DIR) \
					   $(GOGOPROTO_DIR) \
					   $(GOOGLEAPIS_DIR)

	touch $(REST_CLIENT_OUT_DIR)/__init__.py

git_clone_dependencies:
	rm -rf ./proto-deps
	mkdir -p ./proto-deps
	git clone --depth 1 https://github.com/cosmos/gogoproto --single-branch --branch=v1.7.0 ./proto-deps/gogoproto
	git clone --depth 1 https://github.com/cosmos/cosmos-proto --single-branch --branch=v1.0.0-beta.5 ./proto-deps/cosmos-proto
	git clone --depth 1 https://github.com/cosmos/cosmos-sdk --single-branch --branch=v0.50.13 ./proto-deps/cosmos-sdk
	git clone --depth 1 https://github.com/googleapis/googleapis --single-branch --branch=master ./proto-deps/googleapis
	git clone --depth 1 https://github.com/allora-network/allora-chain --single-branch --branch=v0.12.1 ./proto-deps/allora-chain

