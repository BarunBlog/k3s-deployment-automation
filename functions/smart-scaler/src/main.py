import os
import logging
from scaler import SmartScaler
from state_manager import StateManager
from metrics import PrometheusClient
from typing import Any, Dict

# Configuring the structured logging
logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

DYNAMO_TABLE = os.environ.get('DYNAMO_TABLE')
state_manager = StateManager(DYNAMO_TABLE) if DYNAMO_TABLE else None

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler to orchestrate K3s cluster auto-scaling.
    """

    logger.info("Auto-scaling check initiated.", extra={"event": event})

    if not state_manager:
        logger.error("Environment variable DYNAMO_TABLE is not set.")
        return {"status": "error", "message": "Configuration error"}

    # Use Context Manager or Try/Finally for Lock Safety
    if not state_manager.acquire_lock():
        logger.warning("Scaling operation already in progress. Skipping execution.")
        return {"status": "skipped", "message": "Lock active"}

    try:
        metrics_client = PrometheusClient()
        scaler = SmartScaler()

        # Fetching Metrics
        cpu_usage = metrics_client.get_avg_cpu()
        pending_pods = metrics_client.get_pending_pods()

        logger.info(
            "Cluster Metrics Fetched",
            extra={"cpu": cpu_usage, "pending_pods": pending_pods}
        )

        # Scaling Logic
        current_capacity = scaler.get_current_capacity()
        recommended_capacity = scaler.make_decision(cpu_usage, pending_pods)

        if recommended_capacity != current_capacity:
            logger.info(
                "Capacity mismatch detected. Scaling...",
                extra={"from": current_capacity, "to": recommended_capacity}
            )
            scaler.apply_scaling(recommended_capacity)
        else:
            logger.info("Cluster capacity is optimal. No action taken.")

        return {"status": "success", "recommended_capacity": recommended_capacity}

    except Exception as e:
        logger.exception("An unhandled error occurred during the scaling process.")
        return {"status": "error", "message": str(e)}

    finally:
        state_manager.release_lock()
        logger.debug("State lock released.")