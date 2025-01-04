from typing import Dict, List, Any
from datetime import datetime

mock_topic: Dict[str, Any] = {
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
