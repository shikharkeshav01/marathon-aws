#!/usr/bin/env bash
set -euo pipefail

# Deploy script for list_images_handler
# Can be run standalone or called from main deploy.sh with parameters

LAMBDA_NAME="list_images_handler"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect if called standalone (no parameters) or from main script
if [[ $# -eq 0 ]]; then
  # Standalone mode - set up everything
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
  RUNTIME="${LAMBDA_RUNTIME:-python3.13}"
  
  # Ensure IAM role exists
  LAMBDA_ROLE_NAME="lambda-role"
  if ! aws iam get-role --role-name "${LAMBDA_ROLE_NAME}" >/dev/null 2>&1; then
    echo "Creating IAM role ${LAMBDA_ROLE_NAME}..."
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
      echo "Creating DynamoDB table ${requests_table}..."
      aws dynamodb create-table --table-name "${requests_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=requestId,AttributeType=S" "AttributeName=eventId,AttributeType=N" \
        --key-schema "AttributeName=requestId,KeyType=HASH" \
        --global-secondary-indexes "[{\"IndexName\":\"eventId-index\",\"KeySchema\":[{\"AttributeName\":\"eventId\",\"KeyType\":\"HASH\"}],\"Projection\":{\"ProjectionType\":\"ALL\"}}]" \
        --region "${REGION}" >/dev/null
    fi
    
    if ! aws dynamodb describe-table --table-name "${images_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo "Creating DynamoDB table ${images_table}..."
      aws dynamodb create-table --table-name "${images_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=id,AttributeType=S" \
        --key-schema "AttributeName=id,KeyType=HASH" \
        --region "${REGION}" >/dev/null
    fi
    
    if ! aws dynamodb describe-table --table-name "${reels_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo "Creating DynamoDB table ${reels_table}..."
      aws dynamodb create-table --table-name "${reels_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=eventId,AttributeType=N" "AttributeName=bibId,AttributeType=S" \
        --key-schema "AttributeName=eventId,KeyType=HASH" "AttributeName=bibId,KeyType=RANGE" \
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
  else
    # Role exists, ensure tables exist
    requests_table="EventRequests"
    images_table="EventImages"
    reels_table="EventReels"
    
    if ! aws dynamodb describe-table --table-name "${requests_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo "Creating DynamoDB table ${requests_table}..."
      aws dynamodb create-table --table-name "${requests_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=requestId,AttributeType=S" "AttributeName=eventId,AttributeType=N" \
        --key-schema "AttributeName=requestId,KeyType=HASH" \
        --global-secondary-indexes "[{\"IndexName\":\"eventId-index\",\"KeySchema\":[{\"AttributeName\":\"eventId\",\"KeyType\":\"HASH\"}],\"Projection\":{\"ProjectionType\":\"ALL\"}}]" \
        --region "${REGION}" >/dev/null
    fi
    
    if ! aws dynamodb describe-table --table-name "${images_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo "Creating DynamoDB table ${images_table}..."
      aws dynamodb create-table --table-name "${images_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=id,AttributeType=S" \
        --key-schema "AttributeName=id,KeyType=HASH" \
        --region "${REGION}" >/dev/null
    fi
    
    if ! aws dynamodb describe-table --table-name "${reels_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo "Creating DynamoDB table ${reels_table}..."
      aws dynamodb create-table --table-name "${reels_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=eventId,AttributeType=N" "AttributeName=bibId,AttributeType=S" \
        --key-schema "AttributeName=eventId,KeyType=HASH" "AttributeName=bibId,KeyType=RANGE" \
        --region "${REGION}" >/dev/null
    fi
  fi
  
  LAMBDA_ROLE_ARN="$(aws iam get-role --role-name "${LAMBDA_ROLE_NAME}" --query 'Role.Arn' --output text)"
  requests_table="EventRequests"
  images_table="EventImages"
  reels_table="EventReels"
  env_json=$(cat <<EOF
{"Variables":{"EVENT_REQUESTS_TABLE":"${requests_table}","EVENT_IMAGES_TABLE":"${images_table}","EVENT_REELS_TABLE":"${reels_table}"}}
EOF
)
else
  # Called from main script with parameters
  REGION="${1}"
  RUNTIME="${2}"
  LAMBDA_ROLE_ARN="${3}"
  env_json="${4}"
fi

# Package Lambda zip
echo "Packaging ${LAMBDA_NAME}..."
TMP_DIR=$(mktemp -d)
cp -R "${SCRIPT_DIR}/." "${TMP_DIR}/"
if [[ -f "${SCRIPT_DIR}/requirements.txt" ]]; then
  echo "  Installing dependencies..."
  python3 -m pip install -r "${SCRIPT_DIR}/requirements.txt" -t "${TMP_DIR}" >/dev/null 2>&1
fi
ZIP_FILE="${SCRIPT_DIR}/../list_images_handler.zip"
(cd "${TMP_DIR}" && zip -r "${ZIP_FILE}" . >/dev/null)
rm -rf "${TMP_DIR}"

# Deploy Lambda
if aws lambda get-function --function-name "${LAMBDA_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  echo "Updating ${LAMBDA_NAME}..."
  aws lambda update-function-code --function-name "${LAMBDA_NAME}" --zip-file "fileb://${ZIP_FILE}" --region "${REGION}" >/dev/null
  aws lambda update-function-configuration --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --runtime "${RUNTIME}" --handler handler.handler --environment "${env_json}" --region "${REGION}" >/dev/null
else
  echo "Creating ${LAMBDA_NAME}..."
  aws lambda create-function --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --runtime "${RUNTIME}" --handler handler.handler --zip-file "fileb://${ZIP_FILE}" --environment "${env_json}" --region "${REGION}" >/dev/null
fi

# Get Lambda ARN
LAMBDA_ARN=$(aws lambda get-function --function-name "${LAMBDA_NAME}" --region "${REGION}" --query 'Configuration.FunctionArn' --output text)
echo "${LAMBDA_ARN}"
