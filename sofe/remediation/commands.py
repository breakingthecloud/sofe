"""
SOFE Remediation Commands — Maps policy findings to actionable AWS CLI commands.

Each policy has one or more remediation options with:
- label: human-readable action name
- command: AWS CLI command (interpolated with resource details)
- risk: low | medium | high
- note: safety/context information

Usage:
    from sofe.remediation.commands import get_remediation_commands
    cmds = get_remediation_commands("no-idle-ec2", "i-0abc123", "aws.ec2", "us-east-1")
"""

from __future__ import annotations


REMEDIATION_MAP: dict[str, list[dict[str, str]]] = {
    "no-idle-ec2": [
        {
            "label": "Stop instance (reversible)",
            "command": "aws ec2 stop-instances --instance-ids {resource_id} --region {region}",
            "risk": "low",
            "note": "Instance can be restarted later. EBS data preserved.",
        },
        {
            "label": "Terminate instance (permanent)",
            "command": "aws ec2 terminate-instances --instance-ids {resource_id} --region {region}",
            "risk": "high",
            "note": "Permanently deletes instance. EBS volumes may be deleted depending on DeleteOnTermination setting.",
        },
    ],
    "no-idle-rds": [
        {
            "label": "Stop RDS instance (7-day max)",
            "command": "aws rds stop-db-instance --db-instance-identifier {resource_id} --region {region}",
            "risk": "low",
            "note": "Auto-restarts after 7 days. Use for temporary cost savings.",
        },
        {
            "label": "Take final snapshot + delete",
            "command": "aws rds delete-db-instance --db-instance-identifier {resource_id} --final-db-snapshot-identifier {resource_id}-final --region {region}",
            "risk": "high",
            "note": "Deletes instance permanently. Final snapshot preserved for restore if needed.",
        },
    ],
    "no-unattached-ebs": [
        {
            "label": "Create snapshot + delete volume",
            "command": "aws ec2 create-snapshot --volume-id {resource_id} --description 'SOFE backup before cleanup' --region {region} && aws ec2 delete-volume --volume-id {resource_id} --region {region}",
            "risk": "medium",
            "note": "Snapshot preserves data. Volume deleted to stop charges ($0.08/GB/mo saved).",
        },
        {
            "label": "Delete volume (no backup)",
            "command": "aws ec2 delete-volume --volume-id {resource_id} --region {region}",
            "risk": "high",
            "note": "Permanent data loss. Only use if volume content is confirmed unnecessary.",
        },
    ],
    "no-old-snapshots": [
        {
            "label": "Delete old snapshot",
            "command": "aws ec2 delete-snapshot --snapshot-id {resource_id} --region {region}",
            "risk": "low",
            "note": "Snapshots older than 90 days are rarely needed for recovery. Verify before deleting.",
        },
    ],
    "require-cost-tags": [
        {
            "label": "Add owner tag",
            "command": "aws resourcegroupstaggingapi tag-resources --resource-arn-list {resource_id} --tags owner=TEAM_NAME --region {region}",
            "risk": "low",
            "note": "Replace TEAM_NAME with the responsible team. Non-destructive operation.",
        },
        {
            "label": "Add all required cost tags",
            "command": "aws resourcegroupstaggingapi tag-resources --resource-arn-list {resource_id} --tags owner=TEAM_NAME,env=ENVIRONMENT,costcenter=CC_CODE --region {region}",
            "risk": "low",
            "note": "Replace placeholders with actual values. Non-destructive.",
        },
    ],
    "s3-require-environment-tag": [
        {
            "label": "Add environment tag to bucket",
            "command": "aws s3api put-bucket-tagging --bucket {resource_id} --tagging 'TagSet=[{{Key=env,Value=ENVIRONMENT}}]' --region {region}",
            "risk": "medium",
            "note": "WARNING: put-bucket-tagging REPLACES all existing tags. Use get-bucket-tagging first to preserve existing ones.",
        },
    ],
    "s3-encryption-required": [
        {
            "label": "Enable default encryption (AES-256)",
            "command": "aws s3api put-bucket-encryption --bucket {resource_id} --server-side-encryption-configuration '{{\"Rules\":[{{\"ApplyServerSideEncryptionByDefault\":{{\"SSEAlgorithm\":\"AES256\"}}}}]}}' --region {region}",
            "risk": "low",
            "note": "Enables encryption for NEW objects only. Existing objects are NOT retroactively encrypted.",
        },
    ],
    "s3-lifecycle-required": [
        {
            "label": "Add 90-day transition to Glacier",
            "command": "aws s3api put-bucket-lifecycle-configuration --bucket {resource_id} --lifecycle-configuration '{{\"Rules\":[{{\"ID\":\"sofe-glacier-90d\",\"Status\":\"Enabled\",\"Transitions\":[{{\"Days\":90,\"StorageClass\":\"GLACIER\"}}],\"Filter\":{{}}}}]}}' --region {region}",
            "risk": "medium",
            "note": "Objects older than 90 days move to Glacier. Retrieval costs apply ($0.01/GB). Review access patterns first.",
        },
    ],
    "idle-load-balancer": [
        {
            "label": "Delete idle load balancer",
            "command": "aws elbv2 delete-load-balancer --load-balancer-arn {resource_id} --region {region}",
            "risk": "high",
            "note": "Verify no DNS records (Route53, external) point to this LB. Check target groups are empty.",
        },
    ],
    "nat-gateway-high-cost": [
        {
            "label": "Delete NAT Gateway",
            "command": "aws ec2 delete-nat-gateway --nat-gateway-id {resource_id} --region {region}",
            "risk": "high",
            "note": "Private subnet resources will LOSE internet access. Ensure VPC endpoints or alternatives exist first.",
        },
    ],
    "no-oversized-staging": [
        {
            "label": "Rightsize to t3.medium (stop→modify→start)",
            "command": "aws ec2 stop-instances --instance-ids {resource_id} --region {region} && sleep 60 && aws ec2 modify-instance-attribute --instance-id {resource_id} --instance-type '{{\"Value\":\"t3.medium\"}}' --region {region} && aws ec2 start-instances --instance-ids {resource_id} --region {region}",
            "risk": "medium",
            "note": "Requires brief downtime (stop→modify→start, ~2 min). Adjust target instance type as needed.",
        },
    ],
    "budget-exceeded": [
        {
            "label": "Create AWS Budget alert",
            "command": "aws budgets create-budget --account-id {account_id} --budget '{{\"BudgetName\":\"sofe-monthly-alert\",\"BudgetLimit\":{{\"Amount\":\"1000\",\"Unit\":\"USD\"}},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\"}}' --notifications-with-subscribers '[{{\"Notification\":{{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":80}},\"Subscribers\":[{{\"SubscriptionType\":\"EMAIL\",\"Address\":\"YOUR_EMAIL\"}}]}}]' --region us-east-1",
            "risk": "low",
            "note": "Creates a budget alert at 80% threshold. Adjust amount and email. Does NOT stop spend.",
        },
    ],
    "secrets-not-rotated": [
        {
            "label": "Enable automatic rotation (30 days)",
            "command": "aws secretsmanager rotate-secret --secret-id {resource_id} --rotation-rules '{{\"AutomaticallyAfterDays\":30}}' --region {region}",
            "risk": "medium",
            "note": "Requires a Lambda rotation function configured. May break apps using the secret if not prepared.",
        },
    ],
}


def get_remediation_commands(
    policy_name: str,
    resource_id: str,
    resource_type: str = "",
    region: str = "us-east-1",
    account_id: str = "",
) -> list[dict[str, str]]:
    """Get remediation commands for a finding, interpolated with resource details."""
    templates = REMEDIATION_MAP.get(policy_name, [])
    if not templates:
        return []

    commands = []
    for t in templates:
        commands.append({
            "label": t["label"],
            "command": t["command"].format(
                resource_id=resource_id,
                region=region,
                account_id=account_id,
            ),
            "risk": t["risk"],
            "note": t.get("note", ""),
        })
    return commands
