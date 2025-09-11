#!/usr/bin/env python3
"""
Script to process transaction YAML files and convert filtered data to CSV.
Filters for transactions with InsertWorkerPayloadRequest messages and extracts
relevant fields to CSV format.
"""

import csv
from math import ceil
from typing import List, Dict
import argparse
from allora_sdk.rpc_client.client import AlloraRPCClient
from allora_sdk.rpc_client.config import AlloraNetworkConfig
from allora_sdk.protos.cosmos.tx.v1beta1 import GetTxsEventRequest, GetTxsEventResponse, OrderBy
from allora_sdk.protos.emissions.v9 import InsertWorkerPayloadRequest


def main():
    """Main function to orchestrate the processing."""
    parser = argparse.ArgumentParser(
        description="Export Allora inference worker transactions from an address to CSV"
    )
    parser.add_argument(
        "--address",
        required=True,
        help="The address to fetch transactions for",
    )
    parser.add_argument(
        "--url",
        default="grpc+https://allora-grpc.testnet.allora.network",
        help="The URL of the RPC endpoint",
    )
    parser.add_argument(
        "--page_size",
        default=10,
        help="The number of txs to fetch per request (lower if you have issues)",
    )
    parser.add_argument(
        "--pages",
        default=1000,
        help="The total number of pages to fetch",
    )
    parser.add_argument(
        "--start_page",
        default=1,
        help="The page on which to start fetching (useful with --resume)",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Set to true if you want to resume an existing fetch",
    )
    parser.add_argument(
        "--order",
        default="desc",
        help="'desc' to start from most recent or 'asc' to start from oldest",
    )
    parser.add_argument(
        "--output_file",
        default="transactions.csv",
        help="Output CSV file path (default: transactions.csv)"
    )

    args = parser.parse_args()
    write_header = not bool(args.resume)
    page = int(args.start_page)
    order = OrderBy.ASC if args.order == "asc" else OrderBy.DESC
    query = f"message.sender='{args.address}'"

    network = AlloraNetworkConfig(chain_id="allora-testnet-1", url=args.url, websocket_url="")
    client = AlloraRPCClient(network=network, wallet=None, debug=False)
    while page <= int(args.pages):
        try:
            req = GetTxsEventRequest(query=query, order_by=order, page=page, limit=args.page_size)
            resp = client.tx.get_txs_event(req)
            if resp is None:
                raise Exception("resp is None")

            records = filter_and_extract_data(resp)
            write_to_csv(records, args.output_file, write_header=write_header)
            write_header = False

            total_pages = ceil(resp.total / args.page_size)
            print(f"Wrote page {page} of {total_pages} ({resp.total} txs)")

            page += 1

        except Exception as e:
            print(f"Error: {e}")
            continue

    print("Processing completed successfully!")
    return 0


def filter_and_extract_data(data: GetTxsEventResponse):
    extracted_records = []

    for tx, tx_resp in zip(data.txs, data.tx_responses):
        if tx.body is None:
            continue

        target_suffix = "emissions.v9.InsertWorkerPayloadRequest"
        qualifying_messages = [ msg for msg in tx.body.messages if msg.type_url.endswith(target_suffix) ]

        for m in qualifying_messages:
            msg: InsertWorkerPayloadRequest = m.unpack()
            if (
                msg.worker_data_bundle is None
                or msg.worker_data_bundle.inference_forecasts_bundle is None
                or msg.worker_data_bundle.nonce is None
                or msg.worker_data_bundle.inference_forecasts_bundle.inference is None
            ):
                continue

            inference = msg.worker_data_bundle.inference_forecasts_bundle.inference
            record = {
                "timestamp": tx_resp.timestamp,
                "code": tx_resp.code,
                "nonce_block_height": msg.worker_data_bundle.nonce.block_height,
                "topic_id": inference.topic_id,
                "value": inference.value,
                "inferer": inference.inferer,
                "txhash": tx_resp.txhash,
            }

            extracted_records.append(record)

    return extracted_records


def write_to_csv(records: List[Dict[str, str]], output_file: str, write_header: bool) -> None:
    if not records:
        print("No records to write to CSV.")
        return

    fieldnames = [
        "timestamp", "code", "nonce_block_height",
        "topic_id", "value", "inferer", "txhash"
    ]

    try:
        mode = "w" if write_header else "a"
        with open(output_file, mode, newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerows(records)

        print(f"Successfully wrote {len(records)} records to {output_file}")

    except IOError as e:
        raise IOError(f"Error writing to CSV file: {e}")



if __name__ == "__main__":
    exit(main())