import os
import boto3
import urllib3
import json

def handler(event, context):
    s3 = boto3.client('s3')
    asg = boto3.client('autoscaling')

    bucket = os.environ['BUCKET_NAME']
    asg_name = os.environ['ASG_NAME']

    # Get Master IP and Token from the S3 bucket
    response = s3.get_object(Bucket=bucket, Key='cluster_info')
    cluster_data = response['Body'].read().decode('utf-8').strip()
    master_ip, token = cluster_data.split('|')

    # Query Prometheus API
    prom_url = f"http://{master_ip}:9090"
    http = urllib3.PoolManager()
    query = 'avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100'

    try:
        r = http.request('GET', f"{prom_url}/api/v1/query", fields={'query': query})
        result = json.loads(r.data.decode('utf-8'))
        idle = float(result['data']['result'][0]['value'][1])
        usage = 100 - idle

        print(f"Current K3s CPU Usage: {usage}%")

        # Scale Logic
        if usage > 80:
            asg_info = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
            current = asg_info['AutoScalingGroups'][0]['DesiredCapacity']
            asg.set_desired_capacity(AutoScalingGroupName=asg_name, DesiredCapacity=current + 1)
    except Exception as e:
        print(f"Error querying Prometheus: {e}")