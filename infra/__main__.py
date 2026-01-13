import os
import json
import base64
import pulumi
import pulumi_aws as aws
import pulumi_aws.ec2 as ec2

# Configuration setup
config = pulumi.Config()

master_instance_type = 't3.medium'
worker_instance_type = 't3.medium'
runner_instance_type = 't3.medium'

MIN_NODES = 2
MAX_NODES = 5

ami = "ami-060e277c0d4cce553"

# Creating an s3 bucket
bucket_name = "k3s-s3-bucket"
s3_bucket = aws.s3.Bucket(bucket_name)

# Create the IAM Role to allow nodes to access the bucket
cluster_node_role = aws.iam.Role(
    "k3s-node-role",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Effect": "Allow",
        }]
    })
)

# Define the Policy (Allows Put and Get for the bucket)
node_s3_policy = aws.iam.RolePolicy("node-s3-policy",
    role=cluster_node_role.id,
    policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*"
                ]
            }
        ]
    })
)

# Create the Instance Profile (The "ID Badge" EC2 actually wears)
cluster_instance_profile = aws.iam.InstanceProfile("k3s-instance-profile",
    role=cluster_node_role.name
)

# Create a VPC
vpc = ec2.Vpc(
    'my-vpc',
    cidr_block='10.0.0.0/16',
    enable_dns_hostnames=True,
    enable_dns_support=True,
    tags={
        'Name': 'my-vpc',
    }
)

# Create subnets
# Public Subnet
public_subnet = ec2.Subnet('public-subnet',
    vpc_id=vpc.id,
    cidr_block='10.0.1.0/24',
    map_public_ip_on_launch=True,
    availability_zone='ap-southeast-1a',
    tags={
        'Name': 'public-subnet',
    }
)

# Another public subnet on different az created for the ALB
public_subnet_2 = ec2.Subnet(
    'public-subnet-2',
    vpc_id=vpc.id,
    cidr_block='10.0.3.0/24',
    map_public_ip_on_launch=True,
    availability_zone='ap-southeast-1b',
    tags={'Name': 'public-subnet-2'},
)

# Private Subnet
private_subnet = ec2.Subnet('private-subnet',
    vpc_id=vpc.id,
    cidr_block='10.0.2.0/24',
    map_public_ip_on_launch=False,
    availability_zone='ap-southeast-1a',
    tags={
        'Name': 'private-subnet',
    }
)

# Internet Gateway
igw = ec2.InternetGateway('internet-gateway', vpc_id=vpc.id)


# Route Table for Public Subnet
public_route_table = ec2.RouteTable('public-route-table',
    vpc_id=vpc.id,
    routes=[{
        'cidr_block': '0.0.0.0/0',
        'gateway_id': igw.id,
    }],
    tags={
        'Name': 'public-route-table',
    }
)

# Associate the public route table with the public subnet
public_route_table_association = ec2.RouteTableAssociation(
    'public-route-table-association',
    subnet_id=public_subnet.id,
    route_table_id=public_route_table.id
)

ec2.RouteTableAssociation(
    'public-route-table-association-2',
    subnet_id=public_subnet_2.id,
    route_table_id=public_route_table.id
)

# Elastic IP for NAT Gateway
eip = ec2.Eip('nat-eip')

# NAT Gateway
nat_gateway = ec2.NatGateway(
    'nat-gateway',
    subnet_id=public_subnet.id,
    allocation_id=eip.id,
    tags={
        'Name': 'nat-gateway',
    }
)

# Route Table for Private Subnet
private_route_table = ec2.RouteTable(
    'private-route-table',
    vpc_id=vpc.id,
    routes=[{
        'cidr_block': '0.0.0.0/0',
        'nat_gateway_id': nat_gateway.id,
    }],
    tags={
        'Name': 'private-route-table',
    }
)

# Associate the private route table with the private subnet
private_route_table_association = ec2.RouteTableAssociation(
    'private-route-table-association',
    subnet_id=private_subnet.id,
    route_table_id=private_route_table.id
)

