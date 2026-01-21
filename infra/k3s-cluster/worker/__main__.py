import os
import json
import pulumi
import base64
import pulumi_aws as aws
import pulumi_kubernetes as k8s # Added this for Helm

# Initialize the configuration object
config = pulumi.Config()

# Get the current organization and current stack dynamically
current_org = pulumi.get_organization()
current_stack = pulumi.get_stack()

# Get Config variables
worker_instance_type = config.require('worker-instance-type')
ami = config.require('ami')
common_project_name = config.require('common-project-name')
master_project_name = config.require('master-project-name')
min_nodes = int(config.require("min-nodes"))
max_nodes = int(config.require("max-nodes"))

# Construct the reference string to access exported variables from common project
common_ref_name = f"{current_org}/{common_project_name}/{current_stack}"
master_ref_name = f"{current_org}/{master_project_name}/{current_stack}"

# Create the StackReference
common_ref = pulumi.StackReference(common_ref_name)
master_ref = pulumi.StackReference(master_ref_name)

# Now pull outputs from common project
s3_bucket_id = common_ref.get_output("s3_bucket_id")
private_subnet_id = common_ref.get_output("private_subnet_id")
security_group_id = common_ref.get_output("security_group_id")
cluster_instance_profile_name = common_ref.get_output("cluster_instance_profile_name")
key_pair_key_name = common_ref.get_output("key_pair_key_name")

# Now pull outputs from master project
target_group_arn = master_ref.get_output("target_group_arn")
alb_dns_name = master_ref.get_output("alb_dns")


# Get the directory where __main__.py is located
current_dir = os.path.dirname(os.path.abspath(__file__))

# Construct the path to the script relative to THIS file
script_path = os.path.join(current_dir, "scripts", "join_cluster.sh")

with open(script_path, 'r') as f:
    user_data_script = f.read()

# Encoding it for the AWS Launch Template
worker_user_data = s3_bucket_id.apply(
    lambda name: base64.b64encode(
        user_data_script.replace("REPLACE_ME_BUCKET_NAME", name).encode('utf-8')
    ).decode('utf-8')
)

# Create a launch Template (The Blueprints for the worker nodes)
worker_launch_template = aws.ec2.LaunchTemplate(
    "worker-lt",
    image_id=ami,
    instance_type=worker_instance_type,
    key_name=key_pair_key_name,
    vpc_security_group_ids=[security_group_id], # worker security group
    iam_instance_profile={
        "name": cluster_instance_profile_name
    },
    block_device_mappings=[aws.ec2.LaunchTemplateBlockDeviceMappingArgs(
        device_name="/dev/sda1", # for Ubuntu 24.04
        ebs=aws.ec2.LaunchTemplateBlockDeviceMappingEbsArgs(
            volume_size=25,
            volume_type="gp3",
            delete_on_termination=True,
        ),
    )],
    user_data=worker_user_data,
)

# Create the Auto Scaling Group
worker_asg = aws.autoscaling.Group("worker-asg",
    vpc_zone_identifiers=[private_subnet_id], # private subnets
    launch_template={
        "id": worker_launch_template.id,
        "version": "$Latest",
    },
    min_size=min_nodes, # Scale down to min 2 instances
    max_size=max_nodes, # Scale up to max 5 instances
    desired_capacity=3,
    target_group_arns=[target_group_arn],
    health_check_type="EC2",
    health_check_grace_period=600,
    tags=[{
        "key": "Name",
        "value": "k3s-worker-node",
        "propagate_at_launch": True,
    }]
)

# Create DynamoDB to prevent multiple scaling events from happening at once
scaling_table = aws.dynamodb.Table(
    "scaling-state",
    attributes=[{"name": "LockID", "type": "S"}],
    hash_key="LockID",
    billing_mode="PAY_PER_REQUEST",
)


# IAM Role for AWS Lambda
lambda_role = aws.iam.Role(
    "lambda-exec-role",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Effect": "Allow",
        }]
    })
)

# Attach permissions to the Role
# Allows Lambda to log to CloudWatch, read DynamoDB, and update ASG
role_policy = aws.iam.RolePolicy("lambda-scaling-policy",
    role=lambda_role.id,
    policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["logs:*", "dynamodb:*", "autoscaling:*", "ec2:DescribeInstances"],
                "Resource": "*"
            }
        ]
    })
)

# Creating The Lambda Function
scaling_lambda = aws.lambda_.Function("cluster-autoscaler",
    role=lambda_role.arn,
    runtime="python3.11",
    handler="main.handler", # The auto-scaling repo must use this filename/function
    # This creates a dummy 'main.py' so Pulumi can finish without the local files
    code=pulumi.AssetArchive({
        "main.py": pulumi.StringAsset("def handler(event, context): print('Placeholder code')")
    }),
    environment={
        "variables": {
            "PROMETHEUS_URL": alb_dns_name.apply(lambda dns: f"http://{dns}/prometheus"),
            "BUCKET_NAME": s3_bucket_id,
            "DYNAMO_TABLE": scaling_table.name,
            "ASG_NAME": worker_asg.name,
            "MIN_NODES": min_nodes,
            "MAX_NODES": max_nodes,
        }
    }
)

# Attach EBS CSI policy to Worker Nodes
# This gives EC2 workers permission to create/attach EBS volumes.
ebs_csi_policy_attachment = aws.iam.RolePolicyAttachment("ebs-csi-policy-attach",
    role=cluster_instance_profile_name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
)

# A K8s provider to talk to the K3s cluster
k8s_provider = k8s.Provider("k3s-provider", kubeconfig=config.require("kubeconfig"))

ebs_csi_driver = k8s.helm.v3.Chart("aws-ebs-csi-driver",
    k8s.helm.v3.ChartOpts(
        chart="aws-ebs-csi-driver",
        version="2.26.0",
        namespace="kube-system",
        fetch_opts=k8s.helm.v3.FetchOpts(
            repo="https://kubernetes-sigs.github.io/aws-ebs-csi-driver"
        ),
        # Must wait for IAM permissions to be active before the pods try to start
    ), opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[ebs_csi_policy_attachment])
)

# Creating the Storage Class
# This is what the PVC will point to (storageClassName: ebs-sc)
ebs_storage_class = k8s.storage.v1.StorageClass("ebs-sc",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="ebs-sc",
    ),
    provisioner="ebs.csi.aws.com",
    reclaim_policy="Delete",
    volume_binding_mode="WaitForFirstConsumer", # Best for multi-AZ clusters
    parameters={
        "type": "gp3",
        "fsType": "ext4",
    }, opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[ebs_csi_driver])
)

pulumi.export("dynamo_table", scaling_table.name)
pulumi.export("lambda_function_name", scaling_lambda.name)
pulumi.export("storage_class_name", ebs_storage_class.metadata.apply(lambda m: m.name))