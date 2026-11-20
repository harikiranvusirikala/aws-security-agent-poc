#!/usr/bin/env bash
set -euo pipefail

: "${ARTIFACT_BUCKET:?Set ARTIFACT_BUCKET}"
: "${HOSTED_ZONE_ID:?Set HOSTED_ZONE_ID}"
: "${DOMAIN_NAME:?Set DOMAIN_NAME}"
: "${ENVIRONMENT:?Set ENVIRONMENT}"

./scripts/package.sh

KEY="releases/${CI_COMMIT_SHA:-local}/app.zip"
aws s3 cp build/app.zip "s3://${ARTIFACT_BUCKET}/${KEY}"

aws cloudformation deploy   --template-file cloudformation/01-application.yaml   --stack-name "security-agent-poc-${ENVIRONMENT}"   --parameter-overrides     ArtifactBucketName="${ARTIFACT_BUCKET}"     ArtifactKey="${KEY}"     HostedZoneId="${HOSTED_ZONE_ID}"     DomainName="${DOMAIN_NAME}"     Environment="${ENVIRONMENT}"   --capabilities CAPABILITY_NAMED_IAM
