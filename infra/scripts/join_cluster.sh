#!/bin/bash
# Install dependencies
apt-get update -y
apt-get install -y curl wget apt-transport-https ca-certificates awscli

# Get AWS Account ID to find the bucket
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="k3s-s3-bucket-$ACCOUNT_ID"

# Wait for the Master's Ansible playbook to finish the "Dead Drop"
while ! aws s3 ls s3://$BUCKET_NAME/cluster_info; do
  echo "Waiting for cluster info..."
  sleep 10
done

# Download Master IP and Token
INFO=$(aws s3 cp s3://$BUCKET_NAME/cluster_info -)
MASTER_IP=$(echo $INFO | cut -d'|' -f1)
K3S_TOKEN=$(echo $INFO | cut -d'|' -f2)

# Join the cluster
curl -sfL https://get.k3s.io | K3S_URL=https://$MASTER_IP:6443 K3S_TOKEN=$K3S_TOKEN sh -