#!/bin/bash
# LocalStack init for Project 2 — Self-Healing Infrastructure
# Creates EC2 instances, CloudWatch alarms, and SNS topic

echo "=== Project 2 LocalStack Init ==="
sleep 3

export AWS_DEFAULT_REGION=us-east-1
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_ENDPOINT_URL=http://localhost:4566

echo "Creating EC2 key pair..."
awslocal ec2 create-key-pair --key-name aiops-key 2>/dev/null || true

echo "Launching EC2 instances..."
AUTH_ID=$(awslocal ec2 run-instances \
  --image-id ami-0abcdef1234567890 \
  --instance-type t2.micro --count 1 \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=auth-svc},{Key=Service,Value=auth-service}]' \
  --query 'Instances[0].InstanceId' --output text)
echo "auth-svc: $AUTH_ID"

PAYMENT_ID=$(awslocal ec2 run-instances \
  --image-id ami-0abcdef1234567890 \
  --instance-type t2.micro --count 1 \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=payment-svc},{Key=Service,Value=payment-service}]' \
  --query 'Instances[0].InstanceId' --output text)
echo "payment-svc: $PAYMENT_ID"

INVENTORY_ID=$(awslocal ec2 run-instances \
  --image-id ami-0abcdef1234567890 \
  --instance-type t2.micro --count 1 \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=inventory-svc},{Key=Service,Value=inventory-service}]' \
  --query 'Instances[0].InstanceId' --output text)
echo "inventory-svc: $INVENTORY_ID"

echo "Creating SNS topic and CloudWatch alarms..."
awslocal sns create-topic --name aiops-alerts 2>/dev/null || true
awslocal cloudwatch put-metric-alarm \
  --alarm-name "auth-svc-high-latency" \
  --metric-name "Latency" --namespace "AIOps/Services" \
  --period 60 --threshold 1000 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --alarm-actions "arn:aws:sns:us-east-1:000000000000:aiops-alerts" 2>/dev/null || true

echo "=== Project 2 Init Complete ==="
awslocal ec2 describe-instances \
  --filters "Name=tag:Name,Values=auth-svc,payment-svc,inventory-svc" \
  --query 'Reservations[*].Instances[*].[InstanceId,State.Name,Tags[?Key==`Name`].Value|[0]]' \
  --output table
