"""AWS API Gateway collector — REST + HTTP APIs."""
from .base import BaseCollector
from ...models import Resource

class APIGatewayCollector(BaseCollector):
    resource_type = "aws.apigateway"
    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('apigateway', region_name=self.region)
            apis = client.get_rest_apis().get('items', [])
            resources = [self._make_resource(
                resource_id=api['id'],
                tags=api.get('tags', {}),
                properties={'name': api.get('name'), 'type': 'REST'},
            ) for api in apis]
            # Also check HTTP APIs (apigatewayv2)
            try:
                v2 = self.session.client('apigatewayv2', region_name=self.region)
                http_apis = v2.get_apis().get('Items', [])
                for api in http_apis:
                    resources.append(self._make_resource(
                        resource_id=api['ApiId'],
                        tags=api.get('Tags', {}),
                        properties={'name': api.get('Name'), 'type': 'HTTP', 'protocol': api.get('ProtocolType')},
                    ))
            except:
                pass
            return resources
        except Exception as e:
            print(f"  ⚠️  API Gateway scan failed in {self.region}: {e}")
            return []
