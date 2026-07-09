"""AWS API Gateway collector — REST APIs with auth, logging, throttle metrics."""

from .base import BaseCollector
from ...models import Resource


class APIGatewayCollector(BaseCollector):
    resource_type = "aws.apigateway"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('apigateway', region_name=self.region)
            apis = client.get_rest_apis().get('items', [])
            resources = []
            for api in apis:
                api_id = api['id']
                name = api.get('name', api_id)
                endpoint_type = ','.join(api.get('endpointConfiguration', {}).get('types', ['REGIONAL']))

                # Check stages for throttle + logging
                throttle_configured = False
                logging_enabled = False
                try:
                    stages = client.get_stages(restApiId=api_id).get('item', [])
                    for stage in stages:
                        settings = stage.get('methodSettings', {}).get('*/*', {})
                        if settings.get('throttlingRateLimit', 0) > 0:
                            throttle_configured = True
                        if stage.get('accessLogSettings'):
                            logging_enabled = True
                except:
                    pass

                # Tags
                tags = api.get('tags', {})

                resources.append(self._make_resource(
                    resource_id=name,
                    tags=tags,
                    properties={
                        'api_id': api_id,
                        'endpoint_type': endpoint_type,
                        'description': api.get('description', ''),
                    },
                    metrics={
                        'throttle_configured': 1.0 if throttle_configured else 0.0,
                        'logging_enabled': 1.0 if logging_enabled else 0.0,
                        'endpoint_type_edge': 1.0 if 'EDGE' in endpoint_type else 0.0,
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  API Gateway scan failed in {self.region}: {e}")
            return []
