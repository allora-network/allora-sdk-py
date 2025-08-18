from typing import Protocol, runtime_checkable
import requests
import json
from allora_sdk.protobuf_client.protos.emissions.v9 import (
    AddStakeRequest,
    AddStakeResponse,
    AddToGlobalAdminWhitelistRequest,
    AddToGlobalAdminWhitelistResponse,
    AddToGlobalReputerWhitelistRequest,
    AddToGlobalReputerWhitelistResponse,
    AddToGlobalWhitelistRequest,
    AddToGlobalWhitelistResponse,
    AddToGlobalWorkerWhitelistRequest,
    AddToGlobalWorkerWhitelistResponse,
    AddToTopicCreatorWhitelistRequest,
    AddToTopicCreatorWhitelistResponse,
    AddToTopicReputerWhitelistRequest,
    AddToTopicReputerWhitelistResponse,
    AddToTopicWorkerWhitelistRequest,
    AddToTopicWorkerWhitelistResponse,
    AddToWhitelistAdminRequest,
    AddToWhitelistAdminResponse,
    BulkAddToGlobalReputerWhitelistRequest,
    BulkAddToGlobalReputerWhitelistResponse,
    BulkAddToGlobalWorkerWhitelistRequest,
    BulkAddToGlobalWorkerWhitelistResponse,
    BulkAddToTopicReputerWhitelistRequest,
    BulkAddToTopicReputerWhitelistResponse,
    BulkAddToTopicWorkerWhitelistRequest,
    BulkAddToTopicWorkerWhitelistResponse,
    BulkRemoveFromGlobalReputerWhitelistRequest,
    BulkRemoveFromGlobalReputerWhitelistResponse,
    BulkRemoveFromGlobalWorkerWhitelistRequest,
    BulkRemoveFromGlobalWorkerWhitelistResponse,
    BulkRemoveFromTopicReputerWhitelistRequest,
    BulkRemoveFromTopicReputerWhitelistResponse,
    BulkRemoveFromTopicWorkerWhitelistRequest,
    BulkRemoveFromTopicWorkerWhitelistResponse,
    CancelRemoveDelegateStakeRequest,
    CancelRemoveDelegateStakeResponse,
    CancelRemoveStakeRequest,
    CancelRemoveStakeResponse,
    CreateNewTopicRequest,
    CreateNewTopicResponse,
    DelegateStakeRequest,
    DelegateStakeResponse,
    DisableTopicReputerWhitelistRequest,
    DisableTopicReputerWhitelistResponse,
    DisableTopicWorkerWhitelistRequest,
    DisableTopicWorkerWhitelistResponse,
    EnableTopicReputerWhitelistRequest,
    EnableTopicReputerWhitelistResponse,
    EnableTopicWorkerWhitelistRequest,
    EnableTopicWorkerWhitelistResponse,
    FundTopicRequest,
    FundTopicResponse,
    InsertReputerPayloadRequest,
    InsertReputerPayloadResponse,
    InsertWorkerPayloadRequest,
    InsertWorkerPayloadResponse,
    RegisterRequest,
    RegisterResponse,
    RemoveDelegateStakeRequest,
    RemoveDelegateStakeResponse,
    RemoveFromGlobalAdminWhitelistRequest,
    RemoveFromGlobalAdminWhitelistResponse,
    RemoveFromGlobalReputerWhitelistRequest,
    RemoveFromGlobalReputerWhitelistResponse,
    RemoveFromGlobalWhitelistRequest,
    RemoveFromGlobalWhitelistResponse,
    RemoveFromGlobalWorkerWhitelistRequest,
    RemoveFromGlobalWorkerWhitelistResponse,
    RemoveFromTopicCreatorWhitelistRequest,
    RemoveFromTopicCreatorWhitelistResponse,
    RemoveFromTopicReputerWhitelistRequest,
    RemoveFromTopicReputerWhitelistResponse,
    RemoveFromTopicWorkerWhitelistRequest,
    RemoveFromTopicWorkerWhitelistResponse,
    RemoveFromWhitelistAdminRequest,
    RemoveFromWhitelistAdminResponse,
    RemoveRegistrationRequest,
    RemoveRegistrationResponse,
    RemoveStakeRequest,
    RemoveStakeResponse,
    RewardDelegateStakeRequest,
    RewardDelegateStakeResponse,
    UpdateParamsRequest,
    UpdateParamsResponse,
)

@runtime_checkable
class EmissionsV9QueryLike(Protocol):
    pass

class EmissionsV9RestQueryClient(EmissionsV9QueryLike):
    """Emissions.V9 REST client."""

    def __init__(self, base_url: str):
        """
        Initialize REST client.

        :param base_url: Base URL for the REST API
        """
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()

    def __del__(self):
        """Clean up session on deletion."""
        if hasattr(self, 'session'):
            self.session.close()
