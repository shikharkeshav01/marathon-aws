#!/usr/bin/env bash
set -euo pipefail

# Deploy script for register_user_face_handler
# Can be run standalone or called from main deploy.sh with parameters

LAMBDA_NAME="register_user_face_handler"
LAMBDA_TIMEOUT=60
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
    user_faces_table="UserFaces"
    user_image_matches_table="UserImageMatches"
    indexed_faces_table="IndexedFaces"
    user_table="User"

    # Create UserFaces table if needed
    if ! aws dynamodb describe-table --table-name "${user_faces_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${user_faces_table}..."
      aws dynamodb create-table --table-name "${user_faces_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=Email,AttributeType=S" "AttributeName=EventId,AttributeType=N" \
        --key-schema "AttributeName=Email,KeyType=HASH" "AttributeName=EventId,KeyType=RANGE" \
        --region "${REGION}" >/dev/null
    fi

    # Create UserImageMatches table if needed
    if ! aws dynamodb describe-table --table-name "${user_image_matches_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${user_image_matches_table}..."
      aws dynamodb create-table --table-name "${user_image_matches_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=Email,AttributeType=S" "AttributeName=ImageS3Key,AttributeType=S" \
        --key-schema "AttributeName=Email,KeyType=HASH" "AttributeName=ImageS3Key,KeyType=RANGE" \
        --region "${REGION}" >/dev/null
    fi

    # Create IndexedFaces table if needed
    if ! aws dynamodb describe-table --table-name "${indexed_faces_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${indexed_faces_table}..."
      aws dynamodb create-table --table-name "${indexed_faces_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=FaceId,AttributeType=S" \
        --key-schema "AttributeName=FaceId,KeyType=HASH" \
        --region "${REGION}" >/dev/null
    fi

    # Create User table if needed
    if ! aws dynamodb describe-table --table-name "${user_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${user_table}..."
      aws dynamodb create-table --table-name "${user_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=Email,AttributeType=S" \
        --key-schema "AttributeName=Email,KeyType=HASH" \
        --region "${REGION}" >/dev/null
    fi

    user_faces_arn="$(aws dynamodb describe-table --table-name "${user_faces_table}" --region "${REGION}" --query "Table.TableArn" --output text)"
    user_image_matches_arn="$(aws dynamodb describe-table --table-name "${user_image_matches_table}" --region "${REGION}" --query "Table.TableArn" --output text)"
    indexed_faces_arn="$(aws dynamodb describe-table --table-name "${indexed_faces_table}" --region "${REGION}" --query "Table.TableArn" --output text)"
    user_arn="$(aws dynamodb describe-table --table-name "${user_table}" --region "${REGION}" --query "Table.TableArn" --output text)"

    ddb_policy=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:Query","dynamodb:Scan"],"Resource":["${user_faces_arn}","${user_faces_arn}/index/*","${user_image_matches_arn}","${user_image_matches_arn}/index/*","${indexed_faces_arn}","${indexed_faces_arn}/index/*","${user_arn}","${user_arn}/index/*"]}]}
EOF
)
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "ddb" --policy-document "${ddb_policy}" >/dev/null

    # Add Rekognition permissions
    rekognition_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["rekognition:*"],"Resource":"*"}]}'
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "rekognition" --policy-document "${rekognition_policy}" >/dev/null

    # Add S3 permissions
    s3_policy=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":"arn:aws:s3:::marathon-photos/*"},{"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::marathon-photos"}]}
EOF
)
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "s3" --policy-document "${s3_policy}" >/dev/null
  else
    # Role exists, ensure tables exist
    user_faces_table="UserFaces"
    user_image_matches_table="UserImageMatches"
    indexed_faces_table="IndexedFaces"
    user_table="User"

    # Create tables if they don't exist (same as above)
    if ! aws dynamodb describe-table --table-name "${user_faces_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${user_faces_table}..."
      aws dynamodb create-table --table-name "${user_faces_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=Email,AttributeType=S" "AttributeName=EventId,AttributeType=N" \
        --key-schema "AttributeName=Email,KeyType=HASH" "AttributeName=EventId,KeyType=RANGE" \
        --region "${REGION}" >/dev/null
    fi

    if ! aws dynamodb describe-table --table-name "${user_image_matches_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${user_image_matches_table}..."
      aws dynamodb create-table --table-name "${user_image_matches_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=Email,AttributeType=S" "AttributeName=ImageS3Key,AttributeType=S" \
        --key-schema "AttributeName=Email,KeyType=HASH" "AttributeName=ImageS3Key,KeyType=RANGE" \
        --region "${REGION}" >/dev/null
    fi

    if ! aws dynamodb describe-table --table-name "${indexed_faces_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${indexed_faces_table}..."
      aws dynamodb create-table --table-name "${indexed_faces_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=FaceId,AttributeType=S" \
        --key-schema "AttributeName=FaceId,KeyType=HASH" \
        --region "${REGION}" >/dev/null
    fi

    if ! aws dynamodb describe-table --table-name "${user_table}" --region "${REGION}" >/dev/null 2>&1; then
      echo >&2 "Creating DynamoDB table ${user_table}..."
      aws dynamodb create-table --table-name "${user_table}" --billing-mode PAY_PER_REQUEST \
        --attribute-definitions "AttributeName=Email,AttributeType=S" \
        --key-schema "AttributeName=Email,KeyType=HASH" \
        --region "${REGION}" >/dev/null
    fi

    # Ensure Rekognition policy exists
    rekognition_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["rekognition:*"],"Resource":"*"}]}'
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "rekognition" --policy-document "${rekognition_policy}" >/dev/null

    # Ensure S3 policy exists
    s3_policy=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":"arn:aws:s3:::marathon-photos/*"},{"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::marathon-photos"}]}
EOF
)
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "s3" --policy-document "${s3_policy}" >/dev/null
  fi

  LAMBDA_ROLE_ARN="$(aws iam get-role --role-name "${LAMBDA_ROLE_NAME}" --query 'Role.Arn' --output text)"
  user_faces_table="UserFaces"
  user_image_matches_table="UserImageMatches"
  indexed_faces_table="IndexedFaces"
  user_table="User"
  env_json=$(cat <<EOF
{"Variables":{"RAW_BUCKET":"marathon-photos","USER_FACES_TABLE":"${user_faces_table}","USER_IMAGE_MATCHES_TABLE":"${user_image_matches_table}","INDEXED_FACES_TABLE":"${indexed_faces_table}","USER_TABLE":"${user_table}","REKOGNITION_COLLECTION_ID":"marathon-participants"}}
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
  echo >&2 "  Installing dependencies for x86_64 architecture..."
  # Use Docker to install dependencies for the correct architecture
  if command -v docker >/dev/null 2>&1; then
    docker run --rm --platform linux/amd64 \
      --entrypoint /bin/bash \
      -v "${SCRIPT_DIR}/requirements.txt:/tmp/requirements.txt:ro" \
      -v "${TMP_DIR}:/tmp/package" \
      public.ecr.aws/lambda/python:3.13 \
      -c "pip install -r /tmp/requirements.txt -t /tmp/package --no-cache-dir" >/dev/null 2>&1
  else
    # Fallback to local pip (may have architecture issues on arm64 Macs)
    echo >&2 "  Warning: Docker not found, using local pip (may cause architecture mismatch)"
    python3 -m pip install -r "${SCRIPT_DIR}/requirements.txt" -t "${TMP_DIR}" --platform manylinux2014_x86_64 --only-binary=:all: --no-cache-dir >/dev/null 2>&1 || \
    python3 -m pip install -r "${SCRIPT_DIR}/requirements.txt" -t "${TMP_DIR}" >/dev/null 2>&1
  fi
fi
ZIP_FILE="${SCRIPT_DIR}/../register_user_face_handler.zip"
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
  # Update the code
  aws lambda update-function-code --function-name "${LAMBDA_NAME}" --zip-file "fileb://${ZIP_FILE}" --region "${REGION}" >/dev/null
  wait_for_lambda_ready "${LAMBDA_NAME}"
  # Update configuration
  aws lambda update-function-configuration --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --runtime "${RUNTIME}" --handler handler.handler --environment "${env_json}" --timeout "${LAMBDA_TIMEOUT}" --region "${REGION}" >/dev/null
else
  echo >&2 "Creating ${LAMBDA_NAME}..."
  aws lambda create-function --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --runtime "${RUNTIME}" --handler handler.handler --zip-file "fileb://${ZIP_FILE}" --environment "${env_json}" --timeout "${LAMBDA_TIMEOUT}" --region "${REGION}" >/dev/null
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

