#!/usr/bin/env bash
set -euo pipefail

aws cloudformation delete-stack --stack-name security-agent-poc-preprod || true
aws cloudformation delete-stack --stack-name security-agent-poc-prod || true

echo "Application stacks deletion requested."
echo "Artifact bucket and IAM stack are intentionally not deleted by this script."
echo "Delete them separately if they were created only for the POC."
