import os
import pulumi
import pulumi_aws as aws
import pulumi_aws.ec2 as ec2

# Configuration setup
config = pulumi.Config()

master_instance_type = 't3.medium'
worker_instance_type = 't3.medium'
runner_instance_type = 't3.medium'

ami = "ami-060e277c0d4cce553"

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
    subnets=[public_subnet.id],
    tags={"Name": "k3s-app-alb"},
)

# ALB Target Group
target_group = aws.lb.TargetGroup(
    "k3s-nodeport-alb-tg",
    port=30080,           # NodePort of ingress-nginx or service
    protocol="HTTP",
    vpc_id=vpc.id,
    target_type="instance",
    health_check={
        "path": "/healthz",
        "protocol": "HTTP",
        "port": "traffic-port",
    },
    tags={"Name": "k3s-nodeport-alb-tg"},
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


# Output the instance IP addresses
pulumi.export('git_runner_public_ip', git_runner_instance.public_ip)
pulumi.export('master_private_ip', master_instance.private_ip)
pulumi.export('worker1_private_ip', worker_instance_1.private_ip)
pulumi.export('worker2_private_ip', worker_instance_2.private_ip)
pulumi.export("alb_dns", alb.dns_name)