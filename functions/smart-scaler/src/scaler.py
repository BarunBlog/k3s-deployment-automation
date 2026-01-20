import boto3
import os
import logging
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class SmartScaler:
    def __init__(self):
        self.asg_client = boto3.client('autoscaling')
        self.asg_name = os.environ['ASG_NAME']

        self.min_nodes = int(os.environ.get('MIN_NODES', 2))
        self.max_nodes = int(os.environ.get('MAX_NODES', 5))

        # Thresholds
        self.scale_up_cpu = 70.0
        self.scale_down_cpu = 30.0

    def get_current_capacity(self):
        """Fetches the current Desired Capacity from AWS ASG."""
        try:
            response = self.asg_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[self.asg_name]
            )

            if not response['AutoScalingGroups']:
                raise ValueError(f"ASG with name {self.asg_name} not found.")

            return response['AutoScalingGroups'][0]['DesiredCapacity']

        except ClientError as e:
            logger.error(f"Failed to describe ASG: {e}")
            raise

    def make_decision(self, cpu_utilization: float, pending_pods_count: int) -> int:
        """
        Business logic for scaling decisions.
        Prioritizes Scale-Up for availability, Conservative Scale-Down for stability.
        """
        current = self.get_current_capacity()
        logger.debug(f"Current Desired Capacity: {current}")

        # Scale Up (High CPU or Pending Pods)
        if cpu_utilization > self.scale_up_cpu or pending_pods_count > 0:
            if current < self.max_nodes:
                target = current + 1
                logger.info(
                    f"Decision: SCALE_UP to {target}. Reason: CPU={cpu_utilization}%, Pending={pending_pods_count}")
                return current + 1
            else:
                logger.warning("Max node limit reached. Cannot scale up further.")

        # Scale Down (Low CPU or no Pending Pods)
        elif cpu_utilization < self.scale_down_cpu and pending_pods_count == 0:
            if current > self.min_nodes:
                target = current - 1
                logger.info(f"Decision: SCALE_DOWN to {target}. Reason: CPU={cpu_utilization}%")
                return target

        return current  # No change

    def apply_scaling(self, new_capacity: int):
        """Executes the scaling command in AWS."""
        try:
            logger.info(f"Applying scaling: Setting {self.asg_name} desired capacity to {new_capacity}")

            self.asg_client.set_desired_capacity(
                AutoScalingGroupName=self.asg_name,
                DesiredCapacity=new_capacity,
                HonorCooldown=True  # Respects the ASG cooldown period to prevent thrashing
            )
        except ClientError as e:
            logger.error(f"AWS API Error while scaling: {e}")
            raise
