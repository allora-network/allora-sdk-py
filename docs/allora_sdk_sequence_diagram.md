# Allora SDK Sequence Diagram

## Overview
The Allora SDK provides a Python interface for ML developers to submit predictions to the Allora Network. It handles WebSocket subscriptions, blockchain transactions, and automatic retries.

## Detailed Sequence Diagram

```mermaid
sequenceDiagram
    participant User as User/ML Model
    participant Worker as AlloraWorker
    participant Wallet as Wallet Manager
    participant RPC as AlloraRPCClient
    participant Chain as Allora Blockchain
    participant WS as WebSocket Events
    participant API as Allora API

    %% Initialization Phase
    rect rgb(240, 240, 240)
        Note over User,API: Initialization Phase
        User->>Worker: Initialize AlloraWorker(predict_fn, topic_id, api_key)
        Worker->>Wallet: Initialize/Load Wallet
        alt Wallet doesn't exist
            Wallet->>Wallet: Generate new mnemonic
            Wallet->>Wallet: Save to file (.allora_key)
        else Wallet exists
            Wallet->>Wallet: Load from file/mnemonic/private_key
        end
        Wallet-->>Worker: Return LocalWallet instance
        
        Worker->>RPC: Initialize AlloraRPCClient
        RPC->>RPC: Setup gRPC/REST channels
        RPC-->>Worker: Client ready
        
        Worker->>Chain: Check wallet balance
        Chain-->>Worker: Return balance
        alt Balance < MIN_ALLO
            Worker->>API: Request testnet faucet
            API-->>Worker: Send ALLO tokens
            loop Check balance
                Worker->>Chain: Query balance
                Chain-->>Worker: Return updated balance
            end
        end
    end

    %% Main Execution Loop
    rect rgb(230, 245, 230)
        Note over User,API: Main Execution Loop
        User->>Worker: async for result in worker.run()
        Worker->>Worker: Setup signal handlers
        Worker->>Worker: Create WorkerContext
        Worker->>Worker: Create prediction queue
        
        %% Registration Check
        Worker->>Chain: Check if worker registered for topic
        Chain-->>Worker: Registration status
        alt Not registered
            Worker->>Chain: Register worker for topic
            Chain-->>Worker: Registration confirmation
        end
        
        %% Start parallel tasks
        par Polling Worker
            loop Every 10 seconds
                Worker->>Chain: Check unfulfilled nonces
                Chain-->>Worker: Return nonce list
                Worker->>Worker: Filter new nonces
                loop For each new nonce
                    Worker->>User: Call predict_fn()
                    User-->>Worker: Return prediction value
                    Worker->>Chain: Submit prediction (InsertWorkerPayload)
                    Chain-->>Worker: Transaction response
                    Worker->>Worker: Add to submitted_nonces
                    Worker->>Worker: Queue result for user
                end
            end
        and WebSocket Listener
            Worker->>WS: Subscribe to EventNetworkLossSet events
            WS-->>Worker: Subscription ID
            loop On new epoch event
                WS->>Worker: New epoch event (topic_id, height, nonce)
                Worker->>Worker: Trigger prediction submission
                Worker->>User: Call predict_fn()
                User-->>Worker: Return prediction
                Worker->>Chain: Submit prediction for nonce
                Chain-->>Worker: Transaction response
                Worker->>Worker: Queue result for user
            end
        and Result Yielder
            loop While not cancelled
                Worker->>Worker: Get from prediction queue
                Worker-->>User: Yield PredictionResult or Exception
            end
        end
    end

    %% Cleanup Phase
    rect rgb(245, 230, 230)
        Note over User,API: Cleanup Phase
        alt Manual stop
            User->>Worker: worker.stop()
        else Signal received
            Worker->>Worker: Signal handler triggered (SIGINT/SIGTERM)
        else Timeout reached
            Worker->>Worker: Timeout exceeded
        end
        
        Worker->>Worker: Cancel WorkerContext
        Worker->>WS: Unsubscribe from events
        WS-->>Worker: Unsubscribe confirmation
        Worker->>Worker: Cancel all tasks
        Worker->>Worker: Clear prediction queue
        Worker-->>User: Exit async generator
    end
```

## Key Components

### 1. **AlloraWorker**
- Main entry point for ML developers
- Manages the lifecycle of prediction submissions
- Handles both polling and event-driven submission strategies

### 2. **WorkerContext**
- Go-like context pattern for coordinating shutdown
- Manages cancellation across all async tasks
- Ensures graceful cleanup of resources

### 3. **Wallet Management**
- Automatic wallet creation/loading
- Support for mnemonic, private key, or file-based storage
- Automatic faucet requests for testnet

### 4. **AlloraRPCClient**
- Wraps cosmpy's LedgerClient
- Provides Allora-specific blockchain operations
- Supports both gRPC and REST protocols

### 5. **Prediction Submission Flow**
- **Polling Mode**: Checks for unfulfilled nonces every 10 seconds
- **Event Mode**: Responds to WebSocket events for new epochs
- **Deduplication**: Tracks submitted nonces to avoid duplicates
- **Error Handling**: Queues exceptions for user handling

### 6. **Transaction Management**
- Configurable fee tiers (ECO, STANDARD, PRIORITY)
- Automatic retry logic for failed transactions
- Detailed error reporting with transaction hashes

## Data Flow

1. **Input**: User provides ML prediction function
2. **Processing**: Worker manages timing and submission
3. **Output**: Async iterator yields results/errors

## Error Handling

- Network errors are caught and yielded as exceptions
- Worker not whitelisted errors trigger graceful shutdown
- Transaction failures are logged with error codes
- Duplicate submissions are automatically filtered

## Environment Detection

The SDK automatically detects and adapts to:
- Shell environments (with signal handling)
- Jupyter notebooks
- Google Colab

## Resource Management

- WebSocket subscriptions are properly closed
- All async tasks are cancelled on shutdown
- Memory-efficient nonce tracking with automatic pruning
- Queue-based result delivery prevents memory leaks
