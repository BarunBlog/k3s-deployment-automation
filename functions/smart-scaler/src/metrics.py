import requests
import os
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class PrometheusClient:
    def __init__(self):
        # to ensure the URL doesn't have a trailing slash to avoid // in the API path
        self.url = os.environ['PROMETHEUS_URL'].rstrip('/')

    def query_metric(self, promql_query):
        try:
            response = requests.get(f"{self.url}/api/v1/query", params={'query': promql_query}, timeout=10)
            response.raise_for_status()
            data = response.json()

            status = data.get('status')
            if status != 'success':
                error_type = data.get('errorType', 'UnknownError')
                error_msg = data.get('error', 'No error message provided')
                raise ValueError(f"Prometheus API returned error ({error_type}): {error_msg}")

            results = data.get('data', {}).get('result', [])
            if not results:
                logger.info(f"Query returned no data points: {promql_query}. Interpreting as 0.")
                return 0.0

            # The value is usually a list like [timestamp, "value"]
            return float(results[0]['value'][1])
        except Exception as e:
            logger.error(f"Prometheus query failed: {e}")
            raise

    def get_avg_cpu(self):
        """
        Query: Average CPU usage across all nodes.
        Filters out 'idle' time to get actual utilization.
        """
        query = '100 - (avg(irate(node_cpu_seconds_total{mode="idle"}[10m])) * 100)'
        return self.query_metric(query)

    def get_pending_pods(self):
        """
        The 'Smart' Query:
        Only count pods where the Scheduler explicitly says 'Unschedulable'.
        This ignores pods pending due to ImagePullBackOff or OOMKills.
        """
        query = 'sum(kube_pod_scheduler_status_condition{condition="Scheduled", status="False", reason="Unschedulable"})'
        count = self.query_metric(query)
        logger.info(f"Detected {count} unschedulable (pending) pods.")
        return int(count)