# Security group for load balancer
alb_security_group = aws.ec2.SecurityGroup(
    "alb-sec-grp",
    vpc_id=vpc.id,
    description="Allow HTTP",
    ingress=[
        {
            "protocol": "tcp",
            "from_port":80,
            "to_port":80,
            "cidr_blocks": ["0.0.0.0/0"],
        },
    ],
    egress=[{
        "protocol": "-1",
        "from_port": 0,
        "to_port": 0,
        "cidr_blocks": ["0.0.0.0/0"],
    }],
    tags={
        'Name': 'alb-sec-grp',
    }
)

# Security Group for allowing SSH and k3s traffic
security_group = aws.ec2.SecurityGroup("k3s-instance-sec-grp",
    description='Enable SSH and K3s access',
    vpc_id=vpc.id,
    ingress=[
        {
            "protocol": "tcp",
            "from_port": 22,
            "to_port": 22,
            "cidr_blocks": ["0.0.0.0/0"],
        },
        {
            "protocol": "tcp",
            "from_port": 6443,
            "to_port": 6443,
            "cidr_blocks": ["0.0.0.0/0"],
        },
        # ALLOW ALL NODE-TO-NODE TRAFFIC
        {
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "self": True,
        },
        {
            "protocol": "tcp",
            "from_port": 30080,
            "to_port": 30080,
            "security_groups": [alb_security_group.id], # Allow the ALB specifically
            "description": "Allow traffic from ALB to NGINX NodePort",
        },
        # Allow ALB to Master Traffic
        {
            "protocol": "tcp",
            "from_port": 30090,
            "to_port": 30090,
            "security_groups": [alb_security_group.id], # Allow the ALB specifically
            "description": "Allow traffic from ALB to Master Node",
        }
    ],
    egress=[{
        "protocol": "-1",
        "from_port": 0,
        "to_port": 0,
        "cidr_blocks": ["0.0.0.0/0"],
    }],
    tags={
        'Name': 'k3s-instance-sec-grp',
    }
)


# collect the public key from gitHub workspace
public_key = os.getenv("PUBLIC_KEY")

# Create the EC2 KeyPair using the public key
key_pair = aws.ec2.KeyPair("my-key-pair",
    key_name="my-key-pair",
    public_key=public_key)

# EC2 instances
master_instance = ec2.Instance(
    'master-instance',
    instance_type=master_instance_type,
    ami=ami,
    subnet_id=private_subnet.id,
    vpc_security_group_ids=[security_group.id],
    key_name=key_pair.key_name,
    iam_instance_profile=cluster_instance_profile.name, # To access s3 bucket
    tags={
        'Name': 'Master Node',
    }
)

worker_instance_ids = []

worker_instance_1 = ec2.Instance('worker-instance-1',
    instance_type=worker_instance_type,
    ami=ami,
    subnet_id=private_subnet.id,
    vpc_security_group_ids=[security_group.id],
    key_name=key_pair.key_name,
    tags={
        'Name': 'Worker Node 1',
    }
)

worker_instance_ids.append(worker_instance_1.id)

worker_instance_2 = ec2.Instance('worker-instance-2',
    instance_type=worker_instance_type,
    ami=ami,
    subnet_id=private_subnet.id,
    vpc_security_group_ids=[security_group.id],
    key_name=key_pair.key_name,
    tags={
        'Name': 'Worker Node 2',
    }
)

worker_instance_ids.append(worker_instance_2.id)

worker_instance_3 = ec2.Instance('worker-instance-3',
    instance_type=worker_instance_type,
    ami=ami,
    subnet_id=private_subnet.id,
    vpc_security_group_ids=[security_group.id],
    key_name=key_pair.key_name,
    tags={
        'Name': 'Worker Node 3',
    }
)

worker_instance_ids.append(worker_instance_3.id)

git_runner_instance = ec2.Instance('git-runner-instance',
    instance_type=runner_instance_type,
    ami=ami,
    subnet_id=public_subnet.id,
    vpc_security_group_ids=[security_group.id],
    key_name=key_pair.key_name,
    tags={
        'Name': 'Git Runner',
    }
)

# Creating application load balancer
alb = aws.lb.LoadBalancer(
    'k3s-alb',
    internal=False,
    load_balancer_type="application",
    security_groups=[alb_security_group.id],
    subnets=[public_subnet.id, public_subnet_2.id],
    tags={"Name": "k3s-app-alb"},
)

