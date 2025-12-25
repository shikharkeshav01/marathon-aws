#!/usr/bin/env bash
set -euo pipefail

# Deploy script for process_images_state_machine
# Usage: deploy.sh [region] [sfn_role_arn] [list_images_handler_arn] [extract_bib_number_handler_arn] [image_processing_completion_handler_arn]
# If called without parameters, runs in standalone mode

STATE_MACHINE_NAME="process-images-state-machine"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFINITION_FILE="${SCRIPT_DIR}/definition.json"

# Detect if called standalone (no parameters) or from main script
if [[ $# -eq 0 ]]; then
  # Standalone mode
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
  
  # Ensure IAM role exists
  SFN_ROLE_NAME="sfn-role"
  if ! aws iam get-role --role-name "${SFN_ROLE_NAME}" >/dev/null 2>&1; then
    echo >&2 "Creating IAM role ${SFN_ROLE_NAME}..."
    sfn_assume='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"states.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
    aws iam create-role --role-name "${SFN_ROLE_NAME}" --assume-role-policy-document "${sfn_assume}" >/dev/null
    
    # Attach invoke policy
    invoke_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["lambda:InvokeFunction"],"Resource":"*"}]}'
    aws iam put-role-policy --role-name "${SFN_ROLE_NAME}" --policy-name "invoke" --policy-document "${invoke_policy}" >/dev/null
  fi
  
  SFN_ROLE_ARN="$(aws iam get-role --role-name "${SFN_ROLE_NAME}" --query 'Role.Arn' --output text)"
  
  # Get Lambda ARNs (assuming they exist)
  echo >&2 "Looking up Lambda function ARNs..."
  LIST_IMAGES_HANDLER_ARN="$(aws lambda get-function --function-name list_images_handler --region "${REGION}" --query 'Configuration.FunctionArn' --output text 2>/dev/null || echo "")"
  EXTRACT_BIB_NUMBER_HANDLER_ARN="$(aws lambda get-function --function-name extract_bib_number_handler --region "${REGION}" --query 'Configuration.FunctionArn' --output text 2>/dev/null || echo "")"
  IMAGE_PROCESSING_COMPLETION_HANDLER_ARN="$(aws lambda get-function --function-name image_processing_completion_handler --region "${REGION}" --query 'Configuration.FunctionArn' --output text 2>/dev/null || echo "")"
  
  if [[ -z "${LIST_IMAGES_HANDLER_ARN}" ]] || [[ -z "${EXTRACT_BIB_NUMBER_HANDLER_ARN}" ]] || [[ -z "${IMAGE_PROCESSING_COMPLETION_HANDLER_ARN}" ]]; then
    echo >&2 "Error: Could not find all required Lambda functions. Please deploy Lambda functions first."
    exit 1
  fi
else
  # Called from main script with parameters
  REGION="${1:-ap-south-1}"
  SFN_ROLE_ARN="${2}"
  LIST_IMAGES_HANDLER_ARN="${3}"
  EXTRACT_BIB_NUMBER_HANDLER_ARN="${4}"
  IMAGE_PROCESSING_COMPLETION_HANDLER_ARN="${5}"
fi

# Strip whitespace and newlines from ARNs (they may contain trailing newlines from echo)
SFN_ROLE_ARN=$(printf '%s' "${SFN_ROLE_ARN}" | tr -d '\n\r\t ')
LIST_IMAGES_HANDLER_ARN=$(printf '%s' "${LIST_IMAGES_HANDLER_ARN}" | tr -d '\n\r\t ')
EXTRACT_BIB_NUMBER_HANDLER_ARN=$(printf '%s' "${EXTRACT_BIB_NUMBER_HANDLER_ARN}" | tr -d '\n\r\t ')
IMAGE_PROCESSING_COMPLETION_HANDLER_ARN=$(printf '%s' "${IMAGE_PROCESSING_COMPLETION_HANDLER_ARN}" | tr -d '\n\r\t ')

# Read definition file and substitute placeholders
# Use a temporary file approach to avoid sed newline issues
definition=$(cat "${DEFINITION_FILE}")
definition=$(echo "${definition}" | sed "s|\${LIST_IMAGES_HANDLER_ARN}|${LIST_IMAGES_HANDLER_ARN}|g")
definition=$(echo "${definition}" | sed "s|\${EXTRACT_BIB_NUMBER_HANDLER_ARN}|${EXTRACT_BIB_NUMBER_HANDLER_ARN}|g")
definition=$(echo "${definition}" | sed "s|\${IMAGE_PROCESSING_COMPLETION_HANDLER_ARN}|${IMAGE_PROCESSING_COMPLETION_HANDLER_ARN}|g")

# Check if state machine exists
arn="$(aws stepfunctions list-state-machines --region "${REGION}" --query "stateMachines[?name=='${STATE_MACHINE_NAME}'].stateMachineArn | [0]" --output text 2>/dev/null || echo "")"

if [[ "${arn}" != "None" && -n "${arn}" ]]; then
  echo >&2 "Updating state machine ${STATE_MACHINE_NAME}..."
  aws stepfunctions update-state-machine \
    --state-machine-arn "${arn}" \
    --definition "${definition}" \
    --role-arn "${SFN_ROLE_ARN}" \
    --region "${REGION}" >/dev/null
  echo "${arn}"
else
  echo >&2 "Creating state machine ${STATE_MACHINE_NAME}..."
  arn="$(aws stepfunctions create-state-machine \
    --name "${STATE_MACHINE_NAME}" \
    --definition "${definition}" \
    --role-arn "${SFN_ROLE_ARN}" \
    --region "${REGION}" \
    --query stateMachineArn \
    --output text)"
  echo "${arn}"
fi

