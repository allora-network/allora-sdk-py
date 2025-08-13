

COSMOS_SDK_DIR := ../../cosmos-sdk/proto
GOGOPROTO_DIR := ./proto-deps/gogoproto
GOOGLEAPIS_DIR := ./proto-deps/googleapis
COSMOS_PROTO_DIR := ./proto-deps/cosmos-proto/proto
ALLORA_CHAIN_DIR := ../allora-chain
ALLORA_PROTOS_DIR := src/allora_sdk/protobuf_client/protos
REST_CLIENT_OUT_DIR := src/allora_sdk/protobuf_client/rest

########################################
### Generate protos and grpc files
########################################

ifeq ($(OS),Windows_NT)
	$(error "Please use the WSL (Windows Subsystem for Linux) on Windows platform.")
else
    UNAME_S := $(shell uname -s)
    ifeq ($(UNAME_S),Linux)
		OPEN_CMD := xdg-open
    endif
    ifeq ($(UNAME_S),Darwin)
		OPEN_CMD := open
    endif
endif

define unique
  $(eval seen :=)
  $(foreach _,$1,$(if $(filter $_,${seen}),,$(eval seen += $_)))
  ${seen}
endef
unique = $(if $1,$(firstword $1) $(call unique,$(filter-out $(firstword $1),$1)))

proto: fetch_proto_schema_source generate_proto_types generate_init_py_files generate_rest_clients

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

fetch_proto_schema_source: $(ALLORA_CHAIN_DIR)

.PHONY: generate_init_py_files
generate_init_py_files: generate_proto_types
	find $(ALLORA_PROTOS_DIR)/ -type d -exec touch {}/__init__.py \;
# restore root __init__.py as it contains code to have the proto files module available
	git restore $(ALLORA_PROTOS_DIR)/__init__.py

.PHONY: generate_rest_clients
generate_rest_clients: $(ALLORA_CHAIN_DIR)
	rm -rf $(REST_CLIENT_OUT_DIR)

	python scripts/generate_rest_client.py \
	    --out ./src/allora_sdk/rest/ \
	    --include-tags emissions.v9 mint.v5 \
	    --swagger-json $(ALLORA_CHAIN_DIR)/openapi/allora.swagger.json

# 	python scripts/generate_rest_client_from_openapi.py \
# 		--spec $(ALLORA_CHAIN_DIR)/openapi/allora.swagger.json \
# 		--out src/allora_sdk/rest/generated_client.py \
# 		--include-tags emissions.v9 mint.v5

	touch src/allora_sdk/rest/__init__.py
# 	npx @openapitools/openapi-generator-cli generate \
# 		-i $(ALLORA_CHAIN_DIR)/openapi/allora.swagger.json \
# 		-g python -o src/allora_sdk/protobuf_client/rest \
# 		--additional-properties=packageName=.,generateSourceCodeOnly=true \
# 		--skip-validate-spec

git_clone_dependencies:
	rm -rf ./proto-deps
	mkdir -p ./proto-deps
	git clone --depth 1 https://github.com/cosmos/gogoproto --single-branch --branch=main ./proto-deps/gogoproto
	git clone --depth 1 https://github.com/cosmos/cosmos-proto --single-branch --branch=main ./proto-deps/cosmos-proto
	git clone --depth 1 https://github.com/googleapis/googleapis --single-branch --branch=master ./proto-deps/googleapis
