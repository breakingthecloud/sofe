"""AWS DynamoDB collector — tables."""
from .base import BaseCollector
from ...models import Resource

class DynamoDBCollector(BaseCollector):
    resource_type = "aws.dynamodb"
    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('dynamodb', region_name=self.region)
            tables = client.list_tables().get('TableNames', [])
            resources = []
            for name in tables:
                desc = client.describe_table(TableName=name).get('Table', {})
                billing = desc.get('BillingModeSummary', {}).get('BillingMode', 'PROVISIONED')
                resources.append(self._make_resource(
                    resource_id=name,
                    tags={t['Key']: t['Value'] for t in desc.get('Tags', []) if 'Key' in t},
                    properties={'billing_mode': billing, 'item_count': desc.get('ItemCount', 0), 'size_bytes': desc.get('TableSizeBytes', 0)},
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  DynamoDB scan failed in {self.region}: {e}")
            return []
