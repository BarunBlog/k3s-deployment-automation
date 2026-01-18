#!/bin/bash
# Install dependencies
apt-get update -y
apt-get install -y curl wget apt-transport-https ca-certificates awscli

# Wait for the Master install aws cli and to upload info
while ! /usr/local/bin/aws s3 ls s3://$BUCKET_NAME/cluster_info; do
  echo "Waiting for cluster info in $BUCKET_NAME..."
  sleep 10
done

# Download Info
INFO=$(/usr/local/bin/aws s3 cp s3://$BUCKET_NAME/cluster_info -)
MASTER_IP=$(echo $INFO | cut -d'|' -f1)
K3S_TOKEN=$(echo $INFO | cut -d'|' -f2)

# Join the cluster
curl -sfL https://get.k3s.io | K3S_URL=https://$MASTER_IP:6443 K3S_TOKEN=$K3S_TOKEN sh -