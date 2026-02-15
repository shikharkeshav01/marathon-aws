#!/usr/bin/env bash
set -euo pipefail

# Deploy script for register_user_face_handler
# Can be run standalone or called from main deploy.sh with parameters

LAMBDA_NAME="register_user_face_handler"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect if called standalone (no parameters) or from main script
if [[ $# -eq 0 ]]; then
  echo >&2 "Error: This script must be called from main deploy.sh with parameters"
  echo >&2 "Usage: deploy.sh <region> <runtime> <lambda_role_arn> <env_json>"
  exit 1
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
  aws lambda update-function-configuration --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --runtime "${RUNTIME}" --handler handler.handler --environment "${env_json}" --region "${REGION}" >/dev/null
else
  echo >&2 "Creating ${LAMBDA_NAME}..."
  aws lambda create-function --function-name "${LAMBDA_NAME}" --role "${LAMBDA_ROLE_ARN}" --runtime "${RUNTIME}" --handler handler.handler --zip-file "fileb://${ZIP_FILE}" --environment "${env_json}" --region "${REGION}" >/dev/null
fi

# Get Lambda ARN
LAMBDA_ARN=$(aws lambda get-function --function-name "${LAMBDA_NAME}" --region "${REGION}" --query 'Configuration.FunctionArn' --output text)

echo "${LAMBDA_ARN}"

