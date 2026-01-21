import os
import json
import pulumi
import pulumi_aws as aws
import pulumi_aws.ec2 as ec2

config = pulumi.Config()

# Creating an s3 bucket
s3_bucket = aws.s3.Bucket("k3s-storage-bucket")

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
    policy=s3_bucket.arn.apply(lambda arn: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"{arn}",      # The bucket itself
                    f"{arn}/*"     # The objects inside
                ]
            }
        ]
    }))
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


pulumi.export('s3_bucket_id', s3_bucket.id)
pulumi.export('vpc_id', vpc.id)

pulumi.export('public_subnet_id', public_subnet.id)
pulumi.export('public_subnet_2_id', public_subnet_2.id)
pulumi.export('private_subnet_id', private_subnet.id)

pulumi.export('security_group_id', security_group.id)
pulumi.export('alb_security_group_id', alb_security_group.id)
pulumi.export("cluster_node_role_name", cluster_node_role.name)
pulumi.export('cluster_instance_profile_name', cluster_instance_profile.name)

pulumi.export('key_pair_key_name', key_pair.key_name)