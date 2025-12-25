#!/usr/bin/env bash
set -euo pipefail

# Deploy script for extract_bib_number_handler (container image)
# Can be run standalone or called from main deploy.sh with parameters

LAMBDA_NAME="extract_bib_number_handler"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP_TAG="$(date +%Y%m%d%H%M%S)"

# Set Lambda timeout to 5 minutes (300 seconds)
LAMBDA_TIMEOUT=300

# Check if Dockerfile exists
if [[ ! -f "${SCRIPT_DIR}/Dockerfile" ]]; then
  echo >&2 "Error: Dockerfile not found for ${LAMBDA_NAME}" >&2
  exit 1
fi

# Detect if called standalone (no parameters) or from main script
if [[ $# -eq 0 ]]; then
  # Standalone mode - set up everything
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
  
  # Ensure IAM role exists
  LAMBDA_ROLE_NAME="lambda-role"
  if ! aws iam get-role --role-name "${LAMBDA_ROLE_NAME}" >/dev/null 2>&1; then
    echo >&2 "Creating IAM role ${LAMBDA_ROLE_NAME}..."
    lambda_assume='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    aws iam create-role --role-name "${LAMBDA_ROLE_NAME}" --assume-role-policy-document "${lambda_assume}" >/dev/null
    
    # Attach basic policies
    logs_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"}]}'
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "logs" --policy-document "${logs_policy}" >/dev/null
    
    # Ensure DynamoDB tables exist and attach DDB policy
    requests_table="EventRequests"
    images_table="EventImages"
    reels_table="EventReels"
    
    if ! aws dynamodb describe-table --table-name "${requests_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${requests_table}..."
      aws dynamodb create-table --table-name "${requests_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=RequestId,AttributeType=S" "AttributeName=EventId,AttributeType=N" \
        --key-schema "AttributeName=RequestId,KeyType=HASH" \
        --global-secondary-indexes "[{\"IndexName\":\"EventId-index\",\"KeySchema\":[{\"AttributeName\":\"EventId\",\"KeyType\":\"HASH\"}],\"Projection\":{\"ProjectionType\":\"ALL\"}}]" \
        --region "${REGION}" >/dev/null
    fi
    
    if ! aws dynamodb describe-table --table-name "${images_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${images_table}..."
      aws dynamodb create-table --table-name "${images_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=Id,AttributeType=S" \
        --key-schema "AttributeName=Id,KeyType=HASH" \
        --region "${REGION}" >/dev/null
    fi
    
    if ! aws dynamodb describe-table --table-name "${reels_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${reels_table}..."
      aws dynamodb create-table --table-name "${reels_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=EventId,AttributeType=N" "AttributeName=BibId,AttributeType=S" \
        --key-schema "AttributeName=EventId,KeyType=HASH" "AttributeName=BibId,KeyType=RANGE" \
        --region "${REGION}" >/dev/null
    fi
    
    requests_arn="$(aws dynamodb describe-table --table-name "${requests_table}" --region "${REGION}" --query "Table.TableArn" --output text)"
    images_arn="$(aws dynamodb describe-table --table-name "${images_table}" --region "${REGION}" --query "Table.TableArn" --output text)"
    reels_arn="$(aws dynamodb describe-table --table-name "${reels_table}" --region "${REGION}" --query "Table.TableArn" --output text)"
    
    ddb_policy=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:Query","dynamodb:Scan"],"Resource":["${requests_arn}","${images_arn}","${reels_arn}"]}]}
EOF
)
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "ddb" --policy-document "${ddb_policy}" >/dev/null
    
    # Add SSM read permissions for service account
    ssm_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ssm:GetParameter","ssm:GetParameters"],"Resource":"arn:aws:ssm:*:*:parameter/google-service-account"}]}'
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "ssm" --policy-document "${ssm_policy}" >/dev/null

    s3_policy=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":"arn:aws:s3:::marathon-photos/*"},{"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::marathon-photos"}]}
EOF
)
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "s3" --policy-document "${s3_policy}" >/dev/null
  else
    # Role exists, ensure tables exist
    requests_table="EventRequests"
    images_table="EventImages"
    reels_table="EventReels"
    
    if ! aws dynamodb describe-table --table-name "${requests_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${requests_table}..."
      aws dynamodb create-table --table-name "${requests_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=RequestId,AttributeType=S" "AttributeName=EventId,AttributeType=N" \
        --key-schema "AttributeName=RequestId,KeyType=HASH" \
        --global-secondary-indexes "[{\"IndexName\":\"EventId-index\",\"KeySchema\":[{\"AttributeName\":\"EventId\",\"KeyType\":\"HASH\"}],\"Projection\":{\"ProjectionType\":\"ALL\"}}]" \
        --region "${REGION}" >/dev/null
    fi
    
    if ! aws dynamodb describe-table --table-name "${images_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${images_table}..."
      aws dynamodb create-table --table-name "${images_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=Id,AttributeType=S" \
        --key-schema "AttributeName=Id,KeyType=HASH" \
        --region "${REGION}" >/dev/null
    fi
    
    if ! aws dynamodb describe-table --table-name "${reels_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${reels_table}..."
      aws dynamodb create-table --table-name "${reels_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=EventId,AttributeType=N" "AttributeName=BibId,AttributeType=S" \
        --key-schema "AttributeName=EventId,KeyType=HASH" "AttributeName=BibId,KeyType=RANGE" \
        --region "${REGION}" >/dev/null
    fi
    
    # Ensure SSM policy exists
    ssm_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ssm:GetParameter","ssm:GetParameters"],"Resource":"arn:aws:ssm:*:*:parameter/google-service-account"}]}'
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "ssm" --policy-document "${ssm_policy}" >/dev/null

    s3_policy=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":"arn:aws:s3:::marathon-photos/*"},{"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::marathon-photos"}]}
EOF
)
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "s3" --policy-document "${s3_policy}" >/dev/null
  fi
  
  LAMBDA_ROLE_ARN="$(aws iam get-role --role-name "${LAMBDA_ROLE_NAME}" --query 'Role.Arn' --output text)"
  requests_table="EventRequests"
  images_table="EventImages"
  reels_table="EventReels"
  env_json=$(cat <<EOF
{"Variables":{"RAW_BUCKET":"marathon-photos","EVENT_REQUESTS_TABLE":"${requests_table}","EVENT_IMAGES_TABLE":"${images_table}","EVENT_REELS_TABLE":"${reels_table}","GDRIVE_SA_SSM_PARAM":"google-service-account"}}
EOF
)
else
  # Called from main script with parameters
  REGION="${1}"
  LAMBDA_ROLE_ARN="${2}"
  env_json="${3}"
fi

# Ensure ECR repo exists
REPO_NAME="extract_bib_number_handler"
REPO_URI=$(aws ecr describe-repositories --repository-names "${REPO_NAME}" --region "${REGION}" --query 'repositories[0].repositoryUri' --output text 2>/dev/null || echo "")
if [[ -z "${REPO_URI}" || "${REPO_URI}" == "None" || "${REPO_URI}" == "null" ]]; then
  echo >&2 "Creating ECR repository ${REPO_NAME}..."
  REPO_URI=$(aws ecr create-repository --repository-name "${REPO_NAME}" --region "${REGION}" --query 'repositories[0].repositoryUri' --output text)
  if [[ -z "${REPO_URI}" || "${REPO_URI}" == "None" || "${REPO_URI}" == "null" ]]; then
    echo >&2 "Error: Failed to get ECR repository URI" >&2
    exit 1
  fi
fi

# Authenticate Docker to ECR
echo >&2 "Authenticating Docker to ECR..."
ECR_REGISTRY="${REPO_URI%/*}"
if [[ -z "${ECR_REGISTRY}" || "${ECR_REGISTRY}" == "None" ]]; then
  echo >&2 "Error: Invalid ECR registry URI: ${REPO_URI}" >&2
  exit 1
fi
aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${ECR_REGISTRY}" >/dev/null

# Build and push image
IMAGE_TAG="${REPO_URI}:${TIMESTAMP_TAG}"
IMAGE_LATEST="${REPO_URI}:latest"

echo >&2 "Building Docker image for ${LAMBDA_NAME}..."
# Delete existing tags if they exist (to remove any manifest lists)
aws ecr batch-delete-image --repository-name "${REPO_NAME}" --image-ids imageTag=latest imageTag="${TIMESTAMP_TAG}" --region "${REGION}" >/dev/null 2>&1 || true

# Build and push directly for linux/arm64 platform (matches Dockerfile base image)
# Lambda requires single architecture image manifest, not a manifest list/index
# Use buildx with --provenance=false --sbom=false to avoid creating attestations and manifest lists
if docker buildx version >/dev/null 2>&1; then
  echo >&2 "Pushing image to ECR using buildx..."
  # Create builder if it doesn't exist
  docker buildx create --name lambda-builder --use >/dev/null 2>&1 || docker buildx use lambda-builder >/dev/null 2>&1 || true
  # Build and push with flags to prevent manifest list creation
  docker buildx build --platform linux/arm64 --provenance=false --sbom=false --push -t "${IMAGE_TAG}" "${SCRIPT_DIR}" >/dev/null
  docker buildx build --platform linux/arm64 --provenance=false --sbom=false --push -t "${IMAGE_LATEST}" "${SCRIPT_DIR}" >/dev/null
else
  # Fallback: build locally then push
  docker build --platform linux/arm64 -t "${IMAGE_TAG}" -t "${IMAGE_LATEST}" "${SCRIPT_DIR}" >/dev/null
  echo >&2 "Pushing image to ECR..."
  docker push "${IMAGE_TAG}" >/dev/null
  docker push "${IMAGE_LATEST}" >/dev/null
fi

# Wait for Lambda to be ready
wait_for_lambda_ready() {
  local func_name="$1"
  local max_attempts=30
  local attempt=0
  while [[ $attempt -lt $max_attempts ]]; do
    # Check if function exists
    if ! aws lambda get-function --function-name "${func_name}" --region "${REGION}" >/dev/null 2>&1; then
      # Function doesn't exist yet, that's okay
      return 0
    fi
    
    # Check both State and LastUpdateStatus
    local state=$(aws lambda get-function --function-name "${func_name}" --region "${REGION}" --query 'Configuration.State' --output text 2>/dev/null || echo "")
    local last_update_status=$(aws lambda get-function --function-name "${func_name}" --region "${REGION}" --query 'Configuration.LastUpdateStatus' --output text 2>/dev/null || echo "")
    
    # Only proceed when State is Active AND LastUpdateStatus is not InProgress
    if [[ "${state}" == "Active" ]] && [[ "${last_update_status}" != "InProgress" ]]; then
      return 0
    fi
    
    sleep 1
    attempt=$((attempt + 1))
  done
  echo >&2 "Warning: Lambda may still be updating, proceeding anyway..."
}

# Deploy Lambda
if aws lambda get-function --function-name "${LAMBDA_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  # Check current architecture
  current_arch=$(aws lambda get-function-configuration --function-name "${LAMBDA_NAME}" --region "${REGION}" --query 'Architectures[0]' --output text 2>/dev/null || echo "x86_64")
  
  if [[ "${current_arch}" != "arm64" ]]; then
    echo >&2 "Warning: ${LAMBDA_NAME} is currently ${current_arch}, but needs to be arm64."
    echo >&2 "Architecture cannot be changed after creation. Deleting and recreating function..."
    aws lambda delete-function --function-name "${LAMBDA_NAME}" --region "${REGION}" >/dev/null
    # Wait a bit for deletion to complete
    sleep 5
    echo >&2 "Creating ${LAMBDA_NAME} with arm64 architecture..."
    aws lambda create-function --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --package-type=Image --code "ImageUri=${IMAGE_LATEST}" --architectures arm64 --environment "${env_json}" --timeout "${LAMBDA_TIMEOUT}" --region "${REGION}" >/dev/null
  else
    echo >&2 "Updating ${LAMBDA_NAME}..."
    wait_for_lambda_ready "${LAMBDA_NAME}"
    aws lambda update-function-code --function-name "${LAMBDA_NAME}" --image-uri "${IMAGE_LATEST}" --region "${REGION}" >/dev/null
    wait_for_lambda_ready "${LAMBDA_NAME}"
    # package-type and architecture cannot be changed after creation, so omit them from update
    # Note: Handler is specified in Dockerfile CMD, not in Lambda config for container images
    aws lambda update-function-configuration --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --environment "${env_json}" --timeout "${LAMBDA_TIMEOUT}" --region "${REGION}" >/dev/null
  fi
else
  echo >&2 "Creating ${LAMBDA_NAME}..."
  # Note: Handler is specified in Dockerfile CMD, not in Lambda config for container images
  # Architecture must match the Docker image (arm64)
  aws lambda create-function --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --package-type=Image --code "ImageUri=${IMAGE_LATEST}" --architectures arm64 --environment "${env_json}" --timeout "${LAMBDA_TIMEOUT}" --region "${REGION}" >/dev/null
fi

# Get Lambda ARN
LAMBDA_ARN=$(aws lambda get-function --function-name "${LAMBDA_NAME}" --region "${REGION}" --query 'Configuration.FunctionArn' --output text)

# Ensure CloudWatch log group exists
LOG_GROUP_NAME="/aws/lambda/${LAMBDA_NAME}"
if ! aws logs describe-log-groups --log-group-name-prefix "${LOG_GROUP_NAME}" --region "${REGION}" --query "logGroups[?logGroupName=='${LOG_GROUP_NAME}'].logGroupName | [0]" --output text 2>/dev/null | grep -q "^${LOG_GROUP_NAME}$"; then
  echo >&2 "Creating log group ${LOG_GROUP_NAME}..."
  aws logs create-log-group --log-group-name "${LOG_GROUP_NAME}" --region "${REGION}" >/dev/null 2>&1 || true
fi

echo "${LAMBDA_ARN}"
