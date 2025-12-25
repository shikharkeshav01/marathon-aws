#!/usr/bin/env bash
set -euo pipefail

# Deploy script for reel_generation_completion_handler
# Can be run standalone or called from main deploy.sh with parameters

LAMBDA_NAME="reel_generation_completion_handler"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect if called standalone (no parameters) or from main script
if [[ $# -eq 0 ]]; then
  # Standalone mode - set up everything
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
  RUNTIME="${LAMBDA_RUNTIME:-python3.13}"
  
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
  RUNTIME="${2}"
  LAMBDA_ROLE_ARN="${3}"
  env_json="${4}"
fi

# Package Lambda zip
echo >&2 "Packaging ${LAMBDA_NAME}..."
TMP_DIR=$(mktemp -d)
# Copy all files except deploy.sh
rsync -av --exclude='deploy.sh' "${SCRIPT_DIR}/" "${TMP_DIR}/" >/dev/null 2>&1 || {
  # Fallback if rsync not available
  find "${SCRIPT_DIR}" -mindepth 1 -maxdepth 1 ! -name 'deploy.sh' -exec cp -R {} "${TMP_DIR}/" \;
}
if [[ -f "${SCRIPT_DIR}/requirements.txt" ]]; then
  echo >&2 "  Installing dependencies..."
  python3 -m pip install -r "${SCRIPT_DIR}/requirements.txt" -t "${TMP_DIR}" >/dev/null 2>&1
fi
ZIP_FILE="${SCRIPT_DIR}/../reel_generation_completion_handler.zip"
(cd "${TMP_DIR}" && zip -r "${ZIP_FILE}" . >/dev/null)
rm -rf "${TMP_DIR}"

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
  echo >&2 "Updating ${LAMBDA_NAME}..."
  wait_for_lambda_ready "${LAMBDA_NAME}"
  aws lambda update-function-code --function-name "${LAMBDA_NAME}" --zip-file "fileb://${ZIP_FILE}" --region "${REGION}" >/dev/null
  wait_for_lambda_ready "${LAMBDA_NAME}"
  aws lambda update-function-configuration --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --runtime "${RUNTIME}" --handler handler.main --environment "${env_json}" --region "${REGION}" >/dev/null
else
  echo >&2 "Creating ${LAMBDA_NAME}..."
  aws lambda create-function --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --runtime "${RUNTIME}" --handler handler.main --zip-file "fileb://${ZIP_FILE}" --environment "${env_json}" --region "${REGION}" >/dev/null
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
