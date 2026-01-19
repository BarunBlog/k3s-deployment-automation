#!/bin/bash

set -e # Exit immediately if a command fails
exec > /var/log/k3s-install.log 2>&1 # Log everything to this file for debugging

# Install dependencies
apt-get update -y
apt-get install -y curl wget apt-transport-https ca-certificates unzip

# Install AWS CLI v2 officially
if ! command -v aws &> /dev/null; then
    echo "Installing AWS CLI..."
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
    unzip -q awscliv2.zip
    sudo ./aws/install
    rm -rf awscliv2.zip ./aws
fi

BUCKET_NAME="REPLACE_ME_BUCKET_NAME" # This name will be changed dynamically

# Wait for cluster info
while ! aws s3 ls s3://$BUCKET_NAME/cluster_info; do
  echo "Waiting for cluster info..."
  sleep 10
done

# Download and Parse
aws s3 cp s3://$BUCKET_NAME/cluster_info /tmp/cluster_info
MASTER_IP=$(cut -d'|' -f1 /tmp/cluster_info)
K3S_TOKEN=$(cut -d'|' -f2 /tmp/cluster_info)

echo "Master IP: $MASTER_IP"
echo "Joining cluster..."

# Join command with verbose flag
curl -sfL https://get.k3s.io | K3S_URL=https://${MASTER_IP}:6443 K3S_TOKEN=${K3S_TOKEN} sh -s - agent