# ALB Target Group
target_group = aws.lb.TargetGroup(
    "alb-k3s-tg",
    port=30080,           # NodePort of ingress-nginx or service
    protocol="HTTP",
    vpc_id=vpc.id,
    target_type="instance",
    health_check={
        "path": "/healthz",
        "protocol": "HTTP",
        "port": "traffic-port",
    },
    tags={"Name": "alb-k3s-tg"},
)

# ALB Listener
listener = aws.lb.Listener("http-alb-listener",
    load_balancer_arn=alb.arn,
    port=80,
    protocol="HTTP",
    default_actions=[aws.lb.ListenerDefaultActionArgs(
        type="forward",
        target_group_arn=target_group.arn,
    )],
)

# Attach Worker Nodes
for i, instance_id in enumerate(worker_instance_ids):
    aws.lb.TargetGroupAttachment(
        f"worker-{i}",
        target_group_arn=target_group.arn,
        target_id=instance_id,
        port=30080,
    )

# Create a Target Group for Prometheus
prom_target_group = aws.lb.TargetGroup("alb-prom-tg",
    port=30090, # ALB will hit the port of HELM 30090 port
    protocol="HTTP",
    vpc_id=vpc.id,
    health_check=aws.lb.TargetGroupHealthCheckArgs(
        path="/-/healthy", # Prometheus health endpoint
        port="30090",
    )
)

# Attach Master Node to this Target Group
prom_attachment = aws.lb.TargetGroupAttachment("prom-attachment",
    target_group_arn=prom_target_group.arn,
    target_id=master_instance.id,
    port=30090
)

# Add a Rule to the ALB Listener
prom_rule = aws.lb.ListenerRule("prom-rule",
    listener_arn=listener.arn,
    priority=10,
    actions=[aws.lb.ListenerRuleActionArgs(
        type="forward",
        target_group_arn=prom_target_group.arn,
    )],
    conditions=[aws.lb.ListenerRuleConditionArgs(
        path_pattern=aws.lb.ListenerRuleConditionPathPatternArgs(
            values=["/prometheus*"],
        ),
    )]
)

# Read the file from the scripts directory
script_path = os.path.join(os.getcwd(), 'scripts/join_cluster.sh')
with open(script_path, 'r') as f:
    user_data_script = f.read()


# Encoding it for the AWS Launch Template
worker_user_data_base64 = base64.b64encode(user_data_script.encode('utf-8')).decode('utf-8')

# Create a launch Template (The Blueprints for the worker nodes)
worker_launch_template = aws.ec2.LaunchTemplate(
    "worker-lt",
    image_id=ami,
    instance_type=worker_instance_type,
    key_name=key_pair.key_name,
    vpc_security_group_ids=[security_group.id], # worker security group
    iam_instance_profile={
        "name": cluster_instance_profile.name
    },
    user_data=worker_user_data_base64,
)

# Create the Auto Scaling Group
worker_asg = aws.autoscaling.Group("worker-asg",
    vpc_zone_identifiers=[private_subnet.id], # private subnets
    launch_template={
        "id": worker_launch_template.id,
        "version": "$Latest",
    },
    min_size=MIN_NODES, # Scale down to min 2 instances
    max_size=MAX_NODES, # Scale up to max 5 instances
    desired_capacity=1, # TODO: delete all worker instance and just increment it to three
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
            "PROMETHEUS_URL": f"http://{alb.dns_name}/prometheus",
            "BUCKET_NAME": bucket_name,
            "DYNAMO_TABLE": scaling_table.name,
            "ASG_NAME": worker_asg.name,
            "MIN_NODES": MIN_NODES,
            "MAX_NODES": MAX_NODES,
        }
    }
)

# Output the instance IP addresses
pulumi.export('git_runner_public_ip', git_runner_instance.public_ip)
pulumi.export('master_private_ip', master_instance.private_ip)
pulumi.export('worker1_private_ip', worker_instance_1.private_ip)
pulumi.export('worker2_private_ip', worker_instance_2.private_ip)
pulumi.export('worker3_private_ip', worker_instance_3.private_ip)
pulumi.export("alb_dns", alb.dns_name)
pulumi.export("dynamo_table", scaling_table.name)
pulumi.export("token_bucket", s3_bucket.id)