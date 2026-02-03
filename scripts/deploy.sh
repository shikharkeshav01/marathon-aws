#!/usr/bin/env bash
set -euo pipefail

# Combined deploy script that orchestrates individual Lambda deployments
# Usage: deploy.sh [process|reels|both]
# Requires: AWS CLI, zip, Python3.13, and optionally Docker for image-based Lambda2

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAMBDA_DIR="${ROOT_DIR}/lambdas"
STEP_FUNCTIONS_DIR="${ROOT_DIR}/step_functions"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-south-1}}"
RUNTIME="${LAMBDA_RUNTIME:-python3.13}"

usage() {
  cat <<'EOF'
Usage: deploy.sh [process|reels|both]
  process  Deploy only process-event-images pipeline
  reels    Deploy only generate-event-reels pipeline
  both     Deploy both pipelines (default)
EOF
}

action="${1:-both}"
case "${action}" in
  process) deploy_process=true; deploy_reels=false ;;
  reels) deploy_process=false; deploy_reels=true ;;
  both|"") deploy_process=true; deploy_reels=true ;;
  *) usage; exit 1 ;;
esac

# Helper functions
ensure_table() {
  local name="$1"
  local hash_name="$2"
  local hash_type="$3"
  local range_name="${4:-}"
  local range_type="${5:-}"
  local extra_attr_defs="${6:-}"
  local gsi_json="${7:-}"

  if aws dynamodb describe-table --table-name "${name}" --region "${REGION}" >/dev/null 2>&1; then
    echo "Table ${name} exists"
    return
  fi

  echo "Creating table ${name}"
  local attr_def_args=(--attribute-definitions "AttributeName=${hash_name},AttributeType=${hash_type}")
  if [[ -n "${range_name}" ]]; then
    attr_def_args+=(--attribute-definitions "AttributeName=${range_name},AttributeType=${range_type}")
  fi
  if [[ -n "${extra_attr_defs}" ]]; then
    # extra_attr_defs should be in format "AttributeName=Name,AttributeType=Type"
    attr_def_args+=(--attribute-definitions "${extra_attr_defs}")
  fi

  local args=(--table-name "${name}" --billing-mode PAY_PER_REQUEST "${attr_def_args[@]}" --key-schema "AttributeName=${hash_name},KeyType=HASH")
  if [[ -n "${range_name}" ]]; then
    args+=(--key-schema "AttributeName=${range_name},KeyType=RANGE")
  fi
  if [[ -n "${gsi_json}" ]]; then
    args+=(--global-secondary-indexes "${gsi_json}")
  fi
  aws dynamodb create-table "${args[@]}" --region "${REGION}" >/dev/null
}

ensure_role() {
  local name="$1"
  local assume_json="$2"
  if aws iam get-role --role-name "${name}" >/dev/null 2>&1; then
    echo "Role ${name} exists"
  else
    echo "Creating role ${name}..."
    aws iam create-role --role-name "${name}" --assume-role-policy-document "${assume_json}" >/dev/null
  fi
}

put_inline_policy() {
  local role="$1"
  local policy_name="$2"
  local policy_doc="$3"
  aws iam put-role-policy --role-name "${role}" --policy-name "${policy_name}" --policy-document "${policy_doc}" >/dev/null
}

ensure_log_group() {
  local log_group_name="$1"
  if aws logs describe-log-groups --log-group-name-prefix "${log_group_name}" --region "${REGION}" --query "logGroups[?logGroupName=='${log_group_name}'].logGroupName | [0]" --output text 2>/dev/null | grep -q "^${log_group_name}$"; then
    echo "Log group ${log_group_name} exists"
  else
    echo "Creating log group ${log_group_name}..."
    aws logs create-log-group --log-group-name "${log_group_name}" --region "${REGION}" >/dev/null 2>&1 || true
  fi
}


# Step 1: Create DynamoDB tables
echo "=== Setting up DynamoDB tables ==="
requests_table="EventRequests"
images_table="EventImages"
reels_table="EventReels"
participants_table="EventParticipants"

ensure_table "${requests_table}" "RequestId" "S"

