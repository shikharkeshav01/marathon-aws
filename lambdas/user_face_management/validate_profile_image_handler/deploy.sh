#!/usr/bin/env bash
set -euo pipefail

# Deploy script for validate_profile_image_handler
# Can be run standalone or called from main deploy.sh with parameters

LAMBDA_NAME="validate_profile_image_handler"
LAMBDA_TIMEOUT=30
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

    # Attach basic log policies
    logs_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"}]}'
    aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "logs" --policy-document "${logs_policy}" >/dev/null
  fi

  # Ensure Rekognition policy exists (full set required by all lambdas sharing this role)
  rekognition_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["rekognition:CreateCollection","rekognition:DeleteCollection","rekognition:DescribeCollection","rekognition:ListCollections","rekognition:IndexFaces","rekognition:SearchFaces","rekognition:SearchFacesByImage","rekognition:ListFaces","rekognition:DeleteFaces","rekognition:DetectFaces"],"Resource":"*"}]}'
  aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "rekognition" --policy-document "${rekognition_policy}" >/dev/null

  # Ensure S3 policy exists (full set required by all lambdas sharing this role)
  s3_policy=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":"arn:aws:s3:::marathon-photos/*"},{"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::marathon-photos"}]}
EOF
)
  aws iam put-role-policy --role-name "${LAMBDA_ROLE_NAME}" --policy-name "s3" --policy-document "${s3_policy}" >/dev/null

  LAMBDA_ROLE_ARN="$(aws iam get-role --role-name "${LAMBDA_ROLE_NAME}" --query 'Role.Arn' --output text)"
  env_json='{"Variables":{"RAW_BUCKET":"marathon-photos"}}'
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
  if command -v docker >/dev/null 2>&1; then
    docker run --rm --platform linux/amd64 \
      --entrypoint /bin/bash \
      -v "${SCRIPT_DIR}/requirements.txt:/tmp/requirements.txt:ro" \
      -v "${TMP_DIR}:/tmp/package" \
      public.ecr.aws/lambda/python:3.13 \
      -c "pip install -r /tmp/requirements.txt -t /tmp/package --no-cache-dir" >/dev/null 2>&1
  else
    echo >&2 "  Warning: Docker not found, using local pip (may cause architecture mismatch)"
    python3 -m pip install -r "${SCRIPT_DIR}/requirements.txt" -t "${TMP_DIR}" --platform manylinux2014_x86_64 --only-binary=:all: --no-cache-dir >/dev/null 2>&1 || \
    python3 -m pip install -r "${SCRIPT_DIR}/requirements.txt" -t "${TMP_DIR}" >/dev/null 2>&1
  fi
fi
ZIP_FILE="${SCRIPT_DIR}/../validate_profile_image_handler.zip"
(cd "${TMP_DIR}" && zip -r "${ZIP_FILE}" . >/dev/null)
rm -rf "${TMP_DIR}"

# Wait for Lambda to be ready
wait_for_lambda_ready() {
  local func_name="$1"
  local max_attempts=30
  local attempt=0
  while [[ $attempt -lt $max_attempts ]]; do
    if ! aws lambda get-function --function-name "${func_name}" --region "${REGION}" >/dev/null 2>&1; then
      return 0
    fi
    local state
    state=$(aws lambda get-function --function-name "${func_name}" --region "${REGION}" --query 'Configuration.State' --output text 2>/dev/null || echo "")
    local last_update_status
    last_update_status=$(aws lambda get-function --function-name "${func_name}" --region "${REGION}" --query 'Configuration.LastUpdateStatus' --output text 2>/dev/null || echo "")
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

