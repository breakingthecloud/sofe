"""AWS DynamoDB collector — tables with billing mode, size, item count."""

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
                item_count = desc.get('ItemCount', 0)
                table_size = desc.get('TableSizeBytes', 0)

                # Tags
                tags = {}
                try:
                    tag_resp = client.list_tags_of_resource(ResourceArn=desc['TableArn'])
                    tags = {t['Key']: t['Value'] for t in tag_resp.get('Tags', [])}
                except:
                    pass

                resources.append(self._make_resource(
                    resource_id=name,
                    tags=tags,
                    properties={
                        'billing_mode': billing,
                        'item_count': item_count,
                        'table_size_bytes': table_size,
                        'status': desc.get('TableStatus'),
                    },
                    metrics={
                        'billing_mode_provisioned': 1.0 if billing == 'PROVISIONED' else 0.0,
                        'item_count': float(item_count),
                        'table_size_mb': round(table_size / 1_048_576, 2),
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  DynamoDB scan failed in {self.region}: {e}")
            return []