ensure_table "${images_table}" "Id" "S" "" "" "AttributeName=EventId,AttributeType=N" \
  "[{\"IndexName\":\"EventId-index\",\"KeySchema\":[{\"AttributeName\":\"EventId\",\"KeyType\":\"HASH\"}],\"Projection\":{\"ProjectionType\":\"ALL\"}}]"

ensure_table "${reels_table}" "ReelId" "S" "" "" "AttributeName=EventId,AttributeType=N AttributeName=BibId,AttributeType=S" \
  "[{\"IndexName\":\"EventId-BibId-index\",\"KeySchema\":[{\"AttributeName\":\"EventId\",\"KeyType\":\"HASH\"},{\"AttributeName\":\"BibId\",\"KeyType\":\"RANGE\"}],\"Projection\":{\"ProjectionType\":\"ALL\"}}]"

ensure_table "${participants_table}" "EventId" "N" "BibId" "S" "AttributeName=Email,AttributeType=S" \
  "[{\"IndexName\":\"EventId-Email-index\",\"KeySchema\":[{\"AttributeName\":\"EventId\",\"KeyType\":\"HASH\"},{\"AttributeName\":\"Email\",\"KeyType\":\"RANGE\"}],\"Projection\":{\"ProjectionType\":\"ALL\"}}]"

requests_arn="$(aws dynamodb describe-table --table-name "${requests_table}" --region "${REGION}" --query "Table.TableArn" --output text)"
images_arn="$(aws dynamodb describe-table --table-name "${images_table}" --region "${REGION}" --query "Table.TableArn" --output text)"
reels_arn="$(aws dynamodb describe-table --table-name "${reels_table}" --region "${REGION}" --query "Table.TableArn" --output text)"
participants_arn="$(aws dynamodb describe-table --table-name "${participants_table}" --region "${REGION}" --query "Table.TableArn" --output text)"

# Step 2: Create IAM roles
echo ""
echo "=== Setting up IAM roles ==="
lambda_role_name="lambda-role"
sfn_role_name="sfn-role"

lambda_assume='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
sfn_assume='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"states.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

ensure_role "${lambda_role_name}" "${lambda_assume}"
ensure_role "${sfn_role_name}" "${sfn_assume}"

lambda_role_arn="$(aws iam get-role --role-name "${lambda_role_name}" --query 'Role.Arn' --output text)"
sfn_role_arn="$(aws iam get-role --role-name "${sfn_role_name}" --query 'Role.Arn' --output text)"

