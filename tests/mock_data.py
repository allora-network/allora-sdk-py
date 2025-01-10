from typing import Dict, List, Any
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient
from allora_sdk.v2.api_client import Fetcher

class MockServer:
    def __init__(self):
        self.app = FastAPI()
        self.mock_responses: List[Any] = []
        self.mock_status_codes: List[int] = []

        self.app.add_api_route("/{path:path}", self.mock_endpoint, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])

    def add_mock_response(self, response: Any, status_code: int = 200):
        self.mock_responses.append(response)
        self.mock_status_codes.append(status_code)

    async def mock_endpoint(self, request: Request):
        if self.mock_responses:
            response = self.mock_responses.pop(0)
            status_code = self.mock_status_codes.pop(0) if self.mock_status_codes else 200
            return JSONResponse(content=response, status_code=status_code)

        return JSONResponse(content={"error": "No mock response available", "status": False}, status_code=404)

    # def run(self):
    #     uvicorn.run(self.app, host="localhost", port=self.port, log_level="error")

    # def stop(self):
    #     raise NotImplementedError("Stopping the server programmatically requires custom signal handling.")


class StarletteMockFetcher(Fetcher):
    def __init__(self, server: MockServer):
        self.server = server

    async def fetch(self, url: str, headers: dict) -> Any:
        client = TestClient(self.server.app)
        response = client.get(url, headers=headers)
        return response.json()



mock_topic_1: Dict[str, Any] = {
    "topic_id": 1,
    "topic_name": "Test Topic",
    "description": "Test Description",
    "epoch_length": 300,
    "ground_truth_lag": 60,
    "loss_method": "mse",
    "worker_submission_window": 30,
    "worker_count": 5,
    "reputer_count": 3,
    "total_staked_allo": 1000,
    "total_emissions_allo": 100,
    "is_active": True,
    "updated_at": datetime(2024, 3, 20).isoformat() + "Z",
}

mock_topic_2: Dict[str, Any] = {
    "topic_id": 2,
    "topic_name": "Test Topic",
    "description": "Test Description",
    "epoch_length": 300,
    "ground_truth_lag": 60,
    "loss_method": "mse",
    "worker_submission_window": 30,
    "worker_count": 5,
    "reputer_count": 3,
    "total_staked_allo": 1000,
    "total_emissions_allo": 100,
    "is_active": True,
    "updated_at": datetime(2024, 3, 20).isoformat() + "Z",
}

mock_topics_response: Dict[str, Any] = {
    "request_id": "test-request-id",
    "status": True,
    "data": {
        "topics": [mock_topic_1, mock_topic_2],
        "continuation_token": None,
    },
}

mock_inference_data: Dict[str, Any] = {
    "network_inference": "1000000000000000000",
    "network_inference_normalized": "1.0",
    "confidence_interval_percentiles": ["0.25", "0.75"],
    "confidence_interval_percentiles_normalized": ["0.25", "0.75"],
    "confidence_interval_values": ["900000000000000000", "1100000000000000000"],
    "confidence_interval_values_normalized": ["0.9", "1.1"],
    "topic_id": "1",
    "timestamp": 1679529600,
    "extra_data": "",
}

mock_inference: Dict[str, Any] = {
    "signature": "0x1234567890",
    "inference_data": mock_inference_data,
}

mock_api_response: Dict[str, Any] = {
    "request_id": "test-request-id",
    "status": True,
    "data": mock_inference,
}
