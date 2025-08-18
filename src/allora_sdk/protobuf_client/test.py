from typing import Annotated, Any
from allora_sdk.protobuf_client.config import AlloraNetworkConfig
from allora_sdk.protobuf_client.client import ProtobufClient
from allora_sdk.protobuf_client.events import EventFilter, EventAttributeCondition
from allora_sdk.protobuf_client.proto.emissions.v3 import Nonce
from allora_sdk.protobuf_client.proto.emissions.v3 import WorkerDataBundle
from allora_sdk.protobuf_client.proto.emissions.v9 import (
    EventScoresSet,
    InputInference,
    InputInferenceForecastBundle,
    InputWorkerDataBundle,
    InsertWorkerPayloadRequest,
)
import json

async def main():
    client = ProtobufClient.testnet(mnemonic="mixture wrap brush symbol conduct patch sheriff urge merit antique sorry eagle")

    # print(f"wallet: {client.address}")
    # print(f"balance: {client.get_balance()}")

    topic_id = 13
    
    # Note: Event subscription starts automatically on first subscribe call
    
    # def handle_untyped_events(events: Dict[str, Any], height: int):
    #     print(f"GENERIC HANDLE: Received {len(events)} filtered events")
    #     for i, event in enumerate(events):
    #         print(f"Event {i+1}:")
    #         print(f"  Type: {event.get('type')}")
    #         print(f"  Attributes: {json.dumps(event.get('attributes', []), indent=4)}")
    #         print()

    # def handle_typed_events(event: EventScoresSet, height: int):
    #     print(f"Typed Event {i+1}:")
    #     print(f"  Topic ID: {event.topic_id}")
    #     print(f"  Actor Type: {event.actor_type}")
    #     print(f"  Block Height: {event.block_height}")
    #     print(f"  Addresses: {event.addresses}")
    #     print(f"  Scores: {event.scores}")
    #     print()

    # # Option 1: Generic subscription (existing approach)
    # await client.events.subscribe_new_block_events(
    #     "emissions.v9.EventScoresSet",
    #     [ EventAttributeCondition("topic_id", "CONTAINS", str(topic_id)) ],
    #     handle_untyped_events
    # )

    # def handle(evt):
    #     print(f"TYPED HANDLE: Received {len(events)} typed EventScoresSet events")
    #     for i, event in enumerate(events):
    #         if event.type == 'InsertWorkerPayload':
    #             prediction = call_predict_function()
    #             client.emissions.insert_worker_payload(topic_id, prediction ------)

    
    # # Option 2: NEW - Typed subscription (protobuf instances)
    # await client.events.subscribe_new_block_events_typed(
    #     EventScoresSet,
    #     [ EventAttributeCondition("topic_id", "CONTAINS", str(topic_id)) ],
    #     handle_typed_events
    # )


    # resp = await client.emissions.params()
    # print(f"emissions params: {resp.to_json(indent=4)}")
    # resp = await client.mint.inflation()
    # print(f"inflation: {resp.to_json(indent=4)}")
    # resp = await client.mint.emission_info()
    # print(f"emission_info: {resp.to_json(indent=4)}")
    # resp = client.get_balance()
    # print(f"balance: {json.dumps(resp, indent=4)}")

    # b3lock_height = 123
    # value = '123.45'

    # response = await client.emissions.insert_worker_payload(
    #     InsertWorkerPayloadRequest(sender=client.address or '', worker_data_bundle=InputWorkerDataBundle(
    #         worker=client.address or '',
    #         nonce=Nonce(block_height=block_height),
    #         topic_id=13,
    #         inference_forecasts_bundle=InputInferenceForecastBundle(
    #             inference=InputInference(topic_id=13,block_height=block_height, inferer=client.address or '', value=value),
    #         )
    #     )),
    # )

    
    # #### ALTERNATIVE: Keep the old method for comparison/testing
    # # OLD METHOD: Manual EventFilter construction (still works)
    # # f = EventFilter().event_type('NewBlockEvents').custom(f"emissions.v9.EventScoresSet.topic_id CONTAINS '{topic_id}'")
    # # def handle_old(evt: dict):
    # #     print("OLD HANDLE:", json.dumps(evt, indent=2))
    # # await client.events.subscribe(f, handle_old)

    # #### KEEP THESE FOR FUTURE WORK AND TESTING
    # # f = EventFilter().event_type('NewBlock')
    # # f = EventFilter().event_type('NewBlockEvents').message_action('emissions.v9.EventTopicRewardsSet')
    # # f = EventFilter().event_type('NewBlockEvents').attribute('emissions.v9.EventTopicRewardsSet.mode', 'EndBlock')
    # # f = EventFilter().event_type('Tx').message_action('/emissions.v9.InsertWorkerPayloadRequest')

    # print(f"Subscribed to EventScoresSet events for topic_id={topic_id} (both generic and typed). Waiting for events...")
    # try:
    #     while True:
    #         await asyncio.sleep(1)  # Keep the event loop running
    # except KeyboardInterrupt:
    #     print("Stopping event subscription...")
    #     await client.stop_event_subscription()
    #     print("Event subscription stopped.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())