# Attach policies to roles
logs_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"}]}'
ddb_policy=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:Query","dynamodb:Scan"],"Resource":["${requests_arn}","${requests_arn}/index/*","${images_arn}","${images_arn}/index/*","${reels_arn}","${reels_arn}/index/*","${participants_arn}","${participants_arn}/index/*"]}]}
EOF
)
invoke_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["lambda:InvokeFunction"],"Resource":"*"}]}'
ssm_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ssm:GetParameter","ssm:GetParameters"],"Resource":"arn:aws:ssm:*:*:parameter/google-service-account"}]}'

put_inline_policy "${lambda_role_name}" "logs" "${logs_policy}"
put_inline_policy "${lambda_role_name}" "ddb" "${ddb_policy}"
put_inline_policy "${lambda_role_name}" "ssm" "${ssm_policy}"
put_inline_policy "${lambda_role_name}" "invoke" "${invoke_policy}"

s3_policy=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":"arn:aws:s3:::marathon-photos/*"},{"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::marathon-photos"}]}
EOF
)
put_inline_policy "${lambda_role_name}" "s3" "${s3_policy}"

# Prepare environment JSON for Lambdas
env_json=$(cat <<EOF
{"Variables":{"RAW_BUCKET":"marathon-photos","EVENT_REQUESTS_TABLE":"${requests_table}","EVENT_IMAGES_TABLE":"${images_table}","EVENT_REELS_TABLE":"${reels_table}","EVENT_PARTICIPANTS_TABLE":"${participants_table}","GDRIVE_SA_SSM_PARAM":"google-service-account"}}
EOF
)

# Step 3: Deploy Lambdas using individual deploy scripts
echo ""
echo "=== Deploying Lambda functions ==="

list_images_handler_arn=""
extract_bib_number_handler_arn=""
image_processing_completion_handler_arn=""
event_images_bib_extraction_handler_arn=""
reel_generation_handler_arn=""
reel_generation_completion_handler_arn=""

if "${deploy_process}"; then
  echo ""
  echo "Deploying process-event-images Lambdas..."
  list_images_handler_arn=$("${LAMBDA_DIR}/process_event_images/list_images_handler/deploy.sh" "${REGION}" "${RUNTIME}" "${lambda_role_arn}" "${env_json}")
  extract_bib_number_handler_arn=$("${LAMBDA_DIR}/process_event_images/extract_bib_number_handler/deploy.sh" "${REGION}" "${lambda_role_arn}" "${env_json}")
  image_processing_completion_handler_arn=$("${LAMBDA_DIR}/process_event_images/image_processing_completion_handler/deploy.sh" "${REGION}" "${RUNTIME}" "${lambda_role_arn}" "${env_json}")
fi

if "${deploy_reels}"; then
  echo ""
  echo "Deploying generate-event-reels Lambdas..."
  event_images_bib_extraction_handler_arn=$("${LAMBDA_DIR}/generate_event_reels/event_images_bib_extraction_handler/deploy.sh" "${REGION}" "${RUNTIME}" "${lambda_role_arn}" "${env_json}")
  reel_generation_handler_arn=$("${LAMBDA_DIR}/generate_event_reels/reel_generation_handler/deploy.sh" "${REGION}" "${lambda_role_arn}" "${env_json}")
  reel_generation_completion_handler_arn=$("${LAMBDA_DIR}/generate_event_reels/reel_generation_completion_handler/deploy.sh" "${REGION}" "${RUNTIME}" "${lambda_role_arn}" "${env_json}")
fi

# Step 3.5: Create CloudWatch log groups for Lambda functions
echo ""
echo "=== Creating CloudWatch log groups ==="
if "${deploy_process}"; then
  ensure_log_group "/aws/lambda/list_images_handler"
  ensure_log_group "/aws/lambda/extract_bib_number_handler"
  ensure_log_group "/aws/lambda/image_processing_completion_handler"
fi
if "${deploy_reels}"; then
  ensure_log_group "/aws/lambda/event_images_bib_extraction_handler"
  ensure_log_group "/aws/lambda/reel_generation_handler"
  ensure_log_group "/aws/lambda/reel_generation_completion_handler"
fi

# Step 4: Create Step Functions state machines
echo ""
echo "=== Creating Step Functions state machines ==="

process_sm_arn=""
reels_sm_arn=""

if "${deploy_process}"; then
  echo ""
  echo "Deploying process-images-state-machine..."
  process_sm_arn=$("${STEP_FUNCTIONS_DIR}/process_images_state_machine/deploy.sh" "${REGION}" "${sfn_role_arn}" "${list_images_handler_arn}" "${extract_bib_number_handler_arn}" "${image_processing_completion_handler_arn}")
fi

if "${deploy_reels}"; then
  echo ""
  echo "Deploying generate-reels-state-machine..."
  reels_sm_arn=$("${STEP_FUNCTIONS_DIR}/generate_reels_state_machine/deploy.sh" "${REGION}" "${sfn_role_arn}" "${event_images_bib_extraction_handler_arn}" "${reel_generation_handler_arn}" "${reel_generation_completion_handler_arn}")
fi

# Summary
echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Lambda ARNs:"
if "${deploy_process}"; then
  echo "  list_images_handler: ${list_images_handler_arn}"
  echo "  extract_bib_number_handler: ${extract_bib_number_handler_arn}"
  echo "  image_processing_completion_handler: ${image_processing_completion_handler_arn}"
fi
if "${deploy_reels}"; then
  echo "  event_images_bib_extraction_handler: ${event_images_bib_extraction_handler_arn}"
  echo "  reel_generation_handler: ${reel_generation_handler_arn}"
  echo "  reel_generation_completion_handler: ${reel_generation_completion_handler_arn}"
fi
