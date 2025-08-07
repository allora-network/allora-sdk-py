#!/usr/bin/env python3
"""
Script to generate Python code from protobuf files using grpc_tools.protoc
"""

import os
import subprocess
import sys
from pathlib import Path

def run_protoc(proto_files, include_paths, output_dir):
    """Run protoc to generate Python code"""
    cmd = [
        sys.executable, "-m", "grpc_tools.protoc",
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}"
    ]

    # Add include paths
    for include_path in include_paths:
        cmd.extend(["-I", include_path])

    # Add proto files
    cmd.extend(proto_files)

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return False

    print(f"Success: {result.stdout}")
    return True

def main():
    # Define paths
    base_dir = Path(__file__).parent
    deps_dir = base_dir

    # Create output directories
    emissions_output = base_dir / "src" / "allora_sdk" / "protobuf_client" / "proto" / "emissions"
    mint_output = base_dir / "src" / "allora_sdk" / "protobuf_client" / "proto" / "mint"

    emissions_output.mkdir(parents=True, exist_ok=True)
    mint_output.mkdir(parents=True, exist_ok=True)

    # Generate emissions proto
    print("Generating emissions proto...")
    emissions_proto_dir = deps_dir / "allora-chain" / "x" / "emissions" / "proto"
    emissions_proto_files = list(emissions_proto_dir.rglob("*.proto"))

    emissions_include_paths = [
        str(emissions_proto_dir),
        str(deps_dir / "cosmos-sdk" / "proto"),
        str(deps_dir / "cosmos-proto" / "proto"),
        str(deps_dir / "gogoproto"),
        str(deps_dir / "googleapis"),
    ]

    success = run_protoc(
        [str(f) for f in emissions_proto_files],
        emissions_include_paths,
        str(emissions_output)
    )

    if not success:
        print("Failed to generate emissions proto")
        return 1

    # Generate mint proto
    print("Generating mint proto...")
    mint_proto_dir = deps_dir / "allora-chain" / "x" / "mint" / "proto"
    mint_proto_files = list(mint_proto_dir.rglob("*.proto"))

    mint_include_paths = [
        str(mint_proto_dir),
        str(deps_dir / "cosmos-sdk" / "proto"),
        str(deps_dir / "cosmos-proto" / "proto"),
        str(deps_dir / "gogoproto"),
        str(deps_dir / "googleapis"),
    ]

    success = run_protoc(
        [str(f) for f in mint_proto_files],
        mint_include_paths,
        str(mint_output)
    )

    if not success:
        print("Failed to generate mint proto")
        return 1

    print("Python proto generation completed successfully!")
    return 0

if __name__ == "__main__":
    sys.exit